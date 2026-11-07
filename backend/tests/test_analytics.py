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
                         occurred_at=datetime(2026, 11, 1, 12), payout=5.0, currency="USD"),
            PartnerEvent(client_id=a.id, kind=PartnerEventKind.PAYMENT, external_id="p1",
                         amount=100.0, payout=20.0, currency="USD", occurred_at=datetime(2026, 11, 2, 12)),
            PartnerEvent(client_id=a.id, kind=PartnerEventKind.PAYMENT, external_id="p2",
                         amount=50.5, currency="USD", occurred_at=datetime(2026, 11, 3, 12)),
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


async def test_money_buckets_currency_normalization_unknown_and_payouts(container, world, api):
    async with transaction(container.factory) as s:
        client, _ = await add_client(s, world, 11, START)
        for index, (kind, currency, amount, payout) in enumerate([
            (PartnerEventKind.PAYMENT, " usd ", 100, 10),
            (PartnerEventKind.PAYMENT, "USD", 0.1, 0.2),
            (PartnerEventKind.PAYMENT, "eur", 50, None),
            (PartnerEventKind.PAYMENT, None, 7, 2),
            (PartnerEventKind.PAYMENT, "   ", 3, None),
            (PartnerEventKind.REGISTRATION, "EUR", 999, 5),
            (PartnerEventKind.REGISTRATION, "JPY", None, None),
            (PartnerEventKind.PAYMENT, "custom", 0, 0),
        ]):
            s.add(PartnerEvent(client_id=client.id, kind=kind, currency=currency,
                               amount=amount, payout=payout, occurred_at=START,
                               external_id=f"currency-{index}"))
    async with container.factory() as s:
        out = await AnalyticsService(s).summary(START, END)
    assert out["revenue"] is None and out["partner_payouts"] is None
    assert out["revenue_by_currency"] == [
        {"currency": "CUSTOM", "total": 0.0},
        {"currency": "EUR", "total": 50.0},
        {"currency": "USD", "total": 100.1},
        {"currency": None, "total": 10.0},
    ]
    assert out["partner_payouts_by_currency"] == [
        {"currency": "CUSTOM", "total": 0.0},
        {"currency": "EUR", "total": 5.0},
        {"currency": "USD", "total": 10.2},
        {"currency": None, "total": 2.0},
    ]
    assert out["payments"] == 6 and out["registrations"] == 1
    assert out["llm"]["cost_usd"] == 0
    from tests.conftest import OWNER, login

    await login(api, *OWNER)
    response = await api.get("/api/analytics/summary", params={"from": "2026-11-01", "to": "2026-11-03"})
    assert response.status_code == 200
    assert response.json()["revenue"] is None
    assert response.json()["revenue_by_currency"] == out["revenue_by_currency"]
    assert response.json()["partner_payouts_by_currency"] == out["partner_payouts_by_currency"]


async def test_currency_buckets_persona_and_half_open_date_filters(container, world):
    from sim.adapters.db.models import Persona

    async with transaction(container.factory) as s:
        client, _ = await add_client(s, world, 12, START)
        other_persona = Persona(name="Other currency")
        s.add(other_persona)
        await s.flush()
        other = Client(persona_id=other_persona.id, ref_code="other-currency", created_at=START)
        s.add(other)
        await s.flush()
        for index, (cid, at, currency, amount) in enumerate([
            (client.id, START, "usd", 10),
            (client.id, END, "EUR", 900),
            (client.id, datetime(2026, 10, 31), "JPY", 800),
            (other.id, START, "EUR", 30),
        ]):
            s.add(PartnerEvent(client_id=cid, kind=PartnerEventKind.PAYMENT,
                               currency=currency, amount=amount, payout=amount / 2,
                               occurred_at=at, external_id=f"filtered-{index}"))
        # An unrelated nonmonetary unknown-currency event must not contaminate totals.
        s.add(PartnerEvent(client_id=client.id, kind=PartnerEventKind.REGISTRATION,
                           occurred_at=START, external_id="empty-money"))
    async with container.factory() as s:
        selected = await AnalyticsService(s).summary(START, END, world.persona.id)
        all_personas = await AnalyticsService(s).summary(START, END)
        absent = await AnalyticsService(s).summary(START, END, 999)
    assert selected["revenue"] == 10 and selected["partner_payouts"] == 5
    assert selected["revenue_by_currency"] == [{"currency": "USD", "total": 10.0}]
    assert selected["partner_payouts_by_currency"] == [{"currency": "USD", "total": 5.0}]
    assert all_personas["revenue"] is None and all_personas["partner_payouts"] is None
    assert all_personas["revenue_by_currency"] == [
        {"currency": "EUR", "total": 30.0}, {"currency": "USD", "total": 10.0},
    ]
    assert absent["revenue"] == absent["partner_payouts"] == 0.0
    assert absent["revenue_by_currency"] == absent["partner_payouts_by_currency"] == []


@pytest.mark.parametrize("currency", [None, "", " \t "])
async def test_only_unknown_currency_has_no_scalar_total(container, world, currency):
    async with transaction(container.factory) as s:
        client, _ = await add_client(s, world, 13, START)
        s.add(PartnerEvent(client_id=client.id, kind=PartnerEventKind.PAYMENT,
                           currency=currency, amount=10, payout=2, occurred_at=START,
                           external_id="unknown-only"))
    async with container.factory() as s:
        out = await AnalyticsService(s).summary(START, END)
    assert out["revenue"] is None and out["partner_payouts"] is None
    assert out["revenue_by_currency"] == [{"currency": None, "total": 10.0}]
    assert out["partner_payouts_by_currency"] == [{"currency": None, "total": 2.0}]


async def test_each_money_metric_has_independent_currency_membership(container, world):
    async with transaction(container.factory) as s:
        client, _ = await add_client(s, world, 14, START)
        s.add_all([
            PartnerEvent(client_id=client.id, kind=PartnerEventKind.PAYMENT,
                         currency="USD", amount=0, occurred_at=START, external_id="zero-usd"),
            PartnerEvent(client_id=client.id, kind=PartnerEventKind.REGISTRATION,
                         currency="EUR", amount=1000, payout=5, occurred_at=START,
                         external_id="eur-registration"),
        ])
    async with container.factory() as s:
        out = await AnalyticsService(s).summary(START, END)
    assert out["revenue"] == 0.0 and out["partner_payouts"] == 5.0
    assert out["revenue_by_currency"] == [{"currency": "USD", "total": 0.0}]
    assert out["partner_payouts_by_currency"] == [{"currency": "EUR", "total": 5.0}]


def test_money_totals_adds_decimal_values_before_rounding():
    from sim.services.analytics import money_totals

    events = [PartnerEvent(currency=" usd ", amount=0.1),
              PartnerEvent(currency="USD", amount=0.2)]
    assert money_totals(events, "amount") == (0.3, [{"currency": "USD", "total": 0.3}])
    assert [event.currency for event in events] == [" usd ", "USD"]
