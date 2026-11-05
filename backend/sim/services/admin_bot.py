"""Admin simulation chat inside the login bot (Telegram webhook `/hooks/admin-bot`).

Only active admin_user rows (any role), matched by Telegram id, may use it; anyone else gets a
one-line refusal and nothing else. The webhook only parses the update, changes branch state and
queues a `sim_reply` job: the model call (minutes, in the worst case) never runs inside the
webhook request, so Telegram does not time out. The job shows "typing…" while it waits, runs the
shared conversation engine (see services.simulation) and sends the persona's text.

The bot token lives in the request URL (Telegram's design): nothing here logs it, and
TelegramError messages never contain it."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import html
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.channels.telegram import (
    TG_MAX_FILE_BYTES,
    TelegramError,
    TelegramTransport,
    chunk_text,
)
from sim.adapters.db.locks import NS_SIM, try_lock
from sim.adapters.db.models import AdminUser, Persona, SimBranch
from sim.adapters.db.repositories.accounts import AdminUserRepo
from sim.adapters.db.repositories.catalog import PersonaRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import ClientStatus
from sim.ports.llm import LLMPort
from sim.services import jobkinds
from sim.services.guard import BLOCKED, OK, REWRITTEN
from sim.services.media import VOICE_FAILED, placeholder_for
from sim.services.simulation import (
    BRANCH_LIST_LIMIT,
    CLIENT,
    SimReply,
    SimulationService,
    client_name_for,
)

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/hooks/admin-bot"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 (a header name)
ALLOWED_UPDATES = ["message", "callback_query"]
REFUSAL = "Этот бот доступен только администраторам Sim."
SEND_LIMIT = 3500           # below Telegram's 4096 once HTML escaping has grown the text
TYPING_EVERY_S = 4.0        # Telegram shows "typing…" for ~5 s per sendChatAction
REPLY_BUSY_S = 5
NO_BRANCH = "Нет активной симуляции. Выберите персонажа:"
BUSY = "⏳ Предыдущий ответ ещё готовится — подождите немного."
GIVE_UP = "⚠️ Не удалось получить ответ модели. Отправьте сообщение ещё раз."
HELP = (
    "<b>Симуляции Sim</b>\n"
    "Пишите как клиент — персонаж ответит так же, как отвечал бы настоящему человеку. "
    "Ничего не уходит в реальные каналы и не попадает в аналитику.\n\n"
    "/persona — выбрать персонажа (новая ветка)\n"
    "/new — новая ветка с текущим персонажем\n"
    "/branches — последние 10 веток\n"
    "/stage — этап, потребности и следующий шаг\n"
    "/end — закрыть ветку\n"
    "/debug on|off — строка «этап · модель · контролёр» под ответом"
)
GUARD_LABELS = {OK: "ok", REWRITTEN: "исправлено", BLOCKED: "заблокировано"}
STATUS_LABELS = {
    ClientStatus.ACTIVE: "активен",
    ClientStatus.PAUSED: "ИИ на паузе (передано оператору)",
    ClientStatus.NO_RESPONSE: "нет ответа",
    ClientStatus.REFUSED: "отказался",
}

Button = dict[str, str]


def admin_bot_secret(secret_key: str, bot_token: str) -> str:
    """Deterministic webhook secret derived from SIM_SECRET_KEY and the bot token: no extra env,
    and it changes when either changes (setWebhook is re-run on every API start)."""
    digest = hmac.new(secret_key.encode(), b"admin-bot:" + bot_token.encode(), hashlib.sha256)
    return digest.hexdigest()


async def register_webhook(transport: TelegramTransport, public_url: str, secret: str) -> None:
    """setWebhook for the login bot. Idempotent: the same call may run on every API start."""
    await transport.call(
        "setWebhook",
        {
            "url": f"{public_url.rstrip('/')}{WEBHOOK_PATH}",
            "secret_token": secret,
            "allowed_updates": ALLOWED_UPDATES,
        },
    )


def footer_line(reply: SimReply) -> str:
    guard = GUARD_LABELS.get(reply.verdict or "", reply.verdict or "—")
    stage = reply.stage.value if reply.stage else "—"
    return f"<i>{html.escape(stage)} · {html.escape(reply.model or '—')} · guard: {guard}</i>"


def render_reply(reply: SimReply, *, debug: bool) -> str:
    """The HTML text of the bot's answer to the admin."""
    if reply.status == "skipped":
        return f"ℹ️ {html.escape(reply.note)}. /new — начать заново."
    if reply.status == "no_decision":
        return "⚠️ Модель вернула непригодный ответ. Отправьте сообщение ещё раз."
    if reply.status == "empty":
        body = "(персонаж решил не отвечать)"
    elif reply.status == BLOCKED:
        reasons = "\n".join(f"• {html.escape(v)}" for v in reply.violations) or "• причина не указана"
        body = (
            "🚫 <b>Контролёр заблокировал ответ.</b> Клиент его не увидел бы.\n\n"
            f"<b>Черновик:</b>\n{html.escape(reply.text)}\n\n<b>Нарушения:</b>\n{reasons}"
        )
    else:
        body = html.escape(reply.text)
    return f"{body}\n\n{footer_line(reply)}" if debug else body


