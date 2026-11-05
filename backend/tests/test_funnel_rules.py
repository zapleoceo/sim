from __future__ import annotations

import pytest

from sim.domain.enums import ClientStatus, Stage
from sim.domain.funnel import Actor, FunnelRuleError, next_stage, status_on_inbound

S = Stage


@pytest.mark.parametrize(
    ("current", "proposed", "actor", "expected"),
    [
        (S.FIRST_CONTACT, S.ENGAGED, Actor.AI, S.ENGAGED),
        (S.INTERESTED, S.ENGAGED, Actor.AI, S.INTERESTED),            # backwards ignored
        (S.ENGAGED, S.HANDED_TO_PARTNER, Actor.AI, S.HANDED_TO_PARTNER),
        (S.HANDED_TO_PARTNER, S.ENGAGED, Actor.OPERATOR, S.ENGAGED),  # operator may go back
        (S.INTERESTED, S.PAID, Actor.PARTNER, S.PAID),
        (S.PAID, S.REGISTERED, Actor.PARTNER, S.PAID),
    ],
)
def test_allowed(current, proposed, actor, expected):
    assert next_stage(current, proposed, actor) == expected


@pytest.mark.parametrize(
    ("current", "proposed", "actor"),
    [
        (S.ENGAGED, S.REGISTERED, Actor.AI),
        (S.ENGAGED, S.PAID, Actor.OPERATOR),
        (S.ENGAGED, S.ENGAGED, Actor.PARTNER),
        (S.REGISTERED, S.ENGAGED, Actor.OPERATOR),
    ],
)
def test_forbidden(current, proposed, actor):
    with pytest.raises(FunnelRuleError):
        next_stage(current, proposed, actor)


def test_refused_stays_refused_on_inbound():
    assert status_on_inbound(ClientStatus.REFUSED) is ClientStatus.REFUSED
    assert status_on_inbound(ClientStatus.NO_RESPONSE) is ClientStatus.ACTIVE
