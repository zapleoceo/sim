"""When the persona answers: the delay rules (pure), the wording of the prompt line, and the
reply job planned, re-planned, shown in the dialog card and released by the operator."""
from __future__ import annotations

from datetime import datetime, timedelta

from alembic import command
from sqlalchemy import select

from sim.adapters.db.models import Dialog, Job, Persona
from sim.adapters.db.session import transaction
from sim.services import jobkinds
from sim.services.app_settings import SPECS, AppSettingsService
from sim.services.reply_schedule import (
    BUSY,
    FREE,
    ROAD,
    SLEEP,
    URGENT_WAIT,
    URGENT_WORDS,
    WORK,
    Incoming,
    Plan,
    ScheduleRules,
    classify,
    delay_note,
    plan_reply,
    reason_text,
    rules_from,
)
from sim.worker.loop import Worker
from tests.conftest import OWNER, decision_json, inbound, login
from tests.test_ingest import ingest
from tests.test_migrations import alembic_config, columns

ROUTINE = """
пн-пт 09:00-13:00 консультации с клиентами
пн-пт 13:00-13:40 дорога домой
пн-пт 18:00-19:00 йога
ежедневно 20:00-21:00 свободное время, кофе
ежедневно 23:00-06:30 сплю
"""
RULES = ScheduleRules(
    enabled=True,
    check_min={SLEEP: 0, WORK: 30, ROAD: 15, BUSY: 20},
    class_words={FREE: ("свободн",), SLEEP: ("сплю",), WORK: ("консультац", "работ"), ROAD: ("дорог",)},
    urgent_words=("срочно", "оплат"),
    urgent_delay_min=2,
    urgent_wait_min=60,
)


def fri(hour: int, minute: int = 0) -> datetime:
    """Friday 13 November 2026, naive UTC."""
    return datetime(2026, 11, 13, hour, minute)


def plan(now: datetime, text: str = "Добрый день", *, sent: datetime | None = None, tz: str = "UTC",
         routine: str = ROUTINE, rules: ScheduleRules = RULES, **kw) -> Plan:
    return plan_reply(now, routine, tz, [Incoming(text, sent or now)], rules, **kw)


# ---- the rules ------------------------------------------------------------------------------
def test_routine_entries_get_a_class_by_the_owner_word_lists():
    assert classify("Сплю", RULES) == SLEEP
    assert classify("консультации с клиентами", RULES) == WORK
    assert classify("дорога домой", RULES) == ROAD
    assert classify("свободное время, кофе", RULES) == FREE
    assert classify("йога", RULES) == BUSY                    # any other entry
    stems = ScheduleRules(**{**RULES.__dict__, "class_words": {SLEEP: ("сон",), WORK: ("клиент",)}})
    assert classify("дневной сон", stems) == SLEEP
    assert classify("персональная тренировка", stems) == BUSY  # a stem starts a word, not hides in one
    assert classify("звонок Клиенту", stems) == WORK


def test_free_time_and_no_routine_answer_at_once():
    assert plan(fri(15)) == Plan(fri(15), None)               # nothing in the routine now
    assert plan(fri(20, 10)) == Plan(fri(20, 10), None)       # an entry of the "free" class
    assert plan(fri(9, 10), routine="") == Plan(fri(9, 10), None)


def test_work_waits_for_the_next_look_at_the_chat_or_the_end_of_the_entry():
    at_work = plan(fri(9, 10))
    assert at_work.at == fri(9, 40)                           # she looks every 30 minutes
    assert at_work.delay == {"cls": WORK, "what": "консультации с клиентами",
                             "until": "2026-11-13T13:00:00", "urgent": None}
    assert plan(fri(12, 50)).at == fri(13)                    # the entry ends first


def test_road_and_other_entries_have_their_own_interval():
    road = plan(fri(13, 10))
    assert (road.at, road.delay["cls"]) == (fri(13, 25), ROAD)
    yoga = plan(fri(18, 10))
    assert (yoga.at, yoga.delay["cls"], yoga.delay["what"]) == (fri(18, 30), BUSY, "йога")


