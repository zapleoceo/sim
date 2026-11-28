"""Instagram in the worker (sim/channels/instagram/{account,messenger,poller}.py) with a fake
instagrapi client and a fake channel store: the client restored from the stored session (never a
new login), sends and their caps, soft blocks and checkpoints, polls with a cursor, fail closed,
message requests read only while some wait, the pace of the poll chain and its watchdog."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sim.channels.delivery import RetryAfter
from sim.channels.instagram.account import RELOGIN, SOFT_BLOCK_S, InstagramAccounts, SendLimiter
from sim.channels.instagram.messenger import InstagramMessenger
from sim.channels.instagram.poller import InstagramPoller, Polled, PollPace, PollSchedule
from sim.clock import FixedClock
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.ports import OutboundMessage, Peer, PortError
from tests.channels.ig_fixtures import INBOX, NOW_US, PENDING
from tests.fakes.ai import FakeMediaTools
from tests.fakes.channels import (
    ChallengeRequired,
    FakeIgClient,
    FakeIgFactory,
    FakeStore,
    FeedbackRequired,
    ig_credentials,
)
from tests.fakes.telegram import FakeInbox

NOW = datetime.fromtimestamp(NOW_US / 1_000_000, tz=UTC)
PEER = Peer(external_id="1001", thread_id="340282366841710300949128")


class Mono:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def runtime(factory: FakeIgFactory | None = None, *, hourly: int = 150, daily: int = 800,
            status: ChannelStatus = ChannelStatus.CONNECTED,
            ) -> tuple[InstagramAccounts, FakeStore, FakeIgFactory, Mono]:
    store, mono = FakeStore(), Mono()
    store.add(7, ChannelKind.INSTAGRAM, ig_credentials(), status)
    factory = factory or FakeIgFactory()
    accounts = InstagramAccounts(store, FixedClock(NOW), hourly_cap=hourly, daily_cap=daily,
                                 factory=factory, monotonic=mono)
    return accounts, store, factory, mono


def text(body: str = "привет") -> OutboundMessage:
    return OutboundMessage(peer=PEER, kind="text", text=body, key=1)


async def test_the_client_is_restored_from_the_stored_session() -> None:
    accounts, _, factory, _ = runtime()
    account = await accounts.get(7)
    assert account.own_id == "4242"
    client = factory.last
    assert client.settings["uuids"] == {"phone_id": "p"} and client.login_calls == []
    assert (client.proxy, client.lang, client.tz_offset_s) == (
        "socks5://u:p@10.0.0.1:1080", "ru", 10800)
    assert await accounts.get(7) is account                  # cached until a new login


async def test_send_text_and_photo_through_the_thread() -> None:
    accounts, store, factory, _ = runtime()
    messenger = InstagramMessenger(accounts)
    assert await messenger.send(7, text()) == "101"
    photo = OutboundMessage(peer=PEER, kind="photo", text="это я", data=b"JPEG",
                            mime="image/jpeg")
    assert await messenger.send(7, photo) == "102"
    client = factory.last
    assert client.sent == [("str", "привет", [340282366841710300949128]),
                           ("bytes", b"JPEG", [340282366841710300949128]),
                           ("str", "это я", [340282366841710300949128])]
    assert store.saved == []                                # the session did not change
    # Cookies the client refreshed are stored back (the real store encrypts them).
    client.settings["cookies"] = {"sessionid": "fresh"}
    await messenger.send(7, text("ещё"))
    assert store.saved[-1][1]["settings"]["cookies"] == {"sessionid": "fresh"}
    assert await accounts.get(7) is await accounts.get(7)   # the same client goes on


async def test_voice_goes_as_m4a_and_video_as_mp4() -> None:
    accounts, _, factory, _ = runtime()
    tools = FakeMediaTools()
    messenger = InstagramMessenger(accounts, tools)
    voice = OutboundMessage(peer=PEER, kind="voice", text="(что сказала)", data=b"OGG",
                            mime="audio/ogg")
    assert await messenger.send(7, voice) == "101"
    video = OutboundMessage(peer=PEER, kind="video", text="смотри", data=b"MP4",
                            mime="video/mp4")
    assert await messenger.send(7, video) == "102"
    client = factory.last
    assert tools.calls == [("to_m4a", "audio/ogg")]       # her OGG/Opus -> AAC .m4a
    assert client.suffixes == [".m4a", ".mp4"]
    # A voice note has no caption; a video's caption follows as a text.
    assert client.sent == [("bytes", b"M4A:OGG", [340282366841710300949128]),
                           ("bytes", b"MP4", [340282366841710300949128]),
                           ("str", "смотри", [340282366841710300949128])]


async def test_what_the_private_api_can_not_send_is_refused_for_good() -> None:
    accounts, _, factory, _ = runtime()
    messenger = InstagramMessenger(accounts, FakeMediaTools())
    for kind in ("video_note", "file", "sticker"):
        with pytest.raises(PortError) as refused:
            await messenger.send(7, OutboundMessage(peer=PEER, kind=kind, data=b"X"))
        assert not refused.value.retryable
    with pytest.raises(PortError, match="mp4"):
        await messenger.send(7, OutboundMessage(peer=PEER, kind="video", data=b"WEBM",
                                                mime="video/webm"))
    with pytest.raises(PortError, match="no media tools"):
        await InstagramMessenger(accounts).send(7, OutboundMessage(
            peer=PEER, kind="voice", data=b"OGG", mime="audio/ogg"))
    with pytest.raises(PortError, match="no Instagram thread"):
        await messenger.send(7, OutboundMessage(peer=Peer(external_id="1"), kind="text",
                                                text="x"))
    assert factory.made == []                   # refused before the account was even touched


async def test_send_caps_wait_for_a_free_slot() -> None:
    accounts, _, _, mono = runtime(hourly=2, daily=3)
    messenger = InstagramMessenger(accounts)
    await messenger.send(7, text())
    mono.now = 10
    await messenger.send(7, text())
    with pytest.raises(RetryAfter) as capped:
        await messenger.send(7, text())
    assert capped.value.seconds == pytest.approx(3600 - 10)
    limiter = SendLimiter(5, 2, lambda: 0.0)
    limiter.record()
    limiter.record()
    assert limiter.wait_s() == 86400.0                      # the daily cap


async def test_a_soft_block_waits_15_minutes() -> None:
    def prepare(client: FakeIgClient) -> None:
        client.send_error = FeedbackRequired("feedback_required")

    accounts, store, _, _ = runtime(FakeIgFactory(prepare))
    with pytest.raises(RetryAfter) as blocked:
        await InstagramMessenger(accounts).send(7, text())
    assert blocked.value.seconds == SOFT_BLOCK_S
    assert store.changes == []                              # the account is fine


async def test_a_checkpoint_asks_the_owner_to_log_in_again() -> None:
    def prepare(client: FakeIgClient) -> None:
        client.send_error = ChallengeRequired("challenge_required")

    accounts, store, _, _ = runtime(FakeIgFactory(prepare))
    with pytest.raises(RetryAfter):
        await InstagramMessenger(accounts).send(7, text())
    assert store.changes == [(7, ChannelStatus.CHALLENGE, RELOGIN)]
    with pytest.raises(RetryAfter, match="войти заново"):
        await accounts.get(7)                               # no request until a new login


async def test_mark_read_is_seen_and_typing_does_nothing() -> None:
    accounts, _, factory, _ = runtime()
    messenger = InstagramMessenger(accounts)
    # A real item id: «seen» on that item, one request, no thread read through instagrapi's
    # extractor (it fails on shared reels).
    await messenger.mark_read(7, PEER, "32661234567890123456789012345")
    assert factory.last.seen_items == [(340282366841710300949128,
                                        32661234567890123456789012345)]
    assert factory.last.seen == []
    await messenger.mark_read(7, PEER, "item-1")            # not an Instagram id: the thread
    await messenger.set_typing(7, PEER, "typing")
    assert factory.last.seen == [340282366841710300949128]
    assert factory.last.sent == []


def poll_factory() -> FakeIgFactory:
    def prepare(client: FakeIgClient) -> None:
        client.threads = {"direct_v2/inbox/": INBOX, "direct_v2/pending_inbox/": PENDING}

    return FakeIgFactory(prepare)


async def test_a_poll_hands_new_client_messages_to_the_inbox_once() -> None:
    accounts, store, factory, _ = runtime(poll_factory())
    inbox = FakeInbox()
    downloads: list[str] = []

    async def download(url: str) -> bytes:
        downloads.append(url)
        return b"DATA"

    poller = InstagramPoller(accounts, inbox, FixedClock(NOW), download=download)
    polled = await poller.poll(7)
    assert (polled.chain, polled.count, polled.backoff_s) == (True, 6, 0.0)
    assert polled.latest == NOW - timedelta(minutes=1)     # the voice, the newest item
    ids = [m.external_id for m in inbox.ingested]
    # Not our own item, not the 2-day-old one on the first poll after a start.
    assert ids == ["i-new", "i-story", "i-reel", "i-photo", "i-raven", "i-voice"]
    photo = next(m for m in inbox.ingested if m.external_id == "i-photo")
    assert photo.attachment is not None and photo.attachment.kind == "photo"
    assert (photo.attachment.data, photo.attachment.mime, photo.text) == (b"DATA", "image/jpeg",
                                                                          "")
    assert photo.peer == Peer(external_id="1001", name="Дима Петров", username="dima",
                              thread_id="t-1")
    voice = next(m for m in inbox.ingested if m.external_id == "i-voice")
    assert voice.attachment is not None and voice.attachment.kind == "voice"
    assert len(downloads) == 3
    assert (await poller.poll(7)).count == 0               # the cursor remembers
    assert store.statuses[7] == ChannelStatus.CONNECTED
    # Instagram did not say how many requests wait: both boxes are read.
    assert factory.last.requested[:2] == ["direct_v2/inbox/", "direct_v2/pending_inbox/"]


async def test_message_requests_are_read_only_while_some_wait() -> None:
    accounts, store, factory, _ = runtime(poll_factory())
    store.timezone = "Asia/Tokyo"
    inbox = FakeInbox()
    poller = InstagramPoller(accounts, inbox, FixedClock(NOW), download=fake_download)
    await accounts.get(7)                                  # the client the polls will use
    factory.last.pending_total = 0
    found = await poller.poll(7)
    assert factory.last.requested == ["direct_v2/inbox/"]   # one request, not two
    assert "i-new" not in [m.external_id for m in inbox.ingested]
    assert found.timezone == "Asia/Tokyo"                   # the persona's, for her night
    factory.last.pending_total = 1                          # a stranger wrote
    await poller.poll(7)
    assert factory.last.requested[1:] == ["direct_v2/inbox/", "direct_v2/pending_inbox/"]
    assert "i-new" in [m.external_id for m in inbox.ingested]


async def fake_download(url: str) -> bytes:
    return b"DATA"


async def test_a_poll_without_our_own_id_does_nothing() -> None:
    accounts, store, _, _ = runtime(poll_factory())
    store.accounts[7]["settings"]["authorization_data"] = {}
    inbox = FakeInbox()
    assert await InstagramPoller(accounts, inbox, FixedClock(NOW)).poll(7) == Polled(chain=False)
    assert inbox.ingested == []


async def test_a_soft_block_stretches_the_next_poll_to_15_minutes() -> None:
    def prepare(client: FakeIgClient) -> None:
        client.poll_error = FeedbackRequired("feedback_required")

    accounts, store, _, _ = runtime(FakeIgFactory(prepare))
    polled = await InstagramPoller(accounts, FakeInbox(), FixedClock(NOW)).poll(7)
    assert (polled.chain, polled.count, polled.backoff_s) == (True, 0, SOFT_BLOCK_S)
    assert store.statuses[7] == ChannelStatus.CONNECTED


async def test_a_poll_of_a_blocked_account_is_skipped() -> None:
    def prepare(client: FakeIgClient) -> None:
        client.poll_error = ChallengeRequired("challenge_required")

    accounts, store, _, _ = runtime(FakeIgFactory(prepare))
    poller = InstagramPoller(accounts, FakeInbox(), FixedClock(NOW))
    assert (await poller.poll(7)).backoff_s == SOFT_BLOCK_S
    assert store.statuses[7] == ChannelStatus.CHALLENGE
    assert await poller.poll(7) == Polled(chain=False)      # then no request, the chain stops
    disabled, _, _, _ = runtime(status=ChannelStatus.DISABLED)
    assert await InstagramPoller(disabled, FakeInbox(), FixedClock(NOW)).poll(7) == Polled(
        chain=False)


def polled(minutes_ago: float | None, *, timezone: str = "UTC",
           backoff_s: float = 0.0) -> Polled:
    latest = None if minutes_ago is None else NOW - timedelta(minutes=minutes_ago)
    return Polled(chain=True, latest=latest, timezone=timezone, backoff_s=backoff_s)


@pytest.mark.parametrize(("found", "delay_s"), [
    (polled(2), 50.6),                                     # a conversation: 45 s + 12.5 %
    (polled(30), 135.0),                                   # 120 s
    (polled(120), 337.5),                                  # quiet for an hour: 300 s
    (polled(None), 337.5),                                 # nothing in Direct at all
    (polled(2, timezone="Asia/Tokyo"), 337.5),             # 03:33 at her place: night
    (polled(2, backoff_s=SOFT_BLOCK_S), 1012.5),           # «please wait»
])
def test_the_pace_follows_the_conversation(found: Polled, delay_s: float) -> None:
    schedule = PollSchedule(PollPace(), uniform=lambda: 0.5)
    assert schedule.after_poll(1, NOW, found) == delay_s
    assert schedule.next_at[1] == NOW + timedelta(seconds=delay_s)


def test_the_newest_item_seen_is_kept() -> None:
    schedule = PollSchedule(PollPace(), uniform=lambda: 0.0)
    assert schedule.after_poll(1, NOW, polled(2)) == 45.0
    # A later poll that saw only older items (a box came back short) keeps the conversation.
    assert schedule.after_poll(1, NOW, polled(50)) == 45.0
    assert schedule.after_poll(1, NOW + timedelta(minutes=20), polled(None)) == 120.0


def test_the_watchdog_starts_only_missing_or_late_chains() -> None:
    schedule = PollSchedule(PollPace(), uniform=lambda: 0.5)
    assert schedule.due([1, 2], NOW) == [(1, 3.8), (2, 3.8)]
    assert schedule.due([1, 2], NOW + timedelta(seconds=60)) == []
    schedule.after_poll(1, NOW + timedelta(seconds=60), polled(1))     # its chain goes on
    assert schedule.due([1, 2], NOW + timedelta(seconds=200)) == []    # 2: not late yet
    schedule.after_poll(1, NOW + timedelta(seconds=180), polled(1))
    assert schedule.due([1, 2], NOW + timedelta(seconds=245)) == [(2, 3.8)]
    assert schedule.due([1], NOW + timedelta(seconds=400)) == [(1, 3.8)]  # 1's chain is lost
    assert set(schedule.next_at) == {1}                     # account 2 is gone
