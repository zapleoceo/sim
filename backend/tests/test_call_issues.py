"""Technical call incidents do not authorize handoff; all transports are synthetic."""
from datetime import timedelta

import pytest

from sim.adapters.db.models import Dialog
from sim.adapters.db.session import transaction
from sim.ports.live_voice import LiveVoiceError
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


async def test_call_issue_context_is_scoped_recent_and_not_an_instruction(container, world):
    did = await chatting_world(container, world)
    now = container.clock()
    await service(container, FakeLive()).record_issue(did, "provider_open_failed")
    async with container.factory() as s:
        journal = CommunicationJournal(s, clock=lambda: now)
        lines = await journal.context_lines(did, now - timedelta(days=2))
        assert any("Не удалось начать" in line for line in lines)
        assert any("Не выдумывай" in line and "Не повторяй" in line for line in lines)
        assert not await journal.context_lines(did + 1000, now - timedelta(days=2))
        later = CommunicationJournal(s, clock=lambda: now + timedelta(days=2))
        assert not any("голосовой разговор" in line for line in
                       await later.context_lines(did, now - timedelta(days=2)))
        assert any(e.kind == CALL_ISSUE for e in await later.feed(did))
        conv = service(container, FakeLive()).conversation(s)
        dialog = await conv.dialogs.get(did)
        persona = await conv.personas.get(dialog.client.persona_id)
        prompt = await conv.call_prompt(dialog, persona)
        assert "Не удалось начать голосовой разговор" in prompt


async def test_provider_refusal_journals_without_pausing(container, world):
    did = await chatting_world(container, world)
    outcome = await service(container, FakeLive(error=LiveVoiceError("synthetic"))).handle(
        world.channel.id, FakeCall())
    assert outcome.status == "refused"
    async with container.factory() as s:
        d = await s.get(Dialog, did)
        assert not d.ai_paused and not d.needs_operator
        assert (await CommunicationJournal(s).feed(did))[-1].kind == CALL_ISSUE
