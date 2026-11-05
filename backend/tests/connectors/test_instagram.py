# ruff: noqa: S105, S106
from pathlib import Path
from types import SimpleNamespace

import pytest
from instagrapi.exceptions import BadPassword, ChallengeRequired, LoginRequired, TwoFactorRequired

from sim.connectors.instagram import InstagramConnector
from sim.domain.enums import SessionStatus

OWN = "111"


class FakeClient:
    """Stands in for instagrapi.Client. `script` maps an action to an exception or None."""

    def __init__(self, settings=None, *, proxy="", script=None, **_):
        self.settings = dict(settings or {})
        self.proxy = proxy
        self.script = script if script is not None else {}
        self.last_json = {}
        self.calls = []
        self.threads = []
        self.sent = []
        self.challenge_code_handler = None
        self.authorization_data = {"ds_user_id": OWN}

    def _act(self, name):
        self.calls.append(name)
        exc = self.script.get(name)
        if exc is not None:
            raise exc

    def login(self, user, pw, verification_code=""):
        self.calls.append(("login", user, verification_code))
        exc = self.script.get("login")
        if exc is not None:
            if isinstance(exc, list):
                exc = exc.pop(0) if exc else None
            if exc is not None:
                raise exc
        self.settings = {"uuids": {"device": "d"}, "authorization_data": {"ds_user_id": OWN}}

    def challenge_resolve(self, last_json):
        self.calls.append(("challenge_resolve", self.challenge_code_handler("u", "email")))
        exc = self.script.get("challenge_resolve")
        if exc is not None:
            raise exc
        self.settings = {"authorization_data": {"ds_user_id": OWN}}

    def get_settings(self):
        return dict(self.settings)

    def private_request(self, endpoint, params=None):
        self.calls.append(endpoint)
        if endpoint.endswith("inbox/") and "pending" not in endpoint:
            return {"inbox": {"threads": self.threads, "has_older": False}}
        return {"inbox": {"threads": [], "has_older": False}}

    def direct_send(self, text, thread_ids=None):
        self.sent.append((text, thread_ids))
        return SimpleNamespace(id="item-77")

    def get_timeline_feed(self):
        self._act("health")

    def photo_upload(self, path, caption):
        self.calls.append(("photo", Path(path), caption))
        return SimpleNamespace(pk="555")


def make(script=None, clients=None):
    created = clients if clients is not None else []

    def factory(settings=None, **kw):
        c = FakeClient(settings, script=script, **kw)
        created.append(c)
        return c

    return InstagramConnector(client_factory=factory), created


async def test_connect_success_drops_password_and_stores_settings():
    conn, _ = make()
    res = await conn.connect(1, {"username": "@bob", "password": "pw", "proxy": "http://p:1"}, {})
    assert res.status is SessionStatus.ACTIVE and res.account_label == "@bob"
    assert res.session["username"] == "bob" and res.session["proxy"] == "http://p:1"
    assert res.session["settings"]["authorization_data"]["ds_user_id"] == OWN
    assert "pw" not in repr(res.session)


async def test_connect_bad_password_is_error():
    conn, _ = make({"login": BadPassword("nope pw")})
    res = await conn.connect(1, {"username": "bob", "password": "pw"}, {})
    assert res.status is SessionStatus.ERROR and "pw" not in res.message
    assert res.session == {}


async def test_connect_missing_credentials():
    conn, _ = make()
    assert (await conn.connect(1, {"username": "bob"}, {})).status is SessionStatus.ERROR


async def test_two_factor_then_verify_completes_even_after_restart():
    conn, clients = make({"login": [TwoFactorRequired("2fa")]})
    res = await conn.connect(1, {"username": "bob", "password": "pw"}, {})
    assert res.status is SessionStatus.CHALLENGE and res.need == "2fa"
    pending = res.session["pending"]
    assert pending["password"] == "pw" and pending["username"] == "bob"
    # in-process client reused
    done = await conn.verify(1, " 123456 ", res.session)
    assert done.status is SessionStatus.ACTIVE and "pending" not in done.session
    assert ("login", "bob", "123456") in clients[0].calls
    # fresh connector (process restart): rebuilt from the stored session dict
    conn2, clients2 = make()
    done2 = await conn2.verify(1, "654321", res.session)
    assert done2.status is SessionStatus.ACTIVE
    assert ("login", "bob", "654321") in clients2[0].calls


async def test_hard_failure_during_verify_ends_flow():
    conn, _ = make({"login": [TwoFactorRequired("x")]})
    res = await conn.connect(1, {"username": "bob", "password": "pw"}, {})
    conn._live[1].script["login"] = [BadPassword("wrong code")]
    again = await conn.verify(1, "000000", res.session)
    assert again.status is SessionStatus.ERROR     # bad password-class error ends the flow


async def test_challenge_then_verify_uses_code_handler():
    conn2, clients2 = make({"login": [ChallengeRequired(message="challenge_required")]})
    res = await conn2.connect(1, {"username": "bob", "password": "pw"}, {})
    assert res.status is SessionStatus.CHALLENGE and res.need == "challenge"
    assert "password" not in res.session["pending"]
    done = await conn2.verify(1, "424242", res.session)
    assert done.status is SessionStatus.ACTIVE
    assert ("challenge_resolve", "424242") in clients2[0].calls


