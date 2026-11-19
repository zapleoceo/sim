"""Technical call incidents do not authorize handoff; all transports are synthetic."""
from datetime import timedelta

import pytest

from sim.adapters.db.models import Dialog
from sim.adapters.db.session import transaction
from sim.ports.live_voice import LiveVoiceError, LiveVoiceSummary
from sim.services.journal import CALL_ISSUE, CommunicationJournal
from tests.test_calls import FakeCall, FakeLive, FakeSession, chatting_world, service


@pytest.mark.parametrize("paused,operator", [(False, False), (True, False), (False, True), (True, True)])
async def test_record_failure_preserves_existing_policy(container, world, paused, operator):
    did = await chatting_world(container, world)
    async with transaction(container.factory) as s:
        dialog = await s.get(Dialog, did)
        dialog.ai_paused, dialog.needs_operator = paused, operator
    await service(container, FakeLive()).record(did, await FakeSession().finish(),
        container.clock(), turns=[], failure="synthetic_guard_timeout")
    async with container.factory() as s:
        dialog = await s.get(Dialog, did)
        assert (dialog.ai_paused, dialog.needs_operator) == (paused, operator)
        events = await CommunicationJournal(s).feed(did)
        assert events[-1].kind == CALL_ISSUE
        assert not any(e.kind == "handoff" for e in events)


async def test_call_issue_stays_in_audit_but_out_of_model_context(container, world):
    did = await chatting_world(container, world)
    now = container.clock()
    await service(container, FakeLive()).record_unanswered(did, "", "provider_open_failed")
    async with container.factory() as s:
        journal = CommunicationJournal(s, clock=lambda: now)
        assert not await journal.context_lines(did, now - timedelta(days=2))
        assert any(e.kind == CALL_ISSUE for e in await journal.feed(did))
        conv = service(container, FakeLive()).conversation(s)
        dialog = await conv.dialogs.get(did)
        persona = await conv.personas.get(dialog.client.persona_id)
        prompt = await conv.call_prompt(dialog, persona)
        assert "Не удалось начать голосовой разговор" not in prompt


async def test_recent_answered_call_only_projects_the_conversation_fact(container, world):
    did = await chatting_world(container, world)
    now = container.clock()
    await service(container, FakeLive()).record(did, await FakeSession().finish(), now,
        turns=[], failure="synthetic_internal_transport_detail")
    async with container.factory() as s:
        journal = CommunicationJournal(s, clock=lambda: now)
        (line,) = await journal.context_lines(did, now - timedelta(days=2))
        assert line.endswith("В звонке часть ответа могла не прозвучать.")
        assert "технической проблемой" not in line and "synthetic_internal" not in line
        assert not await journal.context_lines(did + 1000, now - timedelta(days=2))
        later = CommunicationJournal(s, clock=lambda: now + timedelta(hours=25))
        assert not await later.context_lines(did, now - timedelta(days=2))
        assert any(event.kind == CALL_ISSUE for event in await later.feed(did))


async def test_provider_refusal_journals_without_pausing(container, world):
    did = await chatting_world(container, world)
    outcome = await service(container, FakeLive(error=LiveVoiceError("synthetic"))).handle(
        world.channel.id, FakeCall())
    assert outcome.status == "refused"
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator
        assert (await CommunicationJournal(s).feed(did))[-1].kind == CALL_ISSUE


async def test_provider_finish_failure_still_records_issue_without_pause(container, world):
    did = await chatting_world(container, world)

    class BrokenFinish(FakeSession):
        async def finish(self):
            raise LiveVoiceError("synthetic finish error")

    call = FakeCall(frames=1)
    outcome = await service(container, FakeLive(BrokenFinish())).handle(world.channel.id, call)
    assert call.hung_up and outcome.message_ids
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator
        assert (await CommunicationJournal(s).feed(did))[-1].kind == CALL_ISSUE


@pytest.mark.parametrize("reason", ["no_summary", "provider_error", "transport_error", "summary_timeout"])
async def test_failed_live_summary_is_journaled_without_handoff(container, world, reason):
    did = await chatting_world(container, world)
    await service(container, FakeLive(FakeSession(summary=LiveVoiceSummary(end_reason=reason)))).handle(
        world.channel.id, FakeCall(frames=1))
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator
        assert (await CommunicationJournal(s).feed(did))[-1].kind == CALL_ISSUE
