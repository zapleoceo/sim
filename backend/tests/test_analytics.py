from __future__ import annotations

from datetime import datetime

import pytest

from sim.adapters.db.models import (
    Client,
    ClientIdentity,
    Dialog,
    LlmCall,
    Message,
    PartnerEvent,
)
from sim.adapters.db.session import transaction
from sim.domain.enums import (
    Author,
    ChannelKind,
    Direction,
    PartnerEventKind,
    Stage,
)
from sim.services.analytics import AnalyticsService, ratio

START = datetime(2026, 11, 1)
END = datetime(2026, 11, 4)  # exclusive: Nov 1..3


async def add_client(s, world, n: int, created: datetime, stage=Stage.FIRST_CONTACT):
    client = Client(persona_id=world.persona.id, display_name=f"c{n}", ref_code=f"ref{n:05d}",
                    stage=stage, created_at=created)
    s.add(client)
    await s.flush()
    s.add(ClientIdentity(client_id=client.id, channel_id=world.channel.id,
                         channel_kind=ChannelKind.TELEGRAM, external_id=str(n)))
    dialog = Dialog(channel_id=world.channel.id, external_thread_id=str(n), client_id=client.id)
    s.add(dialog)
    await s.flush()
    return client, dialog


def msg(dialog, direction, at):
    return Message(dialog_id=dialog.id, direction=direction, created_at=at, text="t",
                   author=Author.CLIENT if direction is Direction.IN else Author.AI)


async def seed_data(container, world):
    async with transaction(container.factory) as s:
        a, da = await add_client(s, world, 1, datetime(2026, 11, 1, 9), Stage.PAID)
        b, db_ = await add_client(s, world, 2, datetime(2026, 11, 2, 9), Stage.ENGAGED)
        c, dc = await add_client(s, world, 3, datetime(2026, 11, 2, 10), Stage.ENGAGED)
        old, dold = await add_client(s, world, 4, datetime(2026, 10, 1), Stage.INTERESTED)
        s.add_all([
            msg(da, Direction.IN, datetime(2026, 11, 1, 9)),
            msg(da, Direction.OUT, datetime(2026, 11, 1, 9, 1)),
            msg(da, Direction.IN, datetime(2026, 11, 1, 9, 5)),     # a: real exchange
            msg(db_, Direction.IN, datetime(2026, 11, 2, 9)),       # b: one-sided
            msg(dc, Direction.OUT, datetime(2026, 11, 2, 11)),      # c: only we wrote -> not a contact
            msg(dold, Direction.IN, datetime(2026, 11, 3, 9)),      # old client writes again
            msg(dold, Direction.IN, datetime(2026, 10, 1, 9)),      # outside the period
        ])
        s.add_all([
            PartnerEvent(client_id=a.id, kind=PartnerEventKind.REGISTRATION, external_id="r1",
                         occurred_at=datetime(2026, 11, 1, 12), payout=5.0),
            PartnerEvent(client_id=a.id, kind=PartnerEventKind.PAYMENT, external_id="p1",
                         amount=100.0, payout=20.0, occurred_at=datetime(2026, 11, 2, 12)),
            PartnerEvent(client_id=a.id, kind=PartnerEventKind.PAYMENT, external_id="p2",
                         amount=50.5, occurred_at=datetime(2026, 11, 3, 12)),
            PartnerEvent(client_id=old.id, kind=PartnerEventKind.REGISTRATION, external_id="r2",
                         occurred_at=datetime(2026, 11, 3, 13)),
            PartnerEvent(client_id=b.id, kind=PartnerEventKind.PAYMENT, external_id="late",
                         amount=999, occurred_at=datetime(2026, 12, 1)),     # outside
        ])
        s.add_all([
            LlmCall(dialog_id=da.id, tokens_in=100, tokens_out=10, cost_usd=0.5,
                    created_at=datetime(2026, 11, 1, 9)),
            LlmCall(dialog_id=db_.id, tokens_in=50, tokens_out=5, cost_usd=0.25,
                    created_at=datetime(2026, 11, 2, 9)),
            LlmCall(dialog_id=da.id, tokens_in=1, tokens_out=1, cost_usd=9,
                    created_at=datetime(2026, 9, 1)),                         # outside
        ])


async def test_summary_math(container, world):
    await seed_data(container, world)
    async with container.factory() as s:
        out = await AnalyticsService(s).summary(START, END)

    assert out["contacts"] == 3            # a, b, old
    assert out["new_contacts"] == 3        # a, b, c created in period
    assert out["replied"] == 1             # only a had OUT then IN
    assert out["by_stage"]["paid"] == 1 and out["by_stage"]["engaged"] == 1
    assert out["by_stage"]["interested"] == 1 and sum(out["by_stage"].values()) == 3
    assert out["registrations"] == 2       # a and old
    assert out["payments"] == 2
    assert out["revenue"] == pytest.approx(150.5)
    assert out["partner_payouts"] == pytest.approx(25.0)
    assert out["conversion"] == {
        "contact_to_registration": round(2 / 3, 4),
        "registration_to_payment": 0.5,
    }
    assert out["llm"] == {"calls": 2, "cost_usd": 0.75, "tokens_in": 150, "tokens_out": 15}

    tg = next(c for c in out["by_channel"] if c["kind"] == "telegram")
    assert tg == {"kind": "telegram", "contacts": 3, "registrations": 2, "payments": 2}
    assert [d["date"] for d in out["daily"]] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert out["daily"][0] == {"date": "2026-11-01", "contacts": 1, "registrations": 1, "payments": 0}
    assert out["daily"][1] == {"date": "2026-11-02", "contacts": 2, "registrations": 0, "payments": 1}
    assert out["daily"][2] == {"date": "2026-11-03", "contacts": 0, "registrations": 1, "payments": 1}


async def test_empty_period_is_all_zero_without_division_errors(container, world):
    async with container.factory() as s:
        out = await AnalyticsService(s).summary(START, END)
    assert out["contacts"] == 0 and out["conversion"]["contact_to_registration"] == 0.0
    assert len(out["daily"]) == 3 and all(v == 0 for v in out["by_stage"].values())


async def test_persona_filter(container, world):
    await seed_data(container, world)
    async with container.factory() as s:
        assert (await AnalyticsService(s).summary(START, END, persona_id=999))["contacts"] == 0
        assert (await AnalyticsService(s).summary(START, END, persona_id=world.persona.id))[
            "contacts"
        ] == 3


@pytest.mark.parametrize(("n", "d", "expected"), [(1, 3, 0.3333), (0, 5, 0.0), (5, 0, 0.0)])
def test_ratio(n, d, expected):
    assert ratio(n, d) == expected


async def test_summary_endpoint_range_validation(api):
    from tests.conftest import OWNER, login

    await login(api, *OWNER)
    ok = await api.get("/api/analytics/summary", params={"from": "2026-11-01", "to": "2026-11-03"})
    assert ok.status_code == 200 and len(ok.json()["daily"]) == 3  # `to` day is inclusive
    assert (await api.get("/api/analytics/summary", params={"from": "x"})).status_code == 422
    assert (
        await api.get("/api/analytics/summary", params={"from": "2026-11-03", "to": "2026-11-01"})
    ).status_code == 422
