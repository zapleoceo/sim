"""Call memory uses the chat window and saved data only; no provider calls."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from sim.adapters.db.models import Dialog, DialogSummary, Persona
from sim.adapters.db.session import transaction
from sim.domain.enums import Delivery
from sim.services.app_settings import AppSettingsService
from sim.services.call_prompt import CALL_HISTORY_CHARS, CALL_HISTORY_TRIMMED, chat_transcript
from sim.services.journal import CHAT_CLEARED, NOTES, CommunicationJournal
from sim.services.prompt import Turn
from tests.conftest import inbound
from tests.test_ingest import ingest
from tests.test_journal_context import NOW, OLD, message, seed


async def call_prompt(container, world, dialog_id):
    async with container.factory() as s:
        dialog = await s.get(Dialog, dialog_id)
        persona = await s.get(Persona, world.persona.id)
        return await container.conversation(s).call_prompt(dialog, persona)


@pytest.mark.parametrize("saved", [False, True])
async def test_calls_read_the_window_saved_summary_and_journal_without_generating(
    container, world, fake_llm, saved,
):
    dialog_id = await seed(container, world, fresh=45)
    async with transaction(container.factory) as s:
        if saved:
            s.add(DialogSummary(dialog_id=dialog_id, text="Согласовали вторник",
                                covered_until=OLD, messages_covered=1))
        log = CommunicationJournal(s)
        await log.record(dialog_id, NOTES, new="до очистки")
        await log.record(dialog_id, CHAT_CLEARED)
        await log.record(dialog_id, NOTES, new="после очистки")
        # Neither a blocked draft nor an unsent draft was seen by the caller.
        blocked = message(dialog_id, "BLOCKED-SECRET", NOW, client=False)
        blocked.guard_verdict = "blocked"
        unsent = message(dialog_id, "UNSENT-SECRET", NOW, client=False)
        unsent.delivery = Delivery.QUEUED
        s.add_all([blocked, unsent])
    prompt = await call_prompt(container, world, dialog_id)
    assert all(f"свежее-{i:02d}" in prompt for i in range(45))
    assert "свежий вопрос" in prompt and "старое-" not in prompt
    assert ("Согласовали вторник" in prompt) is saved
    assert "после очистки" in prompt and "до очистки" not in prompt
    assert "BLOCKED-SECRET" not in prompt and "UNSENT-SECRET" not in prompt
    assert not fake_llm.calls
    async with container.factory() as s:
        row = await s.get(DialogSummary, dialog_id)
        assert (row.text if row else None) == ("Согласовали вторник" if saved else None)


async def test_call_window_respects_token_budget_and_does_not_read_other_dialogs(
    container, world, fake_llm,
):
    dialog_id = await seed(container, world, size=300)
    await ingest(container, world.channel.id,
                 inbound("OTHER-DIALOG-SECRET", ext="other", user="other", thread="other", at=NOW))
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set("context_token_limit", 500, None)
        other = await s.scalar(select(Dialog).where(Dialog.id != dialog_id))
        s.add(DialogSummary(dialog_id=other.id, text="OTHER-SUMMARY-SECRET", messages_covered=1))
        await CommunicationJournal(s).record(other.id, NOTES, new="OTHER-JOURNAL-SECRET")
    prompt = await call_prompt(container, world, dialog_id)
    assert "свежий вопрос" in prompt and "свежее-09" in prompt
    assert "свежее-00" not in prompt and "ИСТОРИЯ ОБРЕЗАНА" in prompt
    assert "OTHER-" not in prompt and not fake_llm.calls


async def test_call_window_keeps_the_chat_floor_after_a_long_silence(container, world, fake_llm):
    dialog_id = await seed(container, world)
    container.clock = lambda: NOW + timedelta(days=9)
    prompt = await call_prompt(container, world, dialog_id)
    assert "свежее-01" in prompt and "свежий вопрос" in prompt
    assert "свежее-00" not in prompt and "старое-" not in prompt
    assert not fake_llm.calls


def test_voice_budget_keeps_latest_turns_and_discloses_truncation():
    history = [Turn("user" if i % 2 else "assistant", f"turn-{i:03d} " + "x" * 1490)
               for i in range(100)]
    text = chat_transcript(history)
    assert text.startswith(CALL_HISTORY_TRIMMED)
    assert len(text) <= CALL_HISTORY_CHARS + len(CALL_HISTORY_TRIMMED) + 1
    assert "turn-099" in text and "turn-000" not in text
    assert text.index("turn-098") < text.index("turn-099")


def test_more_than_thirty_short_turns_are_not_silently_dropped():
    history = [Turn("user" if i % 2 else "assistant", f"turn-{i:03d}") for i in range(80)]
    text = chat_transcript(history)
    assert "turn-000" in text and "turn-079" in text and CALL_HISTORY_TRIMMED not in text


def test_long_consecutive_client_turns_keep_the_latest_question():
    history = [Turn("user", "old-first " + "x" * 1600),
               Turn("user", "latest-question")]
    text = chat_transcript(history)
    assert CALL_HISTORY_TRIMMED in text and "latest-question" in text
    assert "old-first" not in text
