"""A reply as several short messages: the model separates them with a blank line, code sends
them one by one with a typing pause (channels) or stores them as separate rows (simulations)."""
from __future__ import annotations

from sqlalchemy import select

from sim.adapters.db.models import AdminUser, Job, Message, Persona, SimMessage
from sim.adapters.db.session import transaction
from sim.domain.enums import Direction, Role
from sim.services import jobkinds
from sim.services.humanize import MAX_BUBBLES, TYPING_MAX_S, TYPING_MIN_S, bubbles, typing_pause_s
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
    run_at = [j.run_at for j in sends]
    assert run_at[0] < run_at[1] < run_at[2]
    assert (run_at[1] - run_at[0]).total_seconds() == typing_pause_s("я Марина")


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
