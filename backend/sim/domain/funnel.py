"""Funnel rules — pure functions, no IO.

Who may move a client where is a business invariant, not a UI convenience:
- the AI moves forward only, and never past HANDED_TO_PARTNER;
- an operator may move freely among the AI-reachable stages;
- REGISTERED and PAID come only from partner data.
"""
from __future__ import annotations

from enum import StrEnum

from sim.domain.enums import ClientStatus, Stage

STAGE_ORDER: tuple[Stage, ...] = tuple(Stage)
AI_MAX_STAGE = Stage.HANDED_TO_PARTNER
PARTNER_ONLY: frozenset[Stage] = frozenset({Stage.REGISTERED, Stage.PAID})


class Actor(StrEnum):
    AI = "ai"
    OPERATOR = "operator"
    PARTNER = "partner"
    SYSTEM = "system"


class FunnelRuleError(ValueError):
    """A transition the actor is not allowed to make."""


def rank(stage: Stage) -> int:
    return STAGE_ORDER.index(stage)


def next_stage(current: Stage, proposed: Stage, actor: Actor) -> Stage:
    """The stage the client ends up in when `actor` proposes `proposed`.

    Raises FunnelRuleError for a forbidden move; returns `current` for a no-op (an AI
    proposing a stage behind the current one is ignored, not an error — models restate
    earlier stages all the time)."""
    if actor is Actor.PARTNER:
        if proposed not in PARTNER_ONLY:
            raise FunnelRuleError(f"partner may only set {sorted(PARTNER_ONLY)}")
        return proposed if rank(proposed) > rank(current) else current
    if proposed in PARTNER_ONLY:
        raise FunnelRuleError(f"{proposed} is confirmed only by partner data")
    if actor is Actor.AI:
        if rank(current) >= rank(AI_MAX_STAGE):
            return current
        capped = proposed if rank(proposed) <= rank(AI_MAX_STAGE) else AI_MAX_STAGE
        return capped if rank(capped) > rank(current) else current
    if current in PARTNER_ONLY:
        raise FunnelRuleError("a partner-confirmed stage cannot be changed by hand")
    return proposed


def status_on_inbound(status: ClientStatus) -> ClientStatus:
    """A new message from the client returns them to the dialog — except an explicit refusal
    stays a refusal until an operator reopens it (a refused client writing "stop" again must
    not restart the follow-up ladder)."""
    if status in (ClientStatus.NO_RESPONSE, ClientStatus.PAUSED):
        return ClientStatus.ACTIVE
    return status


def advance_by_ai(current: Stage, proposed: Stage | None) -> Stage:
    """The stage after an AI proposal; a forbidden or absent proposal changes nothing. For the
    states that have no client row (admin simulations): the same rules as a real client."""
    if proposed is None:
        return current
    try:
        return next_stage(current, proposed, Actor.AI)
    except FunnelRuleError:
        return current