def test_sleep_waits_until_she_wakes_up_also_after_midnight():
    evening = plan(fri(23, 30))
    assert evening.at == datetime(2026, 11, 14, 6, 30) and evening.delay["cls"] == SLEEP
    night = plan(datetime(2026, 11, 14, 2, 0))
    assert night.at == datetime(2026, 11, 14, 6, 30)


def test_an_urgent_message_is_answered_after_the_short_delay_but_does_not_wake_her():
    urgent = plan(fri(9, 10), "Срочно! Вопрос по оплате")
    assert urgent.at == fri(9, 12) and urgent.delay["urgent"] == URGENT_WORDS
    asleep = plan(fri(23, 30), "срочно")
    assert asleep.at == datetime(2026, 11, 14, 6, 30)        # only an operator wakes her


def test_a_client_who_waited_past_the_threshold_is_urgent():
    waited = plan(fri(9, 10), sent=fri(8, 5))
    assert waited.at == fri(9, 12) and waited.delay["urgent"] == URGENT_WAIT
    assert plan(fri(9, 10), sent=fri(8, 30)).at == fri(9, 40)   # 40 minutes: not yet
    never = ScheduleRules(**{**RULES.__dict__, "urgent_wait_min": 0})
    assert plan(fri(9, 10), sent=fri(6), rules=never).at == fri(9, 40)


def test_a_dialog_marked_urgent_or_a_switched_off_schedule_has_no_delay():
    assert plan(fri(23, 30), marked_urgent=True) == Plan(fri(23, 30), None)
    off = ScheduleRules(**{**RULES.__dict__, "enabled": False})
    assert plan(fri(9, 10), rules=off) == Plan(fri(9, 10), None)


def test_the_routine_is_read_on_her_own_clock_and_the_least_reachable_entry_wins():
    kyiv = plan(fri(7, 10), tz="Europe/Kyiv")                 # 09:10 in Kyiv (UTC+2 in November)
    assert kyiv.at == fri(7, 40) and kyiv.delay["until"] == "2026-11-13T11:00:00"
    both = plan(fri(23, 30), routine=ROUTINE + "пт 23:00-23:59 йога\n")
    assert both.delay["cls"] == SLEEP


async def test_the_rules_are_owner_settings_with_working_defaults(container):
    keys = {spec.key for spec in SPECS}
    assert {"reply_schedule_enabled", "reply_check_sleep_min", "reply_check_work_min",
            "reply_check_road_min", "reply_check_busy_min", "reply_urgent_delay_min",
            "reply_urgent_wait_min", "reply_urgent_words", "reply_sleep_words", "reply_work_words",
            "reply_road_words", "reply_free_words"} <= keys
    async with transaction(container.factory) as s:
        svc = AppSettingsService(s)
        rules = rules_from(await svc.load())
        assert rules.enabled and rules.check_min == {SLEEP: 0, WORK: 30, ROAD: 15, BUSY: 20}
        assert (rules.urgent_delay_min, rules.urgent_wait_min) == (2, 60)
        assert plan(fri(9, 10), rules=rules).at == fri(9, 40)           # the seeded word lists work
        assert plan(fri(23, 30), rules=rules).delay["cls"] == SLEEP
        assert plan(fri(13, 10), rules=rules).delay["cls"] == ROAD
        assert plan(fri(20, 10), rules=rules).delay is None
        await svc.set("reply_check_work_min", 10, None)
        await svc.set("reply_urgent_words", "билет, виза", None)
        rules = rules_from(await svc.load())
        assert plan(fri(9, 10), rules=rules).at == fri(9, 20)
        assert plan(fri(9, 10), "нужна виза", rules=rules).at == fri(9, 12)


# ---- the line in the reply prompt -----------------------------------------------------------
WORK_DELAY = {"cls": WORK, "what": "консультации с клиентами", "until": "2026-11-13T13:00:00",
              "urgent": None}