class AdminBot:
    def __init__(  # noqa: PLR0913
        self,
        session: AsyncSession,
        transport: TelegramTransport,
        simulation: SimulationService,
        llm: LLMPort,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self.session = session
        self.t = transport
        self.sim = simulation
        self.llm = llm
        self.clock = clock
        self.admins = AdminUserRepo(session)
        self.personas = PersonaRepo(session)
        self.jobs = JobRepo(session)

    # ---- Telegram I/O --------------------------------------------------------------------
    async def say(self, chat_id: int, text: str, buttons: list[Button] | None = None) -> None:
        """HTML text in chunks; the keyboard goes under the last chunk. A failure on the first
        chunk propagates (the caller decides); later chunks are best effort."""
        parts = chunk_text(text, SEND_LIMIT)
        for index, part in enumerate(parts):
            params: dict[str, Any] = {
                "chat_id": chat_id, "text": part, "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            }
            if buttons and index == len(parts) - 1:
                params["reply_markup"] = {"inline_keyboard": [[b] for b in buttons]}
            try:
                await self.t.call("sendMessage", params)
            except TelegramError as exc:
                if index == 0:
                    raise
                log.warning("admin bot: chunk %d/%d failed: %s", index + 1, len(parts), exc)
                return

    async def typing_loop(self, chat_id: int) -> None:
        while True:
            with contextlib.suppress(TelegramError):
                await self.t.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
            await asyncio.sleep(TYPING_EVERY_S)

    async def while_typing[T](self, chat_id: int, work: Awaitable[T]) -> T:
        task = asyncio.create_task(self.typing_loop(chat_id))
        await asyncio.sleep(0)   # let the first sendChatAction go out before the long wait
        try:
            return await work
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ---- the webhook ---------------------------------------------------------------------
    async def handle(self, update: dict[str, Any]) -> None:
        if isinstance(update.get("callback_query"), dict):
            await self.on_callback(update["callback_query"])
        elif isinstance(update.get("message"), dict):
            await self.on_message(update["message"])

    async def admin_of(self, sender: dict[str, Any]) -> AdminUser | None:
        if sender.get("is_bot") or not isinstance(sender.get("id"), int):
            return None
        user = await self.admins.by_telegram_id(sender["id"])
        return user if user is not None and user.is_active else None

    async def on_message(self, msg: dict[str, Any]) -> None:
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        if chat.get("type") != "private" or sender.get("is_bot") or chat.get("id") is None:
            return
        chat_id = int(chat["id"])
        admin = await self.admin_of(sender)
        if admin is None:
            await self.say(chat_id, REFUSAL)
            return
        name = client_name_for(sender.get("first_name"))
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            await self.on_command(admin, chat_id, text)
        elif voice := msg.get("voice") or msg.get("audio"):
            await self.on_client_message(admin, chat_id, name, None, str(voice.get("file_id") or ""))
        elif text:
            await self.on_client_message(admin, chat_id, name, text, None)

    async def on_callback(self, cq: dict[str, Any]) -> None:
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
        admin = await self.admin_of(cq.get("from") or {})
        with contextlib.suppress(TelegramError):
            await self.t.call(
                "answerCallbackQuery",
                {"callback_query_id": cq.get("id"), **({"text": REFUSAL} if admin is None else {})},
            )
        if admin is None or chat_id is None:
            return
        kind, _, raw = str(cq.get("data") or "").partition(":")
        if not raw.isdigit():
            return
        if kind == "p":
            persona = await self.personas.get(int(raw))
            if persona is None or not persona.is_active:
                await self.say(chat_id, "Персонаж недоступен. /persona — выбрать другого.")
                return
            await self.begin(admin, chat_id, persona)
        elif kind == "b":
            branch = await self.sim.resume(admin, int(raw))
            if branch is None:
                await self.say(chat_id, "Ветка не найдена. /branches — список.")
                return
            await self.say(
                chat_id,
                f"▶️ Продолжаем: <b>{html.escape(branch.title)}</b>\n{self.stage_text(branch)}",
            )

    # ---- commands ------------------------------------------------------------------------
    async def on_command(self, admin: AdminUser, chat_id: int, text: str) -> None:
        parts = text.split()
        command = parts[0].split("@")[0].lower()
        handlers: dict[str, Callable[[AdminUser, int], Awaitable[None]]] = {
            "/start": self.cmd_help,
            "/help": self.cmd_help,
            "/persona": self.cmd_persona,
            "/new": self.cmd_new,
            "/branches": self.cmd_branches,
            "/stage": self.cmd_stage,
            "/end": self.cmd_end,
        }
        if command == "/debug":
            await self.cmd_debug(admin, chat_id, parts[1].lower() if len(parts) > 1 else "")
        elif command in handlers:
            await handlers[command](admin, chat_id)
        else:
            await self.say(chat_id, "Не знаю такой команды. /start — справка.")

    async def persona_buttons(self) -> list[Button]:
        return [
            {"text": p.name, "callback_data": f"p:{p.id}"}
            for p in await self.personas.all()
            if p.is_active
        ]

    async def offer_personas(self, chat_id: int, lead: str) -> None:
        buttons = await self.persona_buttons()
        await self.say(chat_id, lead if buttons else "Нет активных персонажей.", buttons or None)

    async def cmd_help(self, admin: AdminUser, chat_id: int) -> None:
        buttons = await self.persona_buttons()
        await self.say(chat_id, f"{HELP}\n\nВыберите персонажа:" if buttons else HELP, buttons or None)

    async def cmd_persona(self, admin: AdminUser, chat_id: int) -> None:
        await self.offer_personas(chat_id, "Выберите персонажа (начнётся новая ветка):")

    async def begin(self, admin: AdminUser, chat_id: int, persona: Persona) -> SimBranch:
        branch = await self.sim.start(admin, persona)
        await self.say(
            chat_id,
            f"🆕 Новая симуляция с <b>{html.escape(persona.name)}</b> (ветка #{branch.id}).\n"
            "Напишите первое сообщение как клиент.",
        )
        return branch

    async def cmd_new(self, admin: AdminUser, chat_id: int) -> None:
        latest = await self.sim.branches.latest(admin.id)
        persona = latest.persona if latest is not None else None
        if persona is None or not persona.is_active:
            await self.offer_personas(chat_id, "Сначала выберите персонажа:")
            return
        await self.begin(admin, chat_id, persona)

    async def cmd_branches(self, admin: AdminUser, chat_id: int) -> None:
        rows = await self.sim.branches.recent(admin.id, BRANCH_LIST_LIMIT)
        if not rows:
            await self.say(chat_id, "Веток пока нет. /persona — начать.")
            return
        counts = await self.sim.messages.counts([b.id for b in rows])
        lines = ["<b>Последние ветки</b> (▶ — текущая, ✖ — закрыта):"]
        buttons = []
        for index, b in enumerate(rows):
            mark = "✖" if b.closed_at else ("▶" if index == 0 else "•")
            lines.append(
                f"{mark} #{b.id} {html.escape(b.persona.name)} · {b.created_at:%d.%m %H:%M} · "
                f"{counts.get(b.id, 0)} сообщ."
            )
            buttons.append({"text": f"#{b.id} {b.persona.name}", "callback_data": f"b:{b.id}"})
        await self.say(chat_id, "\n".join(lines), buttons)

    def stage_text(self, branch: SimBranch) -> str:
        needs = "; ".join(branch.needs or []) or "пока неизвестны"
        return (
            f"Этап: <b>{html.escape(branch.stage.value)}</b>\n"
            f"Статус: {STATUS_LABELS.get(branch.status, branch.status.value)}\n"
            f"Потребности: {html.escape(needs)}\n"
            f"Следующий шаг: {html.escape(branch.next_step or '—')}"
        )

    async def cmd_stage(self, admin: AdminUser, chat_id: int) -> None:
        branch = await self.sim.branches.latest(admin.id)
        if branch is None:
            await self.offer_personas(chat_id, NO_BRANCH)
            return
        closed = " (закрыта)" if branch.closed_at else ""
        await self.say(chat_id, f"Ветка #{branch.id}{closed}\n{self.stage_text(branch)}")

    async def cmd_end(self, admin: AdminUser, chat_id: int) -> None:
        branch = await self.sim.branches.latest(admin.id)
        if branch is None or branch.closed_at is not None:
            await self.say(chat_id, "Нет открытой ветки. /persona — начать новую.")
            return
        await self.sim.close(branch)
        await self.say(chat_id, f"✖ Ветка #{branch.id} закрыта. /new — новая, /branches — вернуться.")

    async def cmd_debug(self, admin: AdminUser, chat_id: int, value: str) -> None:
        if value not in ("on", "off"):
            state = "включена" if await self.sim.prefs.debug(admin.id) else "выключена"
            await self.say(chat_id, f"Строка под ответом {state}. /debug on или /debug off.")
            return
        await self.sim.prefs.set_debug(admin.id, value == "on")
        await self.say(chat_id, f"Строка под ответом {'включена' if value == 'on' else 'выключена'}.")

    # ---- a client message ----------------------------------------------------------------
    async def on_client_message(
        self, admin: AdminUser, chat_id: int, client_name: str, text: str | None, voice_id: str | None
    ) -> None:
        branch = await self.sim.branches.latest(admin.id)
        if branch is None or branch.closed_at is not None:
            await self.offer_personas(chat_id, NO_BRANCH)
            return
        if await self.jobs.has_open(jobkinds.SIM_REPLY, jobkinds.sim_reply_prefix(branch.id)):
            await self.say(chat_id, BUSY)   # one reply in flight per branch
            return
        message = await self.sim.add_client_message(
            branch, text if text is not None else placeholder_for("voice")
        )
        await self.jobs.enqueue(
            jobkinds.SIM_REPLY,
            {
                "branch_id": branch.id, "message_id": message.id, "chat_id": chat_id,
                "client_name": client_name, "voice_file_id": voice_id,
            },
            self.clock(),
            dedup_key=jobkinds.sim_reply_key(branch.id, message.id),
            max_attempts=2,
        )

    # ---- the job -------------------------------------------------------------------------
    async def run_reply_job(self, payload: dict[str, Any]) -> None:
        """Transcribe a voice message if needed, produce the persona's reply, send it."""
        branch_id, chat_id = int(payload["branch_id"]), int(payload["chat_id"])
        branch = await self.sim.branches.get(branch_id)
        if branch is None:
            return
        if not await try_lock(self.session, NS_SIM, branch_id):
            await self.jobs.upsert_pending(
                jobkinds.SIM_REPLY, payload,
                self.clock() + timedelta(seconds=REPLY_BUSY_S), jobkinds.sim_reply_key(branch_id, int(payload["message_id"])),
            )
            return
        reply = await self.while_typing(chat_id, self.produce(branch, payload, chat_id))
        if reply is None:
            return
        debug = await self.sim.prefs.debug(branch.admin_user_id)
        await self.say(chat_id, render_reply(reply, debug=debug))

    async def produce(self, branch: SimBranch, payload: dict[str, Any], chat_id: int) -> SimReply | None:
        message = await self.sim.messages.get(int(payload["message_id"]))
        if message is None:
            return None
        if payload.get("voice_file_id"):
            message.text = await self.transcribe(str(payload["voice_file_id"]))
            await self.say(chat_id, f"🎙 <i>{html.escape(message.text)}</i>")
        last = (await self.sim.messages.recent(branch.id, 1))[-1:]
        if not last or last[0].role != CLIENT:
            return None   # already answered (a duplicate job)
        return await self.sim.reply(branch, str(payload.get("client_name") or client_name_for(None)))

    async def transcribe(self, file_id: str) -> str:
        info = await self.t.call("getFile", {"file_id": file_id})
        path = (info or {}).get("file_path")
        size = int((info or {}).get("file_size") or 0)
        if not path or size > TG_MAX_FILE_BYTES:
            return VOICE_FAILED
        audio = await self.t.download_file(path, TG_MAX_FILE_BYTES)
        result = await self.llm.transcribe(audio, "voice.ogg")   # LLMError: the job retries
        return result.text.strip() or VOICE_FAILED

    async def give_up(self, payload: dict[str, Any]) -> None:
        """Out of attempts: tell the admin instead of leaving the chat silent."""
        with contextlib.suppress(TelegramError):
            await self.say(int(payload["chat_id"]), GIVE_UP)
