"""Context budget (docs/context.md §3): tokens are estimated as characters / 3.5. When today's
timeline does not fit, the newest lines are kept and the oldest part is later folded into the
compressed morning (docs/memory.md): events up to the boundary are told by it, the rest stay
verbatim."""
from __future__ import annotations

import math
from collections.abc import Sequence

from sim.services.context.render import TimelineLine

CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def lines_tokens(lines: Sequence[TimelineLine]) -> int:
    # +1 per line for the newline that joins them.
    return sum(estimate_tokens(line.text) + 1 for line in lines)


def split_by_boundary[T](items: Sequence[T], ids: Sequence[int],
                         until_id: int | None) -> tuple[list[T], list[T]]:
    """(covered by the summary, verbatim timeline) for a summary boundary (inclusive)."""
    if until_id is None:
        return [], list(items)
    summarized = [item for item, i in zip(items, ids, strict=True) if i <= until_id]
    rest = [item for item, i in zip(items, ids, strict=True) if i > until_id]
    return summarized, rest


def fit_newest(lines: Sequence[TimelineLine], budget_tokens: int) -> list[TimelineLine]:
    """The newest lines that fit the budget (an emergency cut until the memory task compresses
    the timeline). The newest line is always kept."""
    kept: list[TimelineLine] = []
    used = 0
    for line in reversed(lines):
        cost = estimate_tokens(line.text) + 1
        if kept and used + cost > budget_tokens:
            break
        kept.append(line)
        used += cost
    kept.reverse()
    return kept


def compression_boundary(lines: Sequence[TimelineLine], budget_tokens: int,
                         keep_recent: int) -> int | None:
    """Event id up to which (inclusive) the timeline should be folded into the summary, or None
    while everything fits. The newest `keep_recent` lines always stay verbatim."""
    if lines_tokens(lines) <= budget_tokens or len(lines) <= keep_recent:
        return None
    older = lines[: len(lines) - keep_recent] if keep_recent > 0 else lines
    return older[-1].event_id if older else None
