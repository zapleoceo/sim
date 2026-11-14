"""A reply as several short messages: the model separates them with a blank line, code sends
them one by one with a typing pause and «печатает…» shown for it (channels) or stores them as
separate rows (simulations)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from sim.adapters.channels.capabilities import CANCEL, TYPING, Capability
from sim.adapters.db.models import AdminUser, Dialog, Job, Message, Persona, SimMessage
from sim.adapters.db.session import transaction
from sim.domain.enums import Delivery, Direction, Role
from sim.services import jobkinds, outbound
from sim.services.humanize import (
    MAX_BUBBLES,
    TYPING_CHARS_PER_S,
    TYPING_MAX_S,
    TYPING_MIN_S,
    bubbles,
    typing_pause_s,
)
from sim.worker.handlers import run_send
from tests.conftest import decision_json
from tests.test_conversation import run_reply, start


def test_bubbles_split_on_blank_lines_and_cap_the_count():
    assert bubbles("привет)\n\nкак ты?") == ["привет)", "как ты?"]
    assert bubbles("одна строка\nвторая строка") == ["одна строка\nвторая строка"]
    assert bubbles("а\n \nб\n\nв\n\nг") == ["а", "б", "в\nг"]
    assert len(bubbles("\n\n".join("x" * 10))) == MAX_BUBBLES
    assert bubbles("  ") == [""]


def test_typing_pause_grows_with_length_within_bounds():
    assert typing_pause_s("ок") == TYPING_MIN_S
    assert typing_pause_s("x" * 1000) == TYPING_MAX_S
    assert TYPING_MIN_S < typing_pause_s("x" * 30) < TYPING_MAX_S
    # About a second per 25-30 characters, at most 6 seconds.
    assert 25 <= TYPING_CHARS_PER_S <= 30 and TYPING_MAX_S == 6.0
    assert typing_pause_s("x" * 84) == pytest.approx(84 / TYPING_CHARS_PER_S)


async def test_a_reply_goes_out_as_separate_messages_with_typing_pauses(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="ой привет)\n\nя Марина\n\nа вы откуда?")]
    await start(container, world)
    outcome = await run_reply(container)
    assert outcome.status == "sent"
    async with container.factory() as s:
        out = (await s.scalars(select(Message).where(Message.direction == Direction.OUT)
                               .order_by(Message.id))).all()
        sends = (await s.scalars(select(Job).where(Job.kind == jobkinds.SEND).order_by(Job.id))).all()
    assert [m.text for m in out] == ["ой привет)", "я Марина", "а вы откуда?"]
    assert out[0].model == "fake-model" and out[1].model is None  # cost counted once
    # The first part has no pause. A next part's job starts when the part before it is due and
    # holds its own pause (`typing_s`) itself, with «печатает…» shown.
    assert [j.payload.get("typing_s", 0.0) for j in sends] == [
        0.0, typing_pause_s("я Марина"), typing_pause_s("а вы откуда?")]
    run_at = [j.run_at for j in sends]
    assert (run_at[1] - run_at[0]).total_seconds() == pytest.approx(0.0, abs=0.5)
    assert (run_at[2] - run_at[1]).total_seconds() == pytest.approx(typing_pause_s("я Марина"), abs=0.5)


async def test_a_simulation_stores_each_short_message(container, world, fake_llm):
    fake_llm.replies = [decision_json(reply="ага)\n\nа что случилось?")]
    async with transaction(container.factory) as s:
        admin = AdminUser(telegram_id=4242, role=Role.OWNER, display_name="Дима")
        s.add(admin)
        await s.flush()
        persona = (await s.scalars(select(Persona))).first()
        sim = container.simulation(s)
        branch = await sim.start(admin, persona)
        await sim.add_client_message(branch, "привет")
        reply = await sim.reply(branch, "Тест")
        rows = (await s.scalars(select(SimMessage).where(SimMessage.role == "ai")
                                .order_by(SimMessage.id))).all()
    assert reply.status == "sent"
    assert [r.text for r in rows] == ["ага)", "а что случилось?"]


# ---- the pause before each next part, with «печатает…» for all of it ---------------------------
class Pauses:
    """Stands in for the sleep of the pause: records its length and what the chat showed and
    had received at that moment."""

    def __init__(self, port, during=None) -> None:
        self.port, self.during = port, during
        self.seen: list[tuple[float, list[tuple[str, str]], list[tuple[str, str]]]] = []

    async def __call__(self, seconds: float) -> None:
        self.seen.append((seconds, list(self.port.actions), list(self.port.sent)))
        if self.during is not None:
            await self.during()


async def queued_parts(container, world, fake_llm, connector, reply: str):
    """The SEND jobs of one reply, with a channel that can show «печатает…»."""
    connector.port.capabilities = connector.port.capabilities | {Capability.CHAT_ACTION}
    fake_llm.replies = [decision_json(reply=reply)]
    await start(container, world)
    assert (await run_reply(container)).status == "sent"
    connector.port.actions.clear()          # what was shown while the reply was being written
    async with container.factory() as s:
        return (await s.scalars(select(Job).where(Job.kind == jobkinds.SEND).order_by(Job.id))).all()


async def send(container, job: Job, **override) -> None:
    """What the worker does with the SEND job."""
    async with transaction(container.factory) as s:
        await run_send(container, s, {**job.payload, **override})


async def test_typing_is_shown_for_the_whole_pause_before_a_next_part(
    container, world, fake_llm, connector, monkeypatch
):
    jobs = await queued_parts(container, world, fake_llm, connector, "ой привет)\n\nа вы откуда?")
    pauses = Pauses(connector.port)
    monkeypatch.setattr(outbound, "sleep", pauses)

    await send(container, jobs[0])
    assert pauses.seen == [] and connector.port.actions == []     # the first part: at once
    assert connector.port.sent == [("chat-1", "ой привет)")]

    await send(container, jobs[1])
    (seconds, shown, sent), = pauses.seen
    assert seconds == typing_pause_s("а вы откуда?")
    assert shown == [("chat-1", TYPING)]                 # «печатает…» is up while she waits
    assert sent == [("chat-1", "ой привет)")]            # and the part is not out yet
    assert connector.port.actions == [("chat-1", TYPING), ("chat-1", CANCEL)]   # cleared after
    assert connector.port.sent[-1] == ("chat-1", "а вы откуда?")


async def test_a_single_message_goes_out_without_a_pause_or_a_status(
    container, world, fake_llm, connector, monkeypatch
):
    jobs = await queued_parts(container, world, fake_llm, connector, "ой привет)")
    pauses = Pauses(connector.port)
    monkeypatch.setattr(outbound, "sleep", pauses)
    assert len(jobs) == 1 and "typing_s" not in jobs[0].payload
    await send(container, jobs[0])
    assert pauses.seen == [] and connector.port.actions == []
    assert connector.port.sent == [("chat-1", "ой привет)")]


async def test_the_pause_never_outlasts_the_cap(container, world, fake_llm, connector, monkeypatch):
    jobs = await queued_parts(container, world, fake_llm, connector, "ага)\n\nсейчас расскажу")
    pauses = Pauses(connector.port)
    monkeypatch.setattr(outbound, "sleep", pauses)
    await send(container, jobs[1], typing_s=600)
    assert [seconds for seconds, _, _ in pauses.seen] == [TYPING_MAX_S]


async def test_a_part_called_off_during_the_pause_is_not_sent(
    container, world, fake_llm, connector, monkeypatch
):
    jobs = await queued_parts(container, world, fake_llm, connector, "ой привет)\n\nа вы откуда?")

    async def operator_takes_over() -> None:
        async with transaction(container.factory) as s:
            (await s.scalars(select(Dialog))).unique().one().ai_paused = True

    monkeypatch.setattr(outbound, "sleep", Pauses(connector.port, operator_takes_over))
    await send(container, jobs[1])
    assert connector.port.sent == []
    assert connector.port.actions[-1] == ("chat-1", CANCEL)      # the status does not hang
    async with container.factory() as s:
        part = await s.get(Message, jobs[1].payload["message_id"])
    assert part.delivery is Delivery.CANCELLED
