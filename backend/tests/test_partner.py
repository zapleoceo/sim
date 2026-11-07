from __future__ import annotations

import json
import time
from datetime import datetime

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Client, PartnerEvent
from sim.adapters.db.session import transaction
from sim.domain.enums import PartnerEventKind, Stage
from sim.services.partner import PartnerAuthError, sign, verify_signature
from tests.conftest import PARTNER_SECRET, inbound
from tests.test_ingest import ingest

BODY = b'{"client_ref":"x"}'


def test_valid_signature_passes():
    ts = str(int(time.time()))
    verify_signature("s", ts, sign("s", ts, BODY), BODY)


@pytest.mark.parametrize(
    ("secret", "ts_offset", "tamper", "match"),
    [
        ("s", 0, True, "bad signature"),            # wrong signature
        ("s", -600, False, "replay window"),        # stale
        ("s", 600, False, "replay window"),         # from the future
        ("", 0, False, "not configured"),           # no secret configured
    ],
)
def test_rejected_signatures(secret, ts_offset, tamper, match):
    ts = str(int(time.time()) + ts_offset)
    sig = sign("s", ts, BODY)
    if tamper:
        sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    with pytest.raises(PartnerAuthError, match=match):
        verify_signature(secret, ts, sig, BODY)


@pytest.mark.parametrize(("ts", "sig"), [(None, "x"), ("1", None), ("abc", "x")])
def test_missing_or_malformed_headers(ts, sig):
    with pytest.raises(PartnerAuthError):
        verify_signature("s", ts, sig, BODY)


def signed_headers(body: bytes, ts: int | None = None) -> dict[str, str]:
    stamp = str(ts if ts is not None else int(time.time()))
    return {"X-Sim-Timestamp": stamp, "X-Sim-Signature": sign(PARTNER_SECRET, stamp, body),
            "Content-Type": "application/json"}


async def client_ref(container, world) -> str:
    await ingest(container, world.channel.id, inbound("hi"))
    async with container.factory() as s:
        return (await s.scalars(select(Client))).one().ref_code


async def test_webhook_registration_then_payment(api, container, world):
    ref = await client_ref(container, world)
    reg = json.dumps({"client_ref": ref, "event": "registration", "external_id": "r1",
                      "occurred_at": "2026-11-05T10:00:00Z"}).encode()
    assert (await api.post("/hooks/partner", content=reg, headers=signed_headers(reg))).json() == {"ok": True}
    pay = json.dumps({"client_ref": ref, "event": "payment", "external_id": "p1", "amount": 49.5,
                      "currency": "USD", "payout": 10, "occurred_at": "2026-11-05T11:00:00Z"}).encode()
    assert (await api.post("/hooks/partner", content=pay, headers=signed_headers(pay))).status_code == 200
    # idempotent repeat
    assert (await api.post("/hooks/partner", content=pay, headers=signed_headers(pay))).status_code == 200
    async with container.factory() as s:
        assert (await s.scalars(select(Client))).one().stage is Stage.PAID
        events = (await s.scalars(select(PartnerEvent))).all()
        assert len(events) == 2
        assert sorted(e.amount or 0 for e in events) == [0, 49.5]


async def test_webhook_bad_signature_replay_and_unknown_ref(api, container, world):
    ref = await client_ref(container, world)
    body = json.dumps({"client_ref": ref, "event": "registration", "external_id": "r1",
                       "occurred_at": "2026-11-05T10:00:00Z"}).encode()
    bad = signed_headers(body)
    bad["X-Sim-Signature"] = "0" * 64
    assert (await api.post("/hooks/partner", content=body, headers=bad)).status_code == 401
    old = signed_headers(body, ts=int(time.time()) - 3600)
    assert (await api.post("/hooks/partner", content=body, headers=old)).status_code == 401
    assert (await api.post("/hooks/partner", content=body)).status_code == 401
    ghost = json.dumps({"client_ref": "nope", "event": "payment", "external_id": "z",
                        "occurred_at": "2026-11-05T10:00:00Z"}).encode()
    assert (await api.post("/hooks/partner", content=ghost, headers=signed_headers(ghost))).status_code == 404