def test_the_prompt_line_states_the_facts_of_a_routine_delay():
    assert delay_note(WORK_DELAY, fri(9, 10), fri(13), "UTC") == (
        "- Задержка ответа: сообщение собеседника пришло в 09:10, ты отвечаешь в 13:00. "
        "По распорядку ты была на работе: консультации с клиентами (до 13:00). "
        "Это обычная задержка, извиняться за неё не нужно."
    )
    asleep = {"cls": SLEEP, "what": "сплю", "until": "2026-11-14T06:30:00", "urgent": None}
    assert delay_note(asleep, fri(23, 30), datetime(2026, 11, 14, 6, 30), "UTC") == (
        "- Задержка ответа: сообщение собеседника пришло вчера в 23:30, ты отвечаешь в 06:30. "
        "По распорядку ты спала: сплю (до 06:30). Это обычная задержка, извиняться за неё не нужно."
    )
    kyiv = delay_note(WORK_DELAY, fri(7, 10), fri(11), "Europe/Kyiv")
    assert "пришло в 09:10, ты отвечаешь в 13:00" in kyiv and "(до 15:00)" in kyiv


def test_the_prompt_line_after_an_ai_pause():
    assert delay_note({"pause": True}, fri(13, 20), fri(15, 40), "UTC") == (
        "- Задержка ответа: сообщение собеседника пришло в 13:20, ты отвечаешь в 15:40. "
        "В чате была пауза: ответы были остановлены, ты отвечаешь после её снятия. "
        "Учитывай, что прошло время; о паузе собеседнику не рассказывай."
    )
    both = delay_note({**WORK_DELAY, "pause": True}, fri(9, 10), fri(13), "UTC")
    assert "В чате была пауза" in both and "По распорядку ты была на работе" in both
    assert both.endswith("Это обычная задержка, извиняться за неё не нужно.")


def test_no_prompt_line_when_nothing_was_late():
    assert delay_note(None, fri(9, 10), fri(13), "UTC") == ""
    assert delay_note({}, fri(9, 10), fri(13), "UTC") == ""
    assert delay_note(WORK_DELAY, fri(9, 10), fri(9, 12), "UTC") == ""      # two minutes is no delay


def test_the_dialog_card_reason():
    assert reason_text(WORK_DELAY, "UTC") == "на работе: консультации с клиентами до 13:00"
    assert reason_text({**WORK_DELAY, "urgent": URGENT_WORDS}, "UTC") == (
        "на работе: консультации с клиентами до 13:00; срочное, ответит раньше конца")
    asleep = {"cls": SLEEP, "what": "сплю", "until": "2026-11-14T06:30:00", "urgent": URGENT_WORDS}
    assert reason_text(asleep, "Europe/Kyiv") == "спит: сплю до 08:30"


# ---- the reply job --------------------------------------------------------------------------
async def with_routine(container, world, routine: str = ROUTINE) -> None:
    async with transaction(container.factory) as s:
        persona = await s.get(Persona, world.persona.id)
        persona.routine, persona.timezone = routine, "UTC"


def at_clock(container, moment: datetime) -> None:
    container.clock = lambda: moment


async def reply_job(container) -> Job:
    async with container.factory() as s:
        return (await s.scalars(select(Job).where(Job.kind == jobkinds.REPLY))).one()


async def dialog_id_of(container) -> int:
    async with container.factory() as s:
        return (await s.scalars(select(Dialog))).unique().one().id


