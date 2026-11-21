"""Instagram in the worker (sim/channels/instagram/{account,messenger,poller}.py) with a fake
instagrapi client and a fake channel store: the client restored from the stored session (never a
new login), sends and their caps, soft blocks and checkpoints, polls with a cursor, fail closed."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sim.channels.delivery import RetryAfter
from sim.channels.instagram.account import RELOGIN, SOFT_BLOCK_S, InstagramAccounts, SendLimiter
from sim.channels.instagram.messenger import InstagramMessenger
from sim.channels.instagram.poller import InstagramPoller, PollSchedule
from sim.clock import FixedClock
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.ports import OutboundMessage, Peer, PortError
from tests.channels.ig_fixtures import INBOX, NOW_US, PENDING
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


async def test_what_the_private_api_can_not_send_is_refused_for_good() -> None:
    accounts, _, _, _ = runtime()
    messenger = InstagramMessenger(accounts)
    with pytest.raises(PortError) as refused:
        await messenger.send(7, OutboundMessage(peer=PEER, kind="voice", data=b"OGG"))
    assert not refused.value.retryable
    with pytest.raises(PortError, match="no Instagram thread"):
        await messenger.send(7, OutboundMessage(peer=Peer(external_id="1"), kind="text",
                                                text="x"))


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
    await messenger.mark_read(7, PEER, "item-1")
    await messenger.set_typing(7, PEER, "typing")
    assert factory.last.seen == [340282366841710300949128]
    assert factory.last.sent == []


def poll_factory() -> FakeIgFactory:
    def prepare(client: FakeIgClient) -> None:
        client.threads = {"direct_v2/inbox/": INBOX, "direct_v2/pending_inbox/": PENDING}

    return FakeIgFactory(prepare)


async def test_a_poll_hands_new_client_messages_to_the_inbox_once() -> None:
    accounts, store, _, _ = runtime(poll_factory())
    inbox = FakeInbox()
    downloads: list[str] = []

    async def download(url: str) -> bytes:
        downloads.append(url)
        return b"DATA"

    poller = InstagramPoller(accounts, inbox, FixedClock(NOW), download=download)
    assert await poller.poll(7) == 6
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
    assert await poller.poll(7) == 0                        # the cursor remembers
    assert store.statuses[7] == ChannelStatus.CONNECTED


async def test_a_poll_without_our_own_id_does_nothing() -> None:
    accounts, store, _, _ = runtime(poll_factory())
    store.accounts[7]["settings"]["authorization_data"] = {}
    inbox = FakeInbox()
    assert await InstagramPoller(accounts, inbox, FixedClock(NOW)).poll(7) == 0
    assert inbox.ingested == []


async def test_a_poll_of_a_blocked_account_is_skipped() -> None:
    def prepare(client: FakeIgClient) -> None:
        client.poll_error = ChallengeRequired("challenge_required")

    accounts, store, _, _ = runtime(FakeIgFactory(prepare))
    assert await InstagramPoller(accounts, FakeInbox(), FixedClock(NOW)).poll(7) == 0
    assert store.statuses[7] == ChannelStatus.CHALLENGE
    disabled, _, _, _ = runtime(status=ChannelStatus.DISABLED)
    assert await InstagramPoller(disabled, FakeInbox(), FixedClock(NOW)).poll(7) == 0


def test_the_poll_schedule_spreads_accounts() -> None:
    schedule = PollSchedule(120.0, uniform=lambda: 0.5)
    assert schedule.due([1, 2], NOW) == [(1, 3.0), (2, 3.0)]
    assert schedule.due([1, 2], NOW + timedelta(seconds=60)) == []
    assert schedule.due([1], NOW + timedelta(seconds=133)) == [(1, 3.0)]
    assert set(schedule.next_at) == {1}                     # account 2 is gone
