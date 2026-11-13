# ruff: noqa: S105, S106
"""Telegram userbot connector against a fake Telethon client (no network)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon import errors as tge

from sim.adapters.channels.capabilities import Capability
from sim.adapters.channels.telegram_user import (
    MEDIA_PREFIX,
    TelegramUserAdapter,
    TelethonTransport,
    TGUserState,
)
from sim.connectors.telegram_user import TelegramUserConnector
from sim.domain.enums import SessionStatus

CREDS = {"api_id": "12345", "api_hash": "hash-secret", "phone": "+380 50 123-45-67"}
PHONE = "+380501234567"


def flood(seconds=42):
    return tge.FloodWaitError(request=None, capture=seconds)


class FakeSession:
    def __init__(self, value=""):
        self.value = value

    def save(self):
        return self.value or "SESSION-NEW"


def user(uid, **kw):
    return SimpleNamespace(id=uid, bot=False, deleted=False, is_self=False, first_name="Anna",
                           last_name="K", username="anna", phone="380671112233", **kw)


def dialog(entity, top_id, unread=0, is_user=True):
    return SimpleNamespace(entity=entity, is_user=is_user, unread_count=unread,
                           message=SimpleNamespace(id=top_id) if top_id else None)


def msg(mid, text="hi", out=False, date=None, **media):
    return SimpleNamespace(id=mid, message=text, out=out, date=date or datetime.now(UTC),
                           voice=media.get("voice"), audio=media.get("audio"),
                           photo=media.get("photo"), video=None, video_note=None)


class FakeClient:
    def __init__(self, string_session="", api_id=0, api_hash="", proxy="", script=None):
        self.session = FakeSession(string_session)
        self.api_id, self.api_hash, self.proxy = api_id, api_hash, proxy
        self.script = script if script is not None else {}
        self.connected = False
        self.calls = []
        self.dialogs = []
        self.history = {}           # peer id -> list of messages (oldest first)
        self.sent = []
        self.me = SimpleNamespace(username="stepan", phone="380501234567", first_name="S")
        self.media = b"bytes"
        self.unknown_entities = False
        self.logged_out = False

    def fail(self, name):
        exc = self.script.get(name)
        if isinstance(exc, list):
            exc = exc.pop(0) if exc else None
        if exc is not None:
            raise exc

    async def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False

    async def log_out(self):
        self.logged_out = True

    async def send_code_request(self, phone):
        self.calls.append(("send_code_request", phone))
        self.fail("send_code_request")
        return SimpleNamespace(phone_code_hash="HASH1")

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        self.calls.append(("sign_in", phone, code, password, phone_code_hash))
        self.fail("sign_in_password" if password is not None else "sign_in")

    async def get_me(self):
        return self.me

    async def is_user_authorized(self):
        self.fail("authorized")
        return self.script.get("is_authorized", True)

    async def iter_dialogs(self, limit=None):
        self.fail("dialogs")
        for d in self.dialogs:
            yield d

    async def get_dialogs(self, limit=None):
        self.unknown_entities = False

    async def get_input_entity(self, peer):
        if self.unknown_entities:
            raise ValueError("Could not find the input entity")
        return ("peer", peer)

    async def iter_messages(self, entity, limit=None, min_id=0, reverse=False):
        self.calls.append(("iter_messages", entity[1], min_id, limit, reverse))
        items = [m for m in self.history.get(entity[1], []) if m.id > min_id]
        items = items if reverse else list(reversed(items))
        for m in items[:limit]:
            yield m

    async def get_messages(self, entity, ids=None):
        return next((m for m in self.history.get(entity[1], []) if m.id == ids), None)

    async def download_media(self, message, file=None):
        return self.media

    async def send_message(self, entity, text):
        self.fail("send")
        self.sent.append((entity[1], text))
        return SimpleNamespace(id=900 + len(self.sent))


@pytest.fixture
def made():
    return []


@pytest.fixture
def conn(made):
    script = {}

    def factory(string_session="", api_id=0, api_hash="", proxy=""):
        c = FakeClient(string_session, api_id, api_hash, proxy, script)
        made.append(c)
        return c

    c = TelegramUserConnector(client_factory=factory)
    c.script = script
    return c


ACTIVE = {"api_id": 12345, "api_hash": "hash-secret", "string_session": "SESS",
          "account_label": "@stepan"}


# --- login ---------------------------------------------------------------------------------

async def test_connect_requests_code_and_parks_pending(conn, made):
    res = await conn.connect(5, {**CREDS, "proxy": "socks5://u:p@10.0.0.1:1080"}, {})
    assert res.status is SessionStatus.CHALLENGE and res.need == "code"
    assert made[0].calls == [("send_code_request", PHONE)]
    p = res.session["pending"]
    assert p["phone_code_hash"] == "HASH1" and p["phone"] == PHONE and p["step"] == "code"
    assert p["api_id"] == 12345 and p["proxy"] == "socks5://u:p@10.0.0.1:1080"
    assert "string_session" in p


async def test_connect_says_where_telegram_sent_the_code(conn, made, monkeypatch):
    from telethon.tl.types.auth import SentCodeTypeApp, SentCodeTypeSetUpEmailRequired

    from sim.connectors.telegram_user import CODE_WHERE, CODE_WHERE_UNKNOWN

    async def sent_to_app(self, phone):
        return SimpleNamespace(phone_code_hash="HASH1", type=SentCodeTypeApp(length=5))

    monkeypatch.setattr(FakeClient, "send_code_request", sent_to_app)
    res = await conn.connect(5, CREDS, {})
    assert res.message == CODE_WHERE["app"] and "чат «Telegram»" in res.message
    assert res.session["pending"]["code_delivery"] == "app"

    async def needs_email(self, phone):
        return SimpleNamespace(phone_code_hash="HASH2",
                               type=SentCodeTypeSetUpEmailRequired(apple_signin_allowed=False))

    monkeypatch.setattr(FakeClient, "send_code_request", needs_email)
    res = await conn.connect(5, CREDS, {})
    assert res.message == CODE_WHERE["email_setup"]

    # the plain fake returns no type at all: an honest generic hint
    monkeypatch.undo()
    assert (await conn.connect(5, CREDS, {})).message == CODE_WHERE_UNKNOWN


@pytest.mark.parametrize("creds", [
    {**CREDS, "api_id": "abc"}, {**CREDS, "api_hash": ""}, {**CREDS, "phone": "12"},
    {**CREDS, "proxy": "ftp://nope"},
])
async def test_connect_rejects_bad_input_without_network(conn, made, creds):
    res = await conn.connect(5, creds, {})
    assert res.status is SessionStatus.ERROR and res.message and not made


@pytest.mark.parametrize(("exc", "text"), [
    (tge.PhoneNumberInvalidError(request=None), "Неверный номер"),
    (tge.ApiIdInvalidError(request=None), "api_id"),
    (flood(77), "77"),
])
async def test_connect_errors_are_russian_and_leak_nothing(conn, exc, text):
    conn.script["send_code_request"] = exc
    res = await conn.connect(5, CREDS, {})
    assert res.status is SessionStatus.ERROR and text in res.message
    assert "hash-secret" not in res.message and res.session == {"pending": None}


async def test_code_success_returns_session_without_secrets_of_login(conn, made):
    first = await conn.connect(5, CREDS, {})
    res = await conn.verify(5, "12-345", first.session)
    assert res.status is SessionStatus.ACTIVE and res.account_label == "@stepan"
    assert made[0].calls[-1] == ("sign_in", PHONE, "12345", None, "HASH1")
    assert res.session == {"api_id": 12345, "api_hash": "hash-secret",
                           "string_session": "SESSION-NEW", "account_label": "@stepan"}
    assert not made[0].connected              # login client released


async def test_account_label_falls_back_to_phone(conn, made):
    first = await conn.connect(5, CREDS, {})
    made[0].me = SimpleNamespace(username=None, phone="380501234567", first_name="S")
    res = await conn.verify(5, "1", first.session)
    assert res.account_label == "+380501234567"


async def test_code_then_2fa_then_success(conn, made):
    first = await conn.connect(5, CREDS, {})
    conn.script["sign_in"] = tge.SessionPasswordNeededError(request=None)
    second = await conn.verify(5, "11111", first.session)
    assert second.status is SessionStatus.CHALLENGE and second.need == "2fa"
    assert second.session["pending"]["step"] == "2fa"
    assert "11111" not in repr(second.session)               # the code is not kept
    done = await conn.verify(5, "p@ss word", second.session)
    assert done.status is SessionStatus.ACTIVE
    assert made[0].calls[-1] == ("sign_in", None, None, "p@ss word", None)
    assert "p@ss word" not in repr(done.session)


async def test_2fa_resumes_after_restart_from_parked_session(conn, made):
    first = await conn.connect(5, CREDS, {})
    conn.script["sign_in"] = tge.SessionPasswordNeededError(request=None)
    second = await conn.verify(5, "11111", first.session)
    restarted = TelegramUserConnector(client_factory=lambda *a: made.append(
        FakeClient(*a, script={})) or made[-1])
    done = await restarted.verify(5, "pw", second.session)
    assert done.status is SessionStatus.ACTIVE and len(made) == 2


async def test_wrong_code_keeps_challenge(conn):
    first = await conn.connect(5, CREDS, {})
    conn.script["sign_in"] = tge.PhoneCodeInvalidError(request=None)
    res = await conn.verify(5, "00000", first.session)
    assert res.status is SessionStatus.CHALLENGE and res.need == "code"
    assert "Неверный код" in res.message and res.session["pending"] == first.session["pending"]


async def test_wrong_2fa_password_keeps_2fa_step(conn):
    first = await conn.connect(5, CREDS, {})
    conn.script["sign_in"] = tge.SessionPasswordNeededError(request=None)
    second = await conn.verify(5, "1", first.session)
    conn.script["sign_in_password"] = tge.PasswordHashInvalidError(request=None)
    res = await conn.verify(5, "bad", second.session)
    assert res.status is SessionStatus.CHALLENGE and res.need == "2fa"


async def test_expired_code_ends_the_login(conn):
    first = await conn.connect(5, CREDS, {})
    conn.script["sign_in"] = tge.PhoneCodeExpiredError(request=None)
    res = await conn.verify(5, "1", first.session)
    assert res.status is SessionStatus.ERROR and "истёк" in res.message
    assert res.session["pending"] is None


async def test_flood_during_verify_keeps_login_open_with_seconds(conn):
    first = await conn.connect(5, CREDS, {})
    conn.script["sign_in"] = flood(300)
    res = await conn.verify(5, "1", first.session)
    assert res.status is SessionStatus.CHALLENGE and res.need == "code" and "300" in res.message


async def test_verify_without_login_is_error(conn):
    assert (await conn.verify(5, "1", {})).status is SessionStatus.ERROR


async def test_state_reports_pending_new_and_expired(conn, made):
    first = await conn.connect(5, CREDS, {})
    assert (await conn.state(5, first.session)).need == "code"
    assert (await conn.state(5, {})).status is SessionStatus.NEW
    conn.script["is_authorized"] = False
    res = await conn.state(5, ACTIVE)
    assert res.status is SessionStatus.EXPIRED and res.account_label == "@stepan"


async def test_state_network_blip_is_not_expired(conn):
    conn.script["authorized"] = ConnectionError("net")
    assert (await conn.state(5, ACTIVE)).status is SessionStatus.ACTIVE


async def test_state_dead_session_error_is_expired(conn):
    conn.script["authorized"] = tge.AuthKeyUnregisteredError(request=None)
    assert (await conn.state(5, ACTIVE)).status is SessionStatus.EXPIRED


async def test_disconnect_logs_out_and_forgets(conn, made):
    await conn.disconnect(5, ACTIVE)
    assert made[0].logged_out
    assert 5 not in conn._runtime


async def test_polling_flags():
    c = TelegramUserConnector()
    assert c.polling is True and c.poll_interval_setting == "tg_user_poll_interval_s"
    with pytest.raises(ValueError, match="no active session"):
        c.build_port(1, {})


# --- polling -------------------------------------------------------------------------------

async def port_for(conn, made):
    port = conn.build_port(5, ACTIVE)
    await conn._runtime[5].transport.ready()          # clients are created lazily
    return port, made[-1]


async def test_fetch_maps_private_chats_and_skips_groups_bots_service(conn, made):
    port, c = await port_for(conn, made)
    now = datetime.now(UTC)
    bot = user(2)
    bot.bot = True
    gone = user(3)
    gone.deleted = True
    me = user(4)
    me.is_self = True
    c.dialogs = [
        dialog(user(10), 2, unread=1), dialog(bot, 1), dialog(user(777000), 1), dialog(gone, 1),
        dialog(me, 1), dialog(SimpleNamespace(id=-100), 1, is_user=False),
    ]
    c.history = {10: [msg(1, "старое", date=now - timedelta(days=30)),
                      msg(2, "привет", date=now)]}
    out = await port.fetch_inbound()
    assert [m.external_id for m in out] == ["10:2"]       # old, already-read message skipped
    m = out[0]
    assert (m.external_thread_id, m.external_user_id) == ("10", "10")
    assert m.text == "привет" and m.user_name == "Anna K" and m.user_handle == "@anna"
    assert m.phone == "+380671112233" and m.from_me is False and m.media_kind is None
    assert m.occurred_at.tzinfo is None


async def test_first_sync_takes_unread_even_if_old_and_never_own(conn, made):
    port, c = await port_for(conn, made)
    now = datetime.now(UTC)
    c.dialogs = [dialog(user(10), 3, unread=1)]
    c.history = {10: [msg(1, "я сам", out=True, date=now), msg(2, "старое", date=now - timedelta(days=9)),
                      msg(3, "непрочитанное", date=now - timedelta(days=8))]}
    out = await port.fetch_inbound()
    assert [m.text for m in out] == ["непрочитанное"]


async def test_cursor_only_new_messages_and_own_from_phone(conn, made):
    port, c = await port_for(conn, made)
    now = datetime.now(UTC)
    c.dialogs = [dialog(user(10), 2)]
    c.history = {10: [msg(1, "a", date=now), msg(2, "b", date=now)]}
    assert [m.text for m in await port.fetch_inbound()] == ["a", "b"]
    assert await port.fetch_inbound() == []               # top id == cursor: no history call
    n_calls = len(c.calls)
    await port.fetch_inbound()
    assert len(c.calls) == n_calls
    c.dialogs = [dialog(user(10), 4)]
    c.history[10] += [msg(3, "c", date=now), msg(4, "с телефона", out=True, date=now)]
    out = await conn.build_port(5, ACTIVE).fetch_inbound()   # a REBUILT port keeps the cursor
    assert [(m.text, m.from_me) for m in out] == [("c", False), ("с телефона", True)]
    assert c.calls[-1][2:] == (2, 100, True)              # min_id=cursor, follow limit, reverse


async def test_media_refs_and_kinds(conn, made):
    port, c = await port_for(conn, made)
    now = datetime.now(UTC)
    c.dialogs = [dialog(user(10), 3, unread=3)]
    c.history = {10: [msg(1, "", voice=True, date=now), msg(2, "подпись", photo=True, date=now),
                      msg(3, "", audio=True, date=now)]}
    out = await port.fetch_inbound()
    assert [(m.media_kind, m.media_url) for m in out] == [
        ("voice", f"{MEDIA_PREFIX}10:1"), ("image", f"{MEDIA_PREFIX}10:2"),
        ("audio", f"{MEDIA_PREFIX}10:3")]


async def test_read_flood_wait_backs_off(made):
    c = FakeClient(script={"dialogs": flood(60)})
    clock = [0.0]
    port = TelegramUserAdapter(TelethonTransport(lambda: c), TGUserState(),
                               monotonic=lambda: clock[0])
    assert await port.fetch_inbound() == []
    c.script["dialogs"] = None
    c.dialogs = [dialog(user(10), 1, unread=1)]
    c.history = {10: [msg(1)]}
    assert await port.fetch_inbound() == []               # still inside the flood window
    clock[0] = 61
    assert len(await port.fetch_inbound()) == 1


async def test_entity_cache_is_primed_after_restart(conn, made):
    port, c = await port_for(conn, made)
    c.unknown_entities = True
    res = await port.send_text("10", "hi")
    assert res.ok and c.sent == [(10, "hi")]


# --- sending -------------------------------------------------------------------------------

def adapter(c, clock=None, sleeps=None):
    clock = clock if clock is not None else [100.0]
    sleeps = sleeps if sleeps is not None else []

    async def sleep(s):
        sleeps.append(s)
        clock[0] += s

    return TelegramUserAdapter(TelethonTransport(lambda: c), TGUserState(),
                               sleep=sleep, monotonic=lambda: clock[0]), sleeps


async def test_send_text_returns_namespaced_id():
    c = FakeClient()
    a, _ = adapter(c)
    res = await a.send_text("10", "привет")
    assert res.ok and res.external_message_id == "10:901" and c.sent == [(10, "привет")]


async def test_send_chunks_long_text_and_throttles_between_sends():
    c = FakeClient()
    a, sleeps = adapter(c)
    res = await a.send_text("10", "x" * 5000)
    assert res.ok and [len(t) for _, t in c.sent] == [4096, 904]
    assert res.external_message_id == "10:901"
    assert sleeps == [3.0]                                # min interval before the 2nd chunk


async def test_min_interval_between_separate_sends():
    c = FakeClient()
    clock = [100.0]
    a, sleeps = adapter(c, clock)
    await a.send_text("10", "one")
    clock[0] += 1.0
    await a.send_text("11", "two")
    assert sleeps == [pytest.approx(2.0)]


async def test_flood_on_send_is_reported_for_retry():
    c = FakeClient(script={"send": flood(33)})
    a, _ = adapter(c)
    res = await a.send_text("10", "hi")
    assert not res.ok and res.error == "flood_wait:33"


async def test_flood_on_later_chunk_is_partial_delivery_not_retry():
    c = FakeClient(script={"send": [None, flood(5)]})
    a, _ = adapter(c)
    res = await a.send_text("10", "x" * 5000)
    assert res.ok and res.external_message_id == "10:901" and len(c.sent) == 1


async def test_other_send_error_is_failed_with_name():
    c = FakeClient(script={"send": tge.UserIsBlockedError(request=None)})
    a, _ = adapter(c)
    res = await a.send_text("10", "hi")
    assert not res.ok and res.error.startswith("UserIsBlockedError")


async def test_own_chunks_are_not_ingested_as_phone_messages(conn, made):
    port, c = await port_for(conn, made)
    now = datetime.now(UTC)
    await port.send_text("10", "x" * 5000)
    c.dialogs = [dialog(user(10), 902)]
    c.history = {10: [msg(901, "x" * 4096, out=True, date=now), msg(902, "x" * 904, out=True, date=now)]}
    port2 = conn.build_port(5, ACTIVE)
    # prime a cursor below the sent ids so the follow-up path runs
    conn._runtime[5].state.cursor[10] = 900
    assert await port2.fetch_inbound() == []


# --- media ---------------------------------------------------------------------------------

async def test_download_media_bounded(conn, made):
    port, c = await port_for(conn, made)
    c.history = {10: [msg(7)]}
    assert await port.download_media(f"{MEDIA_PREFIX}10:7") == b"bytes"
    c.history[10][0].file = SimpleNamespace(size=10**9)
    with pytest.raises(ValueError, match="too large"):
        await port.download_media(f"{MEDIA_PREFIX}10:7")
    with pytest.raises(ValueError, match="tgu-msg"):
        await port.download_media("https://x/y")
    with pytest.raises(ValueError, match="malformed"):
        await port.download_media(f"{MEDIA_PREFIX}x:y")


class VoiceClient(FakeClient):
    async def send_file(self, entity, file, voice_note=False, attributes=None):
        self.fail("send")
        self.sent.append((entity[1], ("voice", file.name, file.read(), voice_note,
                                      attributes[0].duration, attributes[0].voice)))
        return SimpleNamespace(id=900 + len(self.sent))


async def test_send_voice_sends_a_voice_note_and_remembers_its_echo():
    c = VoiceClient()
    a, _ = adapter(c)
    assert "send_voice" in a.capabilities
    res = await a.send_voice("10", b"OggS-voice", 4.6)
    assert res.ok and res.external_message_id == "10:901"
    assert c.sent == [(10, ("voice", "voice.ogg", b"OggS-voice", True, 5, True))]
    assert "10:901" in a.s.sent


async def test_send_voice_is_throttled_with_text():
    c = VoiceClient()
    a, sleeps = adapter(c)
    await a.send_text("10", "one")
    await a.send_voice("10", b"OggS", None)
    assert sleeps == [3.0]


async def test_send_voice_flood_and_bad_thread_are_failed_results():
    a, _ = adapter(VoiceClient(script={"send": flood(12)}))
    res = await a.send_voice("10", b"OggS", 1.0)
    assert not res.ok and res.error == "flood_wait:12"
    assert (await a.send_voice("not-a-peer", b"OggS", 1.0)).error == "bad thread id"


# --- login by QR ---------------------------------------------------------------------------

class FakeQr:
    """Telethon QRLogin stand-in: `outcome` decides what the scan wait ends with."""

    def __init__(self, outcome):
        self.url = "tg://login?token=AAA"
        self.outcome = outcome
        self.recreated = 0

    async def wait(self, timeout=None):  # noqa: ASYNC109 — mirrors Telethon QRLogin.wait
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def recreate(self):
        self.recreated += 1
        self.url = f"tg://login?token=NEW{self.recreated}"
        self.outcome = SimpleNamespace(username="marina", phone="380501112233", first_name="M")


def with_qr(monkeypatch, qr):
    async def qr_login(self):
        self.calls.append(("qr_login",))
        return qr
    monkeypatch.setattr(FakeClient, "qr_login", qr_login, raising=False)


async def settle():
    import asyncio
    for _ in range(3):
        await asyncio.sleep(0)


async def test_qr_login_shows_a_qr_and_finishes_after_the_scan(conn, made, monkeypatch):
    with_qr(monkeypatch, FakeQr(SimpleNamespace(username="marina", phone="380501112233")))
    res = await conn.connect(5, {**CREDS, "phone": "", "method": "qr"}, {})
    assert res.status is SessionStatus.PENDING and res.qr_data_uri.startswith("data:image/svg+xml")
    assert res.session["pending"]["step"] == "qr" and "Устройства" in res.message
    assert made[0].calls == [("qr_login",)]                    # no code request, no phone needed
    await settle()
    done = await conn.state(5, res.session)
    assert done.status is SessionStatus.ACTIVE and done.account_label == "@stepan"   # get_me()
    assert done.session["string_session"] and "pending" not in done.session


async def test_an_expired_qr_is_replaced_by_a_new_one(conn, monkeypatch):
    qr = FakeQr(TimeoutError())
    with_qr(monkeypatch, qr)
    res = await conn.connect(5, {**CREDS, "method": "qr"}, {})
    await settle()
    again = await conn.state(5, res.session)
    assert again.status is SessionStatus.PENDING and qr.recreated == 1
    assert again.qr_data_uri != res.qr_data_uri


async def test_qr_scan_on_a_2fa_account_asks_for_the_password(conn, made, monkeypatch):
    with_qr(monkeypatch, FakeQr(tge.SessionPasswordNeededError(request=None)))
    res = await conn.connect(5, {**CREDS, "method": "qr"}, {})
    await settle()
    st = await conn.state(5, res.session)
    assert st.status is SessionStatus.CHALLENGE and st.need == "2fa"
    assert st.session["pending"]["step"] == "2fa"
    ok = await conn.verify(5, "cloud-pass", st.session)
    assert ok.status is SessionStatus.ACTIVE
    assert ("sign_in", None, None, "cloud-pass", None) in made[0].calls


async def test_qr_login_lost_on_restart_asks_to_connect_again(conn):
    st = await conn.state(5, {"pending": {"step": "qr", "api_id": 1, "api_hash": "h"}})
    assert st.status is SessionStatus.ERROR and "Подключить" in st.message
    assert st.session["pending"] is None


# --- new-message events (worker) -----------------------------------------------------------

async def test_listening_transport_wakes_on_new_private_messages_only():
    woken = []
    handlers = []
    client = FakeClient("SESS")
    client.add_event_handler = lambda fn, ev: handlers.append((fn, ev))
    transport = TelethonTransport(lambda: client, on_new_message=lambda: woken.append(1))
    await transport.ready()
    fn, ev = handlers[0]
    assert type(ev).__name__ == "NewMessage"
    await fn(SimpleNamespace(is_private=False))          # a group: ignored
    await fn(SimpleNamespace(is_private=True))
    assert woken == [1]
    await transport.ready()                               # reconnects never add a second handler
    assert len(handlers) == 1


async def test_after_listen_the_connector_builds_listening_clients(made):
    calls = []

    def factory(string_session="", api_id=0, api_hash="", proxy="", **kw):
        calls.append(kw)
        c = FakeClient(string_session, api_id, api_hash, proxy)
        c.add_event_handler = lambda fn, ev: None
        made.append(c)
        return c

    conn = TelegramUserConnector(client_factory=factory)
    conn.listen(lambda: None)
    port = conn.build_port(5, {"api_id": 1, "api_hash": "h", "string_session": "SESS"})
    await port.t.ready()
    assert calls == [{"receive_updates": True}]


async def test_chat_action_sets_typing_or_recording_on_the_peer():
    class CallingClient(FakeClient):
        def __init__(self):
            super().__init__("SESS")
            self.requests = []

        async def __call__(self, request):
            self.requests.append((type(request).__name__, type(request.action).__name__))

    client = CallingClient()
    port = TelegramUserAdapter(TelethonTransport(lambda: client), TGUserState())
    await port.send_chat_action("777", "typing")
    await port.send_chat_action("777", "record_voice")
    assert client.requests == [("SetTypingRequest", "SendMessageTypingAction"),
                               ("SetTypingRequest", "SendMessageRecordAudioAction")]
    await port.send_chat_action("not-a-peer", "typing")  # bad thread id: swallowed


async def test_mark_read_reads_the_history_up_to_the_message():
    class ReadingClient(FakeClient):
        def __init__(self):
            super().__init__("SESS")
            self.requests = []

        async def __call__(self, request):
            self.requests.append((type(request).__name__, request.max_id))

    client = ReadingClient()
    port = TelegramUserAdapter(TelethonTransport(lambda: client), TGUserState())
    assert Capability.MARK_READ in port.capabilities
    await port.mark_read("777", "777:42")
    assert client.requests == [("ReadHistoryRequest", 42)]
    await port.mark_read("777", "broken")              # bad id: swallowed
    assert len(client.requests) == 1
