from __future__ import annotations

import pytest

from sim.domain.enums import EventKind
from sim.services.context.budget import (
    compression_boundary,
    estimate_tokens,
    fit_newest,
    lines_tokens,
    split_by_boundary,
)
from sim.services.context.render import TimelineLine


def lines(n: int, size: int = 35) -> list[TimelineLine]:
    return [TimelineLine(event_id=i, kind=EventKind.MESSAGE_IN, text="x" * size)
            for i in range(1, n + 1)]


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 7) == 2
    assert estimate_tokens("a" * 8) == 3
    assert lines_tokens(lines(3)) == 3 * (10 + 1)


def test_fit_newest_keeps_the_newest_lines() -> None:
    timeline = lines(10)                      # 11 tokens each
    kept = fit_newest(timeline, 33)
    assert [line.event_id for line in kept] == [8, 9, 10]
    # The newest line is kept even when it alone is over the budget.
    assert [line.event_id for line in fit_newest(lines(2, size=3500), 10)] == [2]


def test_compression_boundary() -> None:
    timeline = lines(10)                      # 110 tokens
    assert compression_boundary(timeline, 200, keep_recent=3) is None
    assert compression_boundary(timeline, 50, keep_recent=3) == 7
    assert compression_boundary(timeline, 50, keep_recent=10) is None
    assert compression_boundary(timeline, 50, keep_recent=0) == 10


@pytest.mark.parametrize("until", [None, 0, 1, 5, 9, 10, 99])
def test_summary_and_timeline_cover_every_event_once(until: int | None) -> None:
    ids = [3, 4, 7, 8, 9, 10, 15]
    summarized, rest = split_by_boundary(ids, ids, until)
    assert sorted(summarized + rest) == ids
    assert not set(summarized) & set(rest)
    if until is not None:
        assert all(i <= until for i in summarized)
        assert all(i > until for i in rest)


def test_folding_then_splitting_keeps_every_event() -> None:
    timeline = lines(12)
    boundary = compression_boundary(timeline, 60, keep_recent=4)
    assert boundary == 8
    summarized, rest = split_by_boundary(timeline, [line.event_id for line in timeline],
                                         boundary)
    assert [line.event_id for line in summarized + rest] == list(range(1, 13))
    assert len(rest) == 4