async def test_record_race_on_unique_event_is_duplicate(container, world, monkeypatch):
    """Two concurrent identical webhooks: both pass find(), the loser hits the unique constraint."""
    from sim.adapters.db.repositories.records import PartnerEventRepo
    from sim.services.partner import PartnerEventIn

    ref = await client_ref(container, world)
    event = PartnerEventIn(client_ref=ref, kind=PartnerEventKind.PAYMENT, external_id="race",
                           occurred_at=datetime(2026, 11, 5, 11, 0), amount=10)
    async with transaction(container.factory) as s:
        assert await container.partner(s).record(event) is True

    async def stale_find(self, kind, external_id):
        return None   # the twin's row is not visible yet at check time

    monkeypatch.setattr(PartnerEventRepo, "find", stale_find)
    async with transaction(container.factory) as s:
        assert await container.partner(s).record(event) is False
    async with container.factory() as s:
        assert len((await s.scalars(select(PartnerEvent))).all()) == 1


async def test_csv_import_counts_and_errors(container, world):
    ref = await client_ref(container, world)
    csv_text = (
        "client_ref,event,amount,currency,payout,occurred_at,external_id\n"
        f"{ref},registration,,,,2026-11-05T10:00:00Z,e1\n"
        f"{ref},payment,100,USD,20,2026-11-05T11:00:00Z,e2\n"
        f"{ref},payment,100,USD,20,2026-11-05T11:00:00Z,e2\n"      # duplicate
        "nobody,registration,,,,2026-11-05T10:00:00Z,e3\n"         # unknown ref
        f"{ref},refund,,,,2026-11-05T10:00:00Z,e4\n"                # bad event
    )
    async with transaction(container.factory) as s:
        res = await container.partner(s).import_csv(csv_text.encode())
    assert (res.imported, res.skipped, len(res.errors)) == (2, 1, 2)
    assert "line 5" in res.errors[0] and "line 6" in res.errors[1]
    async with container.factory() as s:
        assert (await s.scalars(select(Client))).one().stage is Stage.PAID


async def test_csv_import_missing_columns(container):
    async with transaction(container.factory) as s:
        res = await container.partner(s).import_csv(b"a,b\n1,2\n")
    assert res.imported == 0 and "missing columns" in res.errors[0]


async def test_import_endpoint_owner_only(api, container, world):
    ref = await client_ref(container, world)
    csv_bytes = f"client_ref,event,external_id\n{ref},registration,owner-reg-1\n".encode()
    csrf = await login_owner(api)
    resp = await api.post("/api/partner/import", files={"file": ("p.csv", csv_bytes, "text/csv")},
                          headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200 and resp.json()["imported"] == 1


async def login_owner(api) -> str:
    from tests.conftest import OWNER, login

    return await login(api, *OWNER)


@pytest.mark.parametrize("identity", ["", ",,", ",  ,  "])
async def test_csv_missing_identity_never_creates_events(container, world, identity):
    ref = await client_ref(container, world)
    content = ("client_ref,event,occurred_at,external_id\n"
               f"{ref},payment{identity}\n").encode()
    for _ in range(2):
        async with transaction(container.factory) as s:
            result = await container.partner(s).import_csv(content)
        assert (result.imported, result.skipped) == (0, 0)
        assert result.errors == [
            "line 2: external_id or occurred_at is required for repeatable CSV imports"
        ]
    async with container.factory() as s:
        assert not (await s.scalars(select(PartnerEvent))).all()
        assert (await s.scalars(select(Client))).one().stage is not Stage.PAID


@pytest.mark.parametrize("identity", [
    ",payment-123",
    "2026-11-05T10:00:00Z,",
    "2026-11-05T10:00:00Z,payment-123",
])
async def test_csv_repeat_with_stable_identity_does_not_double_revenue(
    container, world, monkeypatch, identity,
):
    ref = await client_ref(container, world)
    content = ("client_ref,event,amount,currency,payout,occurred_at,external_id\n"
               f"{ref},payment,100,USD,20,{identity}\n").encode()
    for day, expected in [(5, (1, 0)), (6, (0, 1))]:
        monkeypatch.setattr("sim.services.partner.utcnow", lambda day=day: datetime(2026, 11, day))
        async with transaction(container.factory) as s:
            result = await container.partner(s).import_csv(content)
        assert (result.imported, result.skipped) == expected
        assert result.errors == []
    async with container.factory() as s:
        events = (await s.scalars(select(PartnerEvent))).all()
        assert len(events) == 1
        assert sum(e.amount for e in events) == 100
        assert sum(e.payout for e in events) == 20


async def test_csv_bad_identity_does_not_block_valid_rows(container, world):
    ref = await client_ref(container, world)
    content = ("client_ref,event,external_id\n"
               f"{ref},registration,\n"
               f"{ref},registration,valid-reg\n").encode()
    async with transaction(container.factory) as s:
        result = await container.partner(s).import_csv(content)
    assert (result.imported, result.skipped, len(result.errors)) == (1, 0, 1)
    assert result.errors[0].startswith("line 2:")