async def test_a_message_at_work_is_answered_at_the_next_look_and_the_plan_only_shortens(
    container, world, fake_llm, connector
):
    await with_routine(container, world)
    at_clock(container, fri(9, 10))
    res = await ingest(container, world.channel.id, inbound("Добрый день", ext="1", at=fri(9, 10)))
    assert res.reply_queued
    job = await reply_job(container)
    assert job.run_at == fri(9, 40) and job.payload["delay"]["cls"] == WORK

    at_clock(container, fri(9, 20))                           # one more message: the look stays
    await ingest(container, world.channel.id, inbound("Вы тут?", ext="2", at=fri(9, 20)))
    assert (await reply_job(container)).run_at == fri(9, 40)

    at_clock(container, fri(9, 25))                           # an urgent one: answered sooner
    await ingest(container, world.channel.id, inbound("Срочно, по оплате", ext="3", at=fri(9, 25)))
    job = await reply_job(container)
    assert job.run_at == fri(9, 27) and job.payload["delay"]["urgent"] == URGENT_WORDS

    worker = Worker(container)
    at_clock(container, fri(9, 26))
    assert await worker.tick() == 0                           # not due: nothing is generated
    assert fake_llm.calls == []
    at_clock(container, fri(9, 27))
    fake_llm.replies = [decision_json(reply="Здравствуйте, уже смотрю")]
    assert await worker.tick() == 1                           # ONE reply for the three messages
    prompt = "\n".join(str(m["content"]) for m in fake_llm.gen_calls[0][0])
    assert ("- Задержка ответа: сообщение собеседника пришло в 09:10, ты отвечаешь в 09:27. "
            "По распорядку ты была на работе: консультации с клиентами (до 13:00). "
            "Это обычная задержка, извиняться за неё не нужно.") in prompt
    assert all(text in prompt for text in ("Добрый день", "Вы тут?", "Срочно, по оплате"))
    assert len(fake_llm.gen_calls) == 1


async def test_free_time_keeps_the_plain_debounced_reply_and_no_prompt_line(container, world, fake_llm):
    await with_routine(container, world)
    at_clock(container, fri(15))
    await ingest(container, world.channel.id, inbound("Привет", at=fri(15)))
    job = await reply_job(container)
    assert job.payload == {"dialog_id": await dialog_id_of(container)}
    assert job.run_at == fri(15) + timedelta(seconds=container.settings.reply_debounce_s)
    at_clock(container, fri(15, 1))
    await Worker(container).tick()
    assert "Задержка ответа" not in "\n".join(str(m["content"]) for m in fake_llm.gen_calls[0][0])


async def test_the_schedule_can_be_switched_off(container, world):
    await with_routine(container, world)
    async with transaction(container.factory) as s:
        await AppSettingsService(s).set("reply_schedule_enabled", 0, None)
    at_clock(container, fri(23, 30))
    await ingest(container, world.channel.id, inbound("Не спите?", at=fri(23, 30)))
    job = await reply_job(container)
    assert "delay" not in job.payload and job.run_at < fri(23, 31)


async def test_dialog_card_shows_the_plan_and_reply_now_releases_it(api, container, world):
    await with_routine(container, world)
    at_clock(container, fri(9, 10))
    await ingest(container, world.channel.id, inbound("Добрый день", at=fri(9, 10)))
    dialog_id = await dialog_id_of(container)
    h = {"X-CSRF-Token": await login(api, *OWNER)}

    dialog = (await api.get(f"/api/dialogs/{dialog_id}")).json()["dialog"]
    assert dialog["urgent"] is False
    assert dialog["reply_plan"] == {
        "at": dialog["reply_plan"]["at"], "cls": WORK, "urgent": None,
        "reason": "на работе: консультации с клиентами до 13:00",
    }
    assert dialog["reply_plan"]["at"].startswith("2026-11-13T09:40")

    at_clock(container, fri(9, 15))
    now = await api.post(f"/api/dialogs/{dialog_id}/reply-now", headers=h)
    assert now.status_code == 200 and now.json()["queued"] is True and now.json()["reply_plan"] is None
    job = await reply_job(container)
    assert job.run_at == fri(9, 15) and job.payload["delay"]["cls"] == WORK   # the reason is kept
    assert (await api.get(f"/api/dialogs/{dialog_id}")).json()["dialog"]["reply_plan"] is None
    assert (await api.post("/api/dialogs/9999/reply-now", headers=h)).status_code == 404


async def test_reply_now_does_not_lift_an_ai_pause(api, container, world):
    await with_routine(container, world)
    at_clock(container, fri(9, 10))
    await ingest(container, world.channel.id, inbound("Добрый день", at=fri(9, 10)))
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        dialog.ai_paused = True
        dialog_id = dialog.id
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    resp = await api.post(f"/api/dialogs/{dialog_id}/reply-now", headers=h)
    assert resp.status_code == 409
    dialog = (await api.get(f"/api/dialogs/{dialog_id}")).json()["dialog"]
    assert dialog["ai_paused"] is True and dialog["reply_plan"] is None       # the pause is the news
    assert (await reply_job(container)).run_at == fri(9, 40)


