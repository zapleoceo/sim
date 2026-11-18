"""Incomplete memory must never acknowledge messages it did not preserve."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import DialogSummary, Message
from sim.adapters.db.session import transaction
from sim.clock import to_naive_utc
from sim.ports.llm import LLMError, LLMResult
from sim.services.app_settings import AppSettingsService
from sim.services.summary import SUMMARY_MAX, SummaryService
from tests.test_journal_context import seed

STAMP = to_naive_utc(datetime(2026, 1, 10, 12, tzinfo=UTC))
BAD = [
    LLMResult(text="partial", finish_reason="length"),
    LLMResult(text="partial", finish_reason="max_tokens"),
    LLMResult(text="partial", finish_reason=" MAX_TOKENS "),
    LLMResult(text="x" * (SUMMARY_MAX + 1), finish_reason="stop"),
]


def snapshot(row):
    if row is None:
        return None
    return (row.text, row.covered_message_id, row.covered_until,
            row.messages_covered, row.model, row.updated_at)


async def saved(container, dialog_id):
    async with container.factory() as session:
        return snapshot(await session.get(DialogSummary, dialog_id))


async def setup(container, world, *, existing=True, old=14):
    dialog_id = await seed(container, world, old=old)
    async with transaction(container.factory) as session:
        rows = list((await session.scalars(
            select(Message).where(Message.dialog_id == dialog_id).order_by(Message.id),
        )).all())
        # Fixed dates, independent of machine time and the seed helper's defaults.
        for index, row in enumerate(rows):
            row.created_at = STAMP - timedelta(days=5 if index < old else 0) + timedelta(seconds=index)
        if existing:
            session.add(DialogSummary(
                dialog_id=dialog_id, text="Complete prior memory", covered_message_id=rows[0].id,
                covered_until=rows[0].created_at, messages_covered=1,
                model="prior-model", updated_at=STAMP - timedelta(days=1),
            ))
    return dialog_id


def service(session, llm):
    return SummaryService(session, llm, AppSettingsService(session), clock=lambda: STAMP + timedelta(hours=1))


@pytest.mark.parametrize("answer", BAD)
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("path", ["reply", "refresh"])
async def test_incomplete_result_preserves_all_memory_fields(
    container, world, fake_llm, answer, existing, path,
):
    dialog_id = await setup(container, world, existing=existing)
    before = await saved(container, dialog_id)
    fake_llm.summary_replies = [answer]
    # Catch inside the transaction and commit: safety must not depend on rollback.
    async with transaction(container.factory) as session:
        svc = service(session, fake_llm)
        if path == "reply":
            cfg = await svc.settings.load()
            window = await svc.load_window(dialog_id, cfg)
            assert snapshot(await svc.for_reply(dialog_id, window, cfg)) == before
        else:
            with pytest.raises(LLMError, match="summary response incomplete"):
                await svc.refresh(dialog_id)
    assert await saved(container, dialog_id) == before
    assert len(fake_llm.summary_calls) == 1  # no retry within the four-batch refresh


async def test_explicit_retry_saves_bounded_result_once(container, world, fake_llm):
    dialog_id = await setup(container, world)
    before = await saved(container, dialog_id)
    fake_llm.summary_replies = [BAD[0], LLMResult(text="z" * SUMMARY_MAX, model="complete")]
    for attempt in range(2):
        async with transaction(container.factory) as session:
            svc = service(session, fake_llm)
            if attempt == 0:
                with pytest.raises(LLMError):
                    await svc.refresh(dialog_id)
            else:
                row, pending = await svc.refresh(dialog_id)
                assert row.text == "z" * SUMMARY_MAX and pending == 0
        if attempt == 0:
            assert await saved(container, dialog_id) == before
    complete = await saved(container, dialog_id)
    assert complete[1] > before[1] and complete[2] > before[2]
    assert complete[3:] == (14, "complete", STAMP + timedelta(hours=1))
    async with transaction(container.factory) as session:
        await service(session, fake_llm).refresh(dialog_id)
    assert await saved(container, dialog_id) == complete
    assert len(fake_llm.summary_calls) == 2  # repeated refresh has nothing to generate


async def test_later_bad_batch_rolls_back_manual_refresh(container, world, fake_llm):
    dialog_id = await setup(container, world, old=130)
    before = await saved(container, dialog_id)
    fake_llm.summary_replies = ["Complete first batch", BAD[1]]
    with pytest.raises(LLMError, match="summary response incomplete"):
        async with transaction(container.factory) as session:
            await service(session, fake_llm).refresh(dialog_id)
    assert await saved(container, dialog_id) == before
    assert len(fake_llm.summary_calls) == 2


async def test_late_incomplete_result_cannot_overwrite_concurrent_success(container, world, fake_llm):
    dialog_id = await setup(container, world)
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedIncomplete:
        async def chat(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return BAD[0]

    async def late():
        async with transaction(container.factory) as session:
            # Deliberately commit after handling failure, even with the stale row loaded.
            with pytest.raises(LLMError, match="summary response incomplete"):
                await service(session, DelayedIncomplete()).refresh(dialog_id)

    task = asyncio.create_task(late())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        fake_llm.summary_replies = ["Concurrent complete memory"]
        async with transaction(container.factory) as session:
            await service(session, fake_llm).refresh(dialog_id)
        complete = await saved(container, dialog_id)
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    assert await saved(container, dialog_id) == complete
    assert complete[0] == "Concurrent complete memory"
