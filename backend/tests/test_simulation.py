"""Admin simulation chat in the login bot: webhook, commands, jobs, isolation, API."""
from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy import func, select

from sim.adapters.channels.telegram import TelegramError
from sim.adapters.db.models import (
    AdminUser,
    Client,
    Dialog,
    Message,
    SimBranch,
    SimMessage,
)
from sim.adapters.db.session import transaction
from sim.api.main import register_admin_bot_webhook
from sim.domain.enums import Stage
from sim.services.admin_bot import REFUSAL, SECRET_HEADER, admin_bot_secret
from sim.services.jobkinds import SIM_REPLY
from sim.worker.loop import Worker
from tests.conftest import BOT_TOKEN, OPERATOR, OWNER, decision_json, guard_json, login

HOOK = "/hooks/admin-bot"


class FakeTransport:
    """Bot API stand-in: records every call, answers getFile / downloads."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, Exception] = {}

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params or {}))
        if method in self.fail:
            raise self.fail[method]
        if method == "getFile":
            return {"file_path": "voice/1.ogg", "file_size": 10}
        return {"message_id": len(self.calls)}

    async def download_file(self, file_path: str, max_bytes: int) -> bytes:
        return b"ogg-bytes"

    def sent(self) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == "sendMessage"]

    def texts(self) -> list[str]:
        return [p["text"] for p in self.sent()]

    def last_text(self) -> str:
        return self.sent()[-1]["text"]


@pytest.fixture
def tg(container) -> FakeTransport:
    transport = FakeTransport()
    container.admin_bot_transport = transport
    return transport


def secret(container) -> dict[str, str]:
    return {SECRET_HEADER: admin_bot_secret(container.settings.secret_key, BOT_TOKEN)}


def msg_update(text: str | None = None, *, tg_id: int = OWNER[0], name: str = "Дима", **extra: Any) -> dict:
    message = {
        "message_id": 1, "date": 1, "chat": {"id": tg_id, "type": "private"},
        "from": {"id": tg_id, "first_name": name, "is_bot": False}, **extra,
    }
    if text is not None:
        message["text"] = text
    return {"update_id": 1, "message": message}


def cb_update(data: str, *, tg_id: int = OWNER[0]) -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb1", "from": {"id": tg_id, "first_name": "Дима", "is_bot": False}, "data": data,
            "message": {"message_id": 5, "chat": {"id": tg_id, "type": "private"}},
        },
    }


async def send(api, container, update: dict) -> Any:
    resp = await api.post(HOOK, json=update, headers=secret(container))
    assert resp.status_code == 200, resp.text
    return resp


async def run_jobs(container) -> int:
    return await Worker(container).tick()


async def start_branch(api, container, world) -> int:
    await send(api, container, cb_update(f"p:{world.persona.id}"))
    async with container.factory() as s:
        return int(await s.scalar(select(func.max(SimBranch.id))))


async def count(container, model) -> int:
    async with container.factory() as s:
        return int(await s.scalar(select(func.count()).select_from(model)))


# ---- access -------------------------------------------------------------------------------
async def test_webhook_secret_is_checked(api, container, tg):
    assert (await api.post(HOOK, json=msg_update("/start"))).status_code == 401
    bad = await api.post(HOOK, json=msg_update("/start"), headers={SECRET_HEADER: "nope"})
    assert bad.status_code == 401
    assert tg.calls == []
    await send(api, container, msg_update("/start"))
    assert tg.calls


def test_secret_is_deterministic_and_depends_on_both_inputs():
    a = admin_bot_secret("k1", "tok")
    assert a == admin_bot_secret("k1", "tok")
    assert a != admin_bot_secret("k2", "tok") and a != admin_bot_secret("k1", "tok2")
    assert len(a) == 64 and all(ch in "0123456789abcdef" for ch in a)


async def test_non_admin_gets_one_line_refusal_and_nothing_else(api, container, tg, world):
    await send(api, container, msg_update("/start", tg_id=777))
    await send(api, container, msg_update("привет", tg_id=777))
    await send(api, container, cb_update(f"p:{world.persona.id}", tg_id=777))
    assert tg.texts() == [REFUSAL, REFUSAL]
    assert world.persona.name not in " ".join(tg.texts())
    assert "reply_markup" not in tg.sent()[0]
    assert await count(container, SimBranch) == 0
    assert await count(container, SimMessage) == 0


async def test_inactive_admin_is_refused(api, container, tg):
    async with transaction(container.factory) as s:
        user = await s.scalar(select(AdminUser).where(AdminUser.telegram_id == OPERATOR[0]))
        user.is_active = False
    await send(api, container, msg_update("/start", tg_id=OPERATOR[0]))
    assert tg.texts() == [REFUSAL]


async def test_any_role_may_use_the_bot(api, container, tg, world):
    await send(api, container, msg_update("/start", tg_id=OPERATOR[0]))   # operator
    assert "Симуляции Sim" in tg.last_text()


async def test_webhook_swallows_internal_errors_with_200(api, container, tg):
    tg.fail["sendMessage"] = TelegramError("down")
    resp = await send(api, container, msg_update("/start"))
    assert resp.json()["ok"] is True


# ---- commands -----------------------------------------------------------------------------
async def test_start_shows_help_and_persona_picker(api, container, tg, world):
    await send(api, container, msg_update("/start"))
    sent = tg.sent()[0]
    assert "/branches" in sent["text"] and "/debug" in sent["text"]
    buttons = [b for row in sent["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons == [{"text": world.persona.name, "callback_data": f"p:{world.persona.id}"}]


async def test_persona_pick_and_new_create_branches(api, container, tg, world):
    first = await start_branch(api, container, world)
    assert "Новая симуляция" in tg.last_text()
    await send(api, container, msg_update("/new"))
    async with container.factory() as s:
        rows = (await s.scalars(select(SimBranch).order_by(SimBranch.id))).all()
    assert len(rows) == 2 and rows[0].id == first
    assert all(r.persona_id == world.persona.id and r.stage is Stage.FIRST_CONTACT for r in rows)
    assert await count(container, SimMessage) == 0   # fresh history


async def test_new_without_a_previous_branch_offers_the_picker(api, container, tg, world):
    await send(api, container, msg_update("/new"))
    assert "reply_markup" in tg.sent()[0]
    assert await count(container, SimBranch) == 0


async def test_text_without_a_branch_asks_to_pick_a_persona(api, container, tg, world):
    await send(api, container, msg_update("привет"))
    assert "Нет активной симуляции" in tg.last_text()
    assert await count(container, SimBranch) == 0


# ---- a client message -> job -> reply -----------------------------------------------------
async def test_text_becomes_a_sim_reply_job_and_the_persona_answers_with_footer(
    api, container, tg, world, fake_llm
):
    branch_id = await start_branch(api, container, world)
    fake_llm.replies = [decision_json(reply="Привет, Тест Дима!", stage="engaged")]
    before = len(tg.sent())
    await send(api, container, msg_update("Здравствуйте"))
    assert len(tg.sent()) == before            # the webhook itself sends nothing: a job answers
    async with container.factory() as s:
        from sim.adapters.db.models import Job

        job = await s.scalar(select(Job).where(Job.kind == SIM_REPLY))
        assert job is not None and job.payload["branch_id"] == branch_id
    assert await run_jobs(container) == 1
    answer = tg.last_text()
    assert answer.startswith("Привет, Тест Дима!")
    assert "<i>engaged · fake-model · guard: ok</i>" in answer
    assert any(m == "sendChatAction" and p["action"] == "typing" for m, p in tg.calls)
    async with container.factory() as s:
        rows = (await s.scalars(select(SimMessage).order_by(SimMessage.id))).all()
        branch = await s.get(SimBranch, branch_id)
    assert [(r.role, r.guard_verdict) for r in rows] == [("client", None), ("ai", "ok")]
    assert rows[1].model == "fake-model" and branch.stage is Stage.ENGAGED
    # the persona saw the simulated client: name prefix and the fake ref link
    system_tail = fake_llm.gen_calls[-1][0][-1]["content"]
    assert "Тест Дима" in system_tail and f"ref=SIM{branch_id}" in system_tail


async def test_debug_off_hides_the_footer(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    await send(api, container, msg_update("/debug off"))
    assert "выключена" in tg.last_text()
    fake_llm.replies = [decision_json(reply="Привет")]
    await send(api, container, msg_update("Здравствуйте"))
    await run_jobs(container)
    assert tg.last_text() == "Привет"
    await send(api, container, msg_update("/debug on"))
    assert "включена" in tg.last_text()


async def test_guard_block_shows_the_draft_and_violations(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    fake_llm.replies = [decision_json(reply="Заработаете миллион за месяц!")] * 2
    fake_llm.guard_replies = [guard_json("block", ["обещает доход"])] * 2
    await send(api, container, msg_update("Сколько заработаю?"))
    await run_jobs(container)
    answer = tg.last_text()
    assert "заблокировал" in answer and "Заработаете миллион за месяц!" in answer
    assert "обещает доход" in answer and "guard: заблокировано" in answer
    async with container.factory() as s:
        ai = (await s.scalars(select(SimMessage).where(SimMessage.role == "ai"))).one()
    assert ai.guard_verdict == "blocked" and ai.guard_violations == ["обещает доход"]


async def test_simulation_guard_objection_returns_to_generator(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    fake_llm.replies = [decision_json(reply="Точно заработаете"),
                        decision_json(reply="Многое зависит от вас")]
    fake_llm.guard_replies = [guard_json("rewrite", ["обещание"], "Текст контролёра."),
                              guard_json("ok")]
    await send(api, container, msg_update("Сколько заработаю?"))
    await run_jobs(container)
    assert tg.last_text().startswith("Многое зависит от вас")
    assert "guard: ok" in tg.last_text()
    assert len(fake_llm.gen_calls) == len(fake_llm.guard_calls) == 2


async def test_a_blocked_draft_is_not_part_of_the_next_prompt(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    fake_llm.replies = [decision_json(reply="ЧЕРНОВИК-БЛОК")] * 2 + [decision_json(reply="ок")]
    fake_llm.guard_replies = [guard_json("block", ["x"])] * 2
    await send(api, container, msg_update("раз"))
    await run_jobs(container)
    await send(api, container, msg_update("два"))
    await run_jobs(container)
    prompt = fake_llm.gen_calls[-1][0]
    assert all("ЧЕРНОВИК-БЛОК" not in m["content"] for m in prompt)


async def test_one_reply_in_flight_per_branch(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    await send(api, container, msg_update("первое"))
    await send(api, container, msg_update("второе"))       # job for the first still pending
    assert "ещё готовится" in tg.last_text()
    assert await count(container, SimMessage) == 1
    await run_jobs(container)
    await send(api, container, msg_update("второе"))       # free again
    assert await count(container, SimMessage) == 3


async def test_voice_is_transcribed_by_the_job(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    fake_llm.transcript = "расскажите про курс"
    await send(api, container, msg_update(voice={"file_id": "AwAD", "duration": 3}))
    await run_jobs(container)
    assert any("расскажите про курс" in t for t in tg.texts())
    async with container.factory() as s:
        client_msg = (await s.scalars(select(SimMessage).where(SimMessage.role == "client"))).one()
    assert client_msg.text == "расскажите про курс"
    assert "расскажите про курс" in fake_llm.gen_calls[-1][0][1]["content"]


async def test_stage_follows_the_funnel_rules_and_stage_command(api, container, tg, world, fake_llm):
    branch_id = await start_branch(api, container, world)
    fake_llm.replies = [
        decision_json(stage="interested", needs=["сменить профессию"], next_step="дать ссылку"),
        decision_json(stage="paid", next_step=""),    # partner-only: ignored for the AI
    ]
    await send(api, container, msg_update("хочу сменить профессию"))
    await run_jobs(container)
    await send(api, container, msg_update("я уже оплатил"))
    await run_jobs(container)
    async with container.factory() as s:
        branch = await s.get(SimBranch, branch_id)
    assert branch.stage is Stage.INTERESTED and branch.needs == ["сменить профессию"]
    await send(api, container, msg_update("/stage"))
    shown = tg.last_text()
    assert "interested" in shown and "сменить профессию" in shown and "дать ссылку" in shown


async def test_refusal_stops_the_simulated_ai(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    fake_llm.replies = [decision_json(reply="Хорошо, не буду беспокоить", refused=True, stop_contact=True)]
    await send(api, container, msg_update("не пишите мне"))
    await run_jobs(container)
    n = len(fake_llm.gen_calls)
    await send(api, container, msg_update("ну ладно, а что за курс?"))
    await run_jobs(container)
    assert len(fake_llm.gen_calls) == n          # no model call: the real engine is silent too
    assert "/new" in tg.last_text()


async def test_end_and_branches_and_resume(api, container, tg, world, fake_llm):
    first = await start_branch(api, container, world)
    await send(api, container, msg_update("/end"))
    assert "закрыта" in tg.last_text()
    await send(api, container, msg_update("привет"))
    assert "Нет активной симуляции" in tg.last_text()
    await send(api, container, msg_update("/new"))
    await send(api, container, msg_update("/branches"))
    listing = tg.sent()[-1]
    assert f"#{first}" in listing["text"] and "✖" in listing["text"]
    buttons = [b for row in listing["reply_markup"]["inline_keyboard"] for b in row]
    assert {b["callback_data"] for b in buttons} == {f"b:{first}", f"b:{first + 1}"}
    await send(api, container, cb_update(f"b:{first}"))
    assert "Продолжаем" in tg.last_text()
    async with container.factory() as s:
        assert (await s.get(SimBranch, first)).closed_at is None   # reopened and current
    fake_llm.replies = [decision_json(reply="снова здесь")]
    await send(api, container, msg_update("вернулся"))
    await run_jobs(container)
    async with container.factory() as s:
        ai = (await s.scalars(select(SimMessage).where(SimMessage.role == "ai"))).one()
    assert ai.branch_id == first


async def test_branches_are_isolated(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    await send(api, container, msg_update("секрет-ветки-А"))
    await run_jobs(container)
    await send(api, container, msg_update("/new"))
    await send(api, container, msg_update("вопрос-ветки-Б"))
    await run_jobs(container)
    prompt = "\n".join(m["content"] for m in fake_llm.gen_calls[-1][0])
    assert "вопрос-ветки-Б" in prompt
    assert "секрет-ветки-А" not in prompt and "Здравствуйте" not in prompt


async def test_simulations_create_no_client_dialog_or_message_rows(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    await send(api, container, msg_update("привет"))
    await run_jobs(container)
    assert await count(container, SimMessage) == 2
    for model in (Client, Dialog, Message):
        assert await count(container, model) == 0
    assert all(m != "sendMessage" or p["chat_id"] == OWNER[0] for m, p in tg.calls)


async def test_model_failure_is_reported_to_the_admin_after_the_last_attempt(
    api, container, tg, world, fake_llm
):
    from sim.ports.llm import LLMError

    await start_branch(api, container, world)
    fake_llm.replies = [LLMError("broker down")] * 4
    await send(api, container, msg_update("привет"))
    worker = Worker(container)
    await worker.tick()
    async with transaction(container.factory) as s:
        from datetime import timedelta

        from sim.adapters.db.models import Job

        for job in (await s.scalars(select(Job))).all():
            job.run_at = job.run_at - timedelta(hours=1)
    await worker.tick()
    assert "Не удалось получить ответ" in tg.last_text()


# ---- registration -------------------------------------------------------------------------
async def test_webhook_registration_runs_only_in_prod_and_is_idempotent(container, tg):
    await register_admin_bot_webhook(container)
    assert tg.calls == []                            # env=test: no call
    container.settings.env = "prod"
    container.settings.public_url = "https://sim.example.com/"
    await register_admin_bot_webhook(container)
    await register_admin_bot_webhook(container)
    (_, params), _second = tg.calls
    assert params["url"] == "https://sim.example.com/hooks/admin-bot"
    assert params["secret_token"] == admin_bot_secret(container.settings.secret_key, BOT_TOKEN)
    assert params["allowed_updates"] == ["message", "callback_query"]


async def test_webhook_registration_failure_is_logged_without_the_token(container, tg, caplog):
    logging.getLogger("sim.api.main").disabled = False   # alembic's fileConfig may have muted it
    caplog.set_level(logging.WARNING)
    container.settings.env = "prod"
    tg.fail["setWebhook"] = TelegramError("Bad Request: bad webhook")
    await register_admin_bot_webhook(container)       # must not raise
    assert "setWebhook failed" in caplog.text and BOT_TOKEN not in caplog.text


# ---- admin UI API -------------------------------------------------------------------------
async def test_simulations_api_lists_and_shows_transcripts(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    fake_llm.replies = [decision_json(reply="Привет"), decision_json(reply="Привет!")]
    fake_llm.guard_replies = [guard_json("rewrite", ["тон"], "Текст контролёра."), guard_json("ok")]
    await send(api, container, msg_update("здравствуйте"))
    await run_jobs(container)
    csrf = await login(api, OWNER[0])
    assert csrf
    listing = (await api.get("/api/simulations")).json()
    assert listing["total"] == 1
    row = listing["items"][0]
    assert row["message_count"] == 2 and row["persona"]["name"] == world.persona.name
    assert row["admin"]["display_name"] and row["stage"] == "engaged"
    detail = (await api.get(f"/api/simulations/{row['id']}")).json()
    assert [m["role"] for m in detail["messages"]] == ["client", "ai"]
    ai = detail["messages"][1]
    assert ai["model"] == "fake-model" and ai["guard"]["verdict"] == "ok"
    assert ai["guard"]["original_text"] is None and ai["guard"]["violations"] == []
    assert (await api.get("/api/simulations/9999")).status_code == 404


async def test_simulations_api_is_owner_only(api, container):
    assert (await api.get("/api/simulations")).status_code == 401
    await login(api, OPERATOR[0])
    assert (await api.get("/api/simulations")).status_code == 403


async def test_simulation_traffic_is_not_in_the_analytics_cost(api, container, tg, world, fake_llm):
    await start_branch(api, container, world)
    await send(api, container, msg_update("привет"))
    await run_jobs(container)
    from sim.adapters.db.models import LlmCall

    async with container.factory() as s:
        workflows = {c.workflow for c in (await s.scalars(select(LlmCall))).all()}
    assert workflows == {"sim", "sim_guard"}
    async with container.factory() as s:
        from datetime import timedelta

        from sim.clock import utcnow

        totals = await container.analytics(s).llm_totals(utcnow() - timedelta(days=1), utcnow() + timedelta(days=1), None)
    assert totals["calls"] == 0


async def test_deleting_an_admin_removes_their_simulations(api, container, tg, world, fake_llm):
    branch = await start_branch(api, container, world)
    await send(api, container, msg_update("привет"))
    await run_jobs(container)
    async with transaction(container.factory) as s:
        user = await s.scalar(select(AdminUser).where(AdminUser.telegram_id == OWNER[0]))
        await container.admins(s).users.remove(user)
    assert await count(container, SimBranch) == 0 and await count(container, SimMessage) == 0
    assert branch


async def test_strategy_put_without_prompt_keeps_it(api, container, world):
    async with transaction(container.factory) as s:
        strategy = await s.get(type(world.strategy), world.strategy.id)
        strategy.prompt = "Свободный текст"
        strategy.notes = "заметка"
    csrf = await login(api, OWNER[0])
    resp = await api.put(
        f"/api/strategies/{world.strategy.id}", json={"name": "S", "goal": "новая цель"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal"] == "новая цель" and body["prompt"] == "Свободный текст" and body["notes"] == "заметка"
    resp = await api.put(
        f"/api/strategies/{world.strategy.id}", json={"name": "S", "prompt": "Новый"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.json()["prompt"] == "Новый"
