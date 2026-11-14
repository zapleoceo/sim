"""AI pause is visible and recoverable: a message into a paused dialog is journaled, lifting the
pause answers what was left unanswered (once, unless stale), and a long silence is a warning."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, Message, TraceEvent
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.domain.enums import Author, Delivery
from sim.services import jobkinds, reply_journal, tracing
from sim.services.app_settings import BY_KEY, AppSettingsService
from sim.worker.loop import Worker
from tests.conftest import OWNER, decision_json, inbound, login
from tests.test_ingest import ingest


def ago(minutes: float):
    return utcnow() - timedelta(minutes=minutes)


async def make_due(container) -> None:
    async with transaction(container.factory) as s:
        for job in (await s.scalars(select(Job).where(Job.status == "pending"))).all():
            job.run_at = utcnow() - timedelta(seconds=1)


async def watch_steps(container, name: str | None = None) -> list[TraceEvent]:
    async with container.factory() as s:
        rows = (await s.scalars(select(TraceEvent).where(TraceEvent.kind == tracing.WATCH)
                                .order_by(TraceEvent.id))).all()
    return [e for e in rows if name is None or e.name == name]


async def pending_replies(container) -> list[Job]:
    async with container.factory() as s:
        return list((await s.scalars(select(Job).where(
            Job.kind == jobkinds.REPLY, Job.status == "pending"))).all())


async def set_paused(container, paused: bool = True) -> int:
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        dialog.ai_paused = paused
        return dialog.id


async def toggle(api, dialog_id: int, paused: bool) -> dict:
    csrf = getattr(api, "sim_csrf", None) or await login(api, *OWNER)
    api.sim_csrf = csrf                                       # one login per test client
    headers = {"X-CSRF-Token": csrf}
    resp = await api.post(f"/api/dialogs/{dialog_id}/ai", json={"paused": paused}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_a_message_into_a_paused_dialog_is_journaled_and_gets_no_reply_job(container, world):
    await ingest(container, world.channel.id, inbound("Привет", ext="1", at=ago(30)))
    dialog_id = await set_paused(container)
    async with transaction(container.factory) as s:
        for job in (await s.scalars(select(Job))).all():
            await s.delete(job)
    res = await ingest(container, world.channel.id, inbound("Пришли фото", ext="2", at=ago(1)))
    assert res.message is not None and not res.reply_queued
    assert await pending_replies(container) == []           # the pause still stops the AI
    (step,) = await watch_steps(container)
    assert step.name == reply_journal.PAUSED == "ответ не создан: диалог на паузе ИИ"
    assert step.status == tracing.SKIPPED
    assert (step.dialog_id, step.message_id) == (dialog_id, res.message.id)
    assert step.detail == {"reason": "ai_paused", "source": "inbound"}


async def test_a_reply_job_that_meets_a_pause_is_journaled_not_silently_skipped(
    container, world, fake_llm
):
    res = await ingest(container, world.channel.id, inbound("Привет", at=ago(1)))
    dialog_id = await set_paused(container)
    await make_due(container)
    await Worker(container).tick()
    assert fake_llm.calls == []                              # nothing was generated
    async with container.factory() as s:
        assert [m.author for m in (await s.scalars(select(Message))).all()] == [Author.CLIENT]
        job_step = (await s.scalars(select(TraceEvent).where(TraceEvent.kind == tracing.JOB))).one()
    assert job_step.detail["outcome"] == "skipped" and job_step.detail["reason"] == "ai_paused"
    (step,) = await watch_steps(container, reply_journal.PAUSED)
    assert (step.dialog_id, step.message_id) == (dialog_id, res.message.id)
    assert step.detail["source"] == "reply_job" and step.trace_id == job_step.trace_id


async def test_dialog_card_lists_the_unanswered_messages(api, container, world):
    await ingest(container, world.channel.id, inbound("Пришли фото", ext="1", at=ago(20)))
    second = await ingest(container, world.channel.id, inbound("Пришли кружочек", ext="2", at=ago(10)))
    dialog_id = await set_paused(container)
    await login(api, *OWNER)
    dialog = (await api.get(f"/api/dialogs/{dialog_id}")).json()["dialog"]
    assert dialog["ai_paused"] is True
    assert dialog["unanswered"]["count"] == 2 and dialog["unanswered"]["since"]
    assert dialog["unanswered"]["message_ids"][-1] == second.message.id


async def test_resume_answers_everything_left_unanswered_with_one_reply(
    api, container, world, fake_llm, connector
):
    await ingest(container, world.channel.id, inbound("Привет", ext="1", at=ago(40)))
    dialog_id = await set_paused(container)
    await make_due(container)
    await Worker(container).tick()                           # the first reply job met the pause
    await ingest(container, world.channel.id, inbound("Пришли фото", ext="2", at=ago(20)))
    last = await ingest(container, world.channel.id, inbound("Пришли кружочек", ext="3", at=ago(10)))
    assert await pending_replies(container) == []

    resumed = await toggle(api, dialog_id, paused=False)
    assert resumed["ai_paused"] is False
    assert resumed["recovery"] == {"status": "queued", "messages": 3}
    (job,) = await pending_replies(container)                # ONE reply for the whole burst
    assert job.payload == {"dialog_id": dialog_id}
    (step,) = await watch_steps(container, reply_journal.RECOVERED)
    assert step.status == tracing.OK and step.message_id == last.message.id
    assert step.detail == {"unanswered": 3}

    fake_llm.replies = [decision_json(reply="Сейчас пришлю")]
    worker = Worker(container)
    await make_due(container)
    await worker.tick()                                      # the one reply, with all three in view
    prompt = " ".join(str(m["content"]) for m in fake_llm.gen_calls[-1][0])
    assert all(text in prompt for text in ("Привет", "Пришли фото", "Пришли кружочек"))
    async with container.factory() as s:
        ai = (await s.scalars(select(Message).where(Message.author == Author.AI))).all()
    assert len(ai) == 1 and len(fake_llm.gen_calls) == 1
    await make_due(container)
    await worker.tick()                                      # its send
    assert connector.port.sent == [("chat-1", "Сейчас пришлю")]


async def test_resume_does_not_answer_a_stale_message_and_says_so(api, container, world, fake_llm):
    res = await ingest(container, world.channel.id, inbound("Вы тут?", at=ago(181)))
    dialog_id = await set_paused(container)                  # its reply job is still pending

    resumed = await toggle(api, dialog_id, paused=False)
    assert resumed["ai_paused"] is False and resumed["recovery"] == {"status": "stale", "messages": 1}
    assert await pending_replies(container) == []            # the leftover job is cancelled too
    (step,) = await watch_steps(container, reply_journal.STALE)
    assert step.name == "пропущено, устарело" and step.status == tracing.SKIPPED
    assert (step.dialog_id, step.message_id) == (dialog_id, res.message.id)
    assert step.detail["window_min"] == 120 and step.detail["age_min"] >= 180
    assert fake_llm.calls == []

    # The window is an owner setting: a wider one lets the same message be answered.
    assert BY_KEY["pause_recovery_window_min"].default == 120
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set("pause_recovery_window_min", 240, None)
    await set_paused(container)
    assert (await toggle(api, dialog_id, paused=False))["recovery"]["status"] == "queued"
    assert len(await pending_replies(container)) == 1


async def test_a_reply_cancelled_by_the_pause_is_not_an_answer(
    api, container, world, fake_llm, connector
):
    """The AI answer was queued, the operator paused before it went out: the client still waits."""
    await ingest(container, world.channel.id, inbound("Привет", at=ago(1)))
    fake_llm.replies = [decision_json(reply="Первый ответ")]
    worker = Worker(container)
    await make_due(container)
    await worker.tick()                                      # the AI message waits for its send
    async with container.factory() as s:
        dialog_id = (await s.scalars(select(Dialog))).unique().one().id
    await toggle(api, dialog_id, paused=True)
    async with container.factory() as s:
        ai = (await s.scalars(select(Message).where(Message.author == Author.AI))).one()
    assert ai.delivery is Delivery.CANCELLED

    assert (await toggle(api, dialog_id, paused=False))["recovery"] == {"status": "queued", "messages": 1}
    fake_llm.replies = [decision_json(reply="Второй ответ")]
    await make_due(container)
    await worker.tick()
    await make_due(container)
    await worker.tick()
    assert connector.port.sent == [("chat-1", "Второй ответ")]


async def test_resume_after_the_operator_answered_recovers_nothing(api, container, world):
    await ingest(container, world.channel.id, inbound("Привет", at=ago(5)))
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        await container.outbound(s).send_manual(dialog, 1, "Здравствуйте, это оператор")
        dialog_id = dialog.id
    resumed = await toggle(api, dialog_id, paused=False)
    assert resumed["recovery"] == {"status": "none", "messages": 0}
    assert await pending_replies(container) == [] and await watch_steps(container) == []


async def test_a_long_silence_with_the_ai_on_is_one_warning_per_message(container, world):
    waiting = await ingest(container, world.channel.id,
                           inbound("Алло?", ext="a", thread="chat-a", user="ua", at=ago(10)))
    await ingest(container, world.channel.id,
                 inbound("Только что", ext="b", thread="chat-b", user="ub", at=ago(1)))
    paused = await ingest(container, world.channel.id,
                          inbound("На паузе", ext="c", thread="chat-c", user="uc", at=ago(10)))
    async with transaction(container.factory) as s:
        (await s.get(Dialog, paused.dialog.id)).ai_paused = True

    worker = Worker(container)
    assert await worker.watch_unanswered() == 1
    (step,) = await watch_steps(container, reply_journal.OVERDUE)
    assert step.status == tracing.WARNING
    assert (step.dialog_id, step.message_id) == (waiting.dialog.id, waiting.message.id)
    assert step.detail["threshold_s"] == 300 and step.detail["waiting_s"] >= 600
    assert step.detail["unanswered"] == 1
    assert await worker.watch_unanswered() == 0             # not repeated on the next sweep

    # Answered (even if the send is still queued): no longer waiting.
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, waiting.dialog.id)
        await container.outbound(s).queue_ai(dialog, "Здравствуйте!", None)
    async with transaction(container.factory) as s:
        assert await container.unanswered(s).of(waiting.dialog.id) == []


async def test_the_worker_loop_runs_the_watch_on_its_own_interval(container):
    worker = Worker(container)
    worker.maybe_start_watch(1000.0)
    first = worker.watch_task
    assert first is not None and await first == 0
    worker.maybe_start_watch(1000.0 + container.settings.unanswered_watch_interval_s - 1)
    assert worker.watch_task is first                        # not due yet
    worker.maybe_start_watch(1000.0 + container.settings.unanswered_watch_interval_s)
    assert worker.watch_task is not first
    await asyncio.gather(worker.watch_task)