async def test_an_urgent_dialog_is_answered_without_the_routine_delay(api, container, world):
    await with_routine(container, world)
    at_clock(container, fri(23, 30))
    await ingest(container, world.channel.id, inbound("Вы спите?", ext="1", at=fri(23, 30)))
    assert (await reply_job(container)).run_at == datetime(2026, 11, 14, 6, 30)
    dialog_id = await dialog_id_of(container)
    h = {"X-CSRF-Token": await login(api, *OWNER)}

    at_clock(container, fri(23, 35))
    marked = await api.post(f"/api/dialogs/{dialog_id}/urgent", json={"urgent": True}, headers=h)
    assert marked.status_code == 200
    assert marked.json()["urgent"] is True and marked.json()["reply_plan"] is None
    assert (await reply_job(container)).run_at == fri(23, 35)                  # she is woken up

    async with transaction(container.factory) as s:                            # that reply ran
        (await s.scalars(select(Job).where(Job.kind == jobkinds.REPLY))).one().status = "done"
    at_clock(container, fri(23, 50))
    await ingest(container, world.channel.id, inbound("И ещё вопрос", ext="2", at=fri(23, 50)))
    async with container.factory() as s:
        job = (await s.scalars(select(Job).where(Job.kind == jobkinds.REPLY, Job.status == "pending"))).one()
    assert "delay" not in job.payload and job.run_at < fri(23, 51)

    cleared = await api.post(f"/api/dialogs/{dialog_id}/urgent", json={"urgent": False}, headers=h)
    assert cleared.json()["urgent"] is False


async def test_the_reply_after_an_ai_pause_knows_about_the_pause(api, container, world, fake_llm):
    await with_routine(container, world, routine="")           # her clock is UTC, nothing planned
    at_clock(container, fri(15))
    await ingest(container, world.channel.id, inbound("Пришли фото", at=fri(15)))
    async with transaction(container.factory) as s:
        dialog = (await s.scalars(select(Dialog))).unique().one()
        dialog.ai_paused = True
        dialog_id = dialog.id
        for job in (await s.scalars(select(Job))).all():
            await s.delete(job)
    h = {"X-CSRF-Token": await login(api, *OWNER)}
    at_clock(container, fri(15, 30))
    resumed = await api.post(f"/api/dialogs/{dialog_id}/ai", json={"paused": False}, headers=h)
    assert resumed.json()["recovery"] == {"status": "queued", "messages": 1}
    assert (await reply_job(container)).payload == {"dialog_id": dialog_id, "delay": {"pause": True}}

    at_clock(container, fri(15, 31))
    await Worker(container).tick()
    prompt = "\n".join(str(m["content"]) for m in fake_llm.gen_calls[0][0])
    assert ("- Задержка ответа: сообщение собеседника пришло в 15:00, ты отвечаешь в 15:31. "
            "В чате была пауза: ответы были остановлены, ты отвечаешь после её снятия.") in prompt


async def test_a_planned_delay_is_not_reported_as_a_lost_message(container, world):
    await with_routine(container, world)
    at_clock(container, fri(23, 30))
    await ingest(container, world.channel.id, inbound("Вы спите?", at=fri(23, 30)))
    at_clock(container, fri(23, 50))
    assert await Worker(container).watch_unanswered() == 0   # planned for 06:30: no warning
    async with transaction(container.factory) as s:           # the plan is gone, she is silent
        for job in (await s.scalars(select(Job))).all():
            await s.delete(job)
    assert await Worker(container).watch_unanswered() == 1


def test_0025_adds_the_urgent_flag_and_roundtrips(tmp_path):
    db = tmp_path / "mig.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    assert "urgent" in columns(db, "dialog")
    command.downgrade(cfg, "0024")
    assert "urgent" not in columns(db, "dialog")
