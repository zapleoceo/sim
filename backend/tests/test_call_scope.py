"""sim.call_scope: the idempotency scope of Broker calls (docs/broker.md §2b)."""
from __future__ import annotations

import asyncio

from sim.call_scope import call_scope, current_scope


def test_scopes_nest_and_reset() -> None:
    assert current_scope() is None
    with call_scope("job-7") as job:
        assert current_scope() is job and not job.sequential
        with call_scope("reply-3-41", sequential=True) as turn:
            assert current_scope() is turn
            assert turn.name == "job-7/reply-3-41" and turn.sequential
            assert [turn.next_call(), turn.next_call()] == [1, 2]
        assert current_scope() is job and job.calls == 0
    assert current_scope() is None


async def test_each_asyncio_task_keeps_its_own_scope() -> None:
    async def work(name: str) -> str | None:
        with call_scope(name):
            await asyncio.sleep(0)
            scope = current_scope()
            return scope.name if scope is not None else None

    assert await asyncio.gather(work("job-1"), work("job-2")) == ["job-1", "job-2"]
    assert current_scope() is None


async def test_a_child_task_shares_the_counter_of_its_scope() -> None:
    """`asyncio.wait_for` runs the call in a task: the copied context holds the same scope."""
    with call_scope("reply-1-2", sequential=True) as turn:
        async def call() -> int:
            scope = current_scope()
            assert scope is not None
            return scope.next_call()

        assert await asyncio.wait_for(call(), 1) == 1
        assert turn.next_call() == 2