async def test_manual_challenge_keeps_password_for_retry():
    exc = ChallengeRequired("Manual verification required via Instagram native flow")
    conn, _ = make({"login": [exc]})
    res = await conn.connect(1, {"username": "bob", "password": "pw"}, {})
    assert res.need == "challenge" and res.session["pending"]["kind"] == "manual"
    assert res.session["pending"]["password"] == "pw"
    done = await conn.verify(1, "", res.session)
    assert done.status is SessionStatus.ACTIVE


async def test_verify_without_pending():
    conn, _ = make()
    assert (await conn.verify(1, "1", {})).status is SessionStatus.ERROR


def _thread(items, tid="900"):
    return {"thread_id": tid, "is_group": False,
            "users": [{"pk": "222", "username": "lead", "full_name": "Lead L",
                       "profile_pic_url": "https://cdn/a.jpg"}],
            "items": items}


def _item(iid, uid, text, ts):
    return {"item_id": iid, "user_id": uid, "item_type": "text", "text": text,
            "timestamp": ts}


SESSION = {"settings": {"authorization_data": {"ds_user_id": OWN}}, "username": "bob",
           "proxy": ""}


async def test_fetch_inbound_mapping_and_cursor():
    conn, clients = make()
    port = conn.build_port(1, SESSION)
    # IG returns newest first
    new = [_item("i3", "222", "latest", 1_700_000_003_000_000),
           _item("i2", OWN, "my reply", 1_700_000_002_000_000),
           _item("i1", "222", "first", 1_700_000_001_000_000)]
    # client is created lazily on first fetch
    first_client_threads = [_thread(new)]
    orig = conn._factory

    def factory(settings=None, **kw):
        c = orig(settings, **kw)
        c.threads = first_client_threads
        return c

    conn._factory = factory
    port = conn.build_port(1, SESSION)
    msgs = await port.fetch_inbound()
    assert [m.external_id for m in msgs] == ["i1", "i2", "i3"]          # oldest first
    assert [m.from_me for m in msgs] == [False, True, False]
    m = msgs[0]
    assert (m.external_thread_id, m.external_user_id, m.text) == ("900", "222", "first")
    assert m.user_handle == "@lead" and m.user_name == "Lead L"
    assert m.occurred_at.year == 2023
    # cursor survives a port rebuild; only a new item comes out
    first_client_threads[0]["items"].insert(0, _item("i4", "222", "again", 1_700_000_004_000_000))
    again = await conn.build_port(1, SESSION).fetch_inbound()
    assert [m.external_id for m in again] == ["i4"]


async def test_fetch_inbound_skips_groups_and_unknown_sender():
    conn, _ = make()
    orig = conn._factory

    def factory(settings=None, **kw):
        c = orig(settings, **kw)
        grp = _thread([_item("g1", "222", "x", 1)], tid="1")
        grp["is_group"] = True
        c.threads = [grp, _thread([{"item_id": "n", "item_type": "text", "text": "?",
                                    "timestamp": 2}], tid="2")]
        return c

    conn._factory = factory
    assert await conn.build_port(1, SESSION).fetch_inbound() == []


async def test_send_text_and_failure():
    conn, clients = make()
    port = conn.build_port(1, SESSION)
    res = await port.send_text("900", "hello")
    assert res.ok and res.external_message_id == "item-77"
    assert clients[0].sent == [("hello", [900])]
    clients[0].direct_send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("throttled"))
    bad = await port.send_text("900", "x")
    assert not bad.ok and "throttled" in bad.error


@pytest.mark.parametrize(("script", "expected"), [
    ({}, SessionStatus.ACTIVE),
    ({"health": LoginRequired("x")}, SessionStatus.EXPIRED),
    ({"health": ChallengeRequired(message="c")}, SessionStatus.CHALLENGE),
    ({"health": TimeoutError("blip")}, SessionStatus.ACTIVE),
])
async def test_session_status(script, expected):
    conn, _ = make(script)
    assert await conn.build_port(1, SESSION).session_status() is expected
    assert (await conn.state(1, SESSION)).status is expected


async def test_state_pending_and_new():
    conn, _ = make()
    assert (await conn.state(1, {})).status is SessionStatus.NEW
    r = await conn.state(1, {"pending": {"kind": "2fa"}})
    assert r.status is SessionStatus.CHALLENGE and r.need == "2fa"


async def test_publish_photo_capability():
    conn, clients = make()
    port = conn.build_port(1, SESSION)
    assert await port.publish_photo(Path("a.jpg"), "cap") == "555"
    assert clients[0].calls[-1] == ("photo", Path("a.jpg"), "cap")
    assert "publish_photo" in conn.capabilities


def test_polling_connector_has_no_webhook():
    conn, _ = make()
    assert conn.polling is True and conn.verify_webhook({}, SESSION) is False
    assert conn.parse_webhook({}) == []
