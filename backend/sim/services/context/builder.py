"""ContextBuilder: one place that turns the database state of a chat into what the model sees
(docs/context.md §2). The reply, the guard, calls and the memory steps all read it from here,
so a text answer and a call never disagree. Deterministic: time comes only from the Clock.

The blocks go from the most stable to the most volatile, so consecutive turns of a chat share a
byte-identical prefix and the provider's prompt cache hits (docs/context.md §2): the persona
prompt and format rules (system, shared by all her chats) → her routine, story arcs, canon
(shared by her chats) → this client's card and passport, the week and day summaries (change at
night) → today's verbatim timeline (append-only) → the notes of the day, the facts of «now»,
her current scene, the media catalog and the turn instruction (change every turn)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.config import Settings
from sim.db.models import (
    Call,
    Chat,
    ChatEvent,
    Client,
    Generation,
    MediaAsset,
    Message,
    Persona,
)
from sim.domain.agents import AgentKey, agent_config
from sim.domain.enums import (
    CallStatus,
    EventKind,
    GenerationStatus,
    MediaKind,
)
from sim.domain.memory import notes_of
from sim.domain.passport import passport_lines, passport_of
from sim.ports import AUTO_TIER, ChatMessage, ChatPart, ClockPort
from sim.services.capabilities import Capabilities, capabilities_of, with_camera
from sim.services.context.budget import estimate_tokens, fit_newest, lines_tokens
from sim.services.context.diary import Diary, load_diary, usable
from sim.services.context.facts import Facts, FactsInput, compute_facts, stamp
from sim.services.context.instructions import CALL_RULES, FORMAT_RULES, REPLY_TURN
from sim.services.context.render import (
    GENERATION_LABELS,
    Related,
    Speakers,
    TimelineLine,
    one_line,
    render_events,
)
from sim.services.context.window import day_start, local_day
from sim.services.life.render import LifeBlocks, life_blocks
from sim.services.life.store import load_life
from sim.services.media_library import catalog_for_chat

MIN_TIMELINE_TOKENS = 2_000
GUARD_CLIENT_LINES = 6
CALL_GREETINGS = {"ru": "Алло?", "uk": "Алло?"}
DEFAULT_GREETING = "Hello?"
KIND_WORDS = {MediaKind.PHOTO: "фото", MediaKind.VIDEO: "видео"}

# The verbatim window (docs/memory.md): her whole local day, plus the conversation that crossed
# midnight, plus yesterday while its summary is not written yet, plus unanswered messages.
CARRY_OVER = timedelta(hours=2)
UNANSWERED_LOOKBACK = timedelta(days=3)
TIMELINE_HEADING = "# Лента за сегодня (дословно, по порядку)"
MORNING_NOTE = "(раньше в этот день, кратко: {text})"
TRIMMED_NOTE = "(начало дня не поместилось)"
EMPTY_TIMELINE = "(сегодня сообщений пока нет)"


class ChatNotFound(LookupError):
    pass


@dataclass
class Knowledge:
    """Everything known about the chat now; shared by every consumer."""

    persona: Persona
    client: Client
    chat: Chat
    speakers: Speakers
    facts: Facts
    capabilities: Capabilities
    client_card: str
    life: LifeBlocks                        # routine, now, story arcs, canon
    diary: Diary                            # past days and weeks, notes of the day
    today: date                             # her local date now
    window_start: datetime                  # the verbatim window starts here (UTC)
    morning: str                            # today's oldest part, compressed ("" = none)
    all_lines: list[TimelineLine]           # the whole verbatim window after the morning
    lines: list[TimelineLine]               # the part that fits the budget (newest)
    timeline_budget: int

    @property
    def summary(self) -> str:
        """The legacy summary of the time before the diary (read-only)."""
        return self.diary.legacy

    @property
    def trimmed(self) -> bool:
        return len(self.lines) < len(self.all_lines)

    @property
    def needs_compression(self) -> bool:
        return lines_tokens(self.all_lines) > self.timeline_budget

    def shared_blocks(self) -> list[str]:
        """The stable part, most stable first: her world, the client, the past."""
        return [*self.life.shared(), f"# Собеседник\n{self.client_card}",
                *self.diary.stable_blocks()]

    def timeline_block(self) -> str:
        """Today's verbatim timeline: lines are only appended during the day."""
        parts = [TIMELINE_HEADING]
        if self.morning:
            parts.append(MORNING_NOTE.format(text=one_line(self.morning)))
        if self.trimmed:
            parts.append(TRIMMED_NOTE)
        parts += [line.text for line in self.lines] or [EMPTY_TIMELINE]
        return "\n".join(parts)

    def volatile_blocks(self) -> list[str]:
        """What changes every turn: the notes of the day, the facts of «now», her scene."""
        return [block for block in (self.diary.notes_block(), f"# Факты\n{self.facts.text}",
                                    self.life.now) if block]

    def snapshot(self) -> dict[str, Any]:
        return {
            "routine": self.life.routine, "arcs": self.life.arcs, "canon": self.life.canon,
            "client": self.client_card, "summary": self.summary, **self.diary.snapshot(),
            "window_start": self.window_start.isoformat(), "morning": self.morning,
            "timeline": [line.text for line in self.lines],
            "event_ids": [line.event_id for line in self.lines], "trimmed": self.trimmed,
            "facts": list(self.facts.lines), "facts_values": self.facts.values,
            "now": self.life.now,
        }


@dataclass
class ReplyFrame:
    knowledge: Knowledge
    system: str                             # persona prompt + format rules
    catalog: str                            # rendered media catalog
    allowed: dict[int, MediaKind]           # catalog media ids -> kind


@dataclass
class ReplyContext:
    chat_id: int
    persona_id: int
    client_id: int
    messages: list[ChatMessage]
    snapshot: dict[str, Any]
    catalog: dict[int, MediaKind]           # media ids the plan may send, with their kind
    capabilities: Capabilities
    facts: Facts
    client_lines: list[str]                 # the latest client lines (for the guard)
    last_event_id: int | None
    needs_compression: bool
    event_ids: list[int] = field(default_factory=list)
    # The user message up to the end of today's timeline: the same bytes on the next turn
    # when only new events arrived (the cached prefix, docs/context.md §2).
    stable_prefix: str = ""
    today: date | None = None


@dataclass
class CallContext:
    system: str
    greeting: str
    language: str
    voice: str
    model: str
    max_seconds: int
    key_tier: str = AUTO_TIER       # «Звонок» (docs/agents.md)


def _text_message(role: str, text: str) -> ChatMessage:
    return ChatMessage(role=role, parts=[ChatPart(text=text)])


def client_card(client: Client) -> str:
    lines = [f"Имя: {client.name or 'неизвестно'}"]
    if client.username:
        lines.append(f"Username: @{client.username}")
    if client.phone:
        lines.append(f"Телефон: {client.phone}")
    if client.timezone:
        lines.append(f"Часовой пояс: {client.timezone}")
    if client.notes.strip():
        lines.append(f"Заметки оператора: {client.notes.strip()}")
    lines.extend(passport_lines(passport_of(client.passport)))
    return "\n".join(lines)


def client_timezone(client: Client) -> str | None:
    """The client's time zone: the passport's (the operator edits it there, the writer finds it
    out), else the old profile field `client.timezone`."""
    return passport_of(client.passport).timezone or client.timezone or None


def catalog_text(available: Iterable[MediaAsset], sent: Iterable[tuple[MediaAsset, datetime]],
                 tz: str) -> str:
    lines = ["Можно отправить (ещё не отправляла в этот чат):"]
    offer = [
        f"- id {asset.id} · {KIND_WORDS.get(asset.kind, 'медиа')} · "
        f"{one_line(asset.caption or asset.description or asset.file_name) or 'без подписи'}"
        + (f" · теги: {', '.join(asset.tags)}" if asset.tags else "")
        for asset in available
    ]
    lines.extend(offer or ["- ничего"])
    sent_lines = [
        f"- {KIND_WORDS.get(asset.kind, 'медиа')} "
        f"«{one_line(asset.caption or asset.description) or 'без подписи'}» — {stamp(at, tz)}"
        for asset, at in sent
    ]
    if sent_lines:
        lines.append("Уже отправляла в этот чат (повторно не отправлять):")
        lines.extend(sent_lines)
    return "\n".join(lines)


async def _by_id(
    session: AsyncSession, model: type[Any], ids: set[int],
) -> dict[int, Any]:
    if not ids:
        return {}
    rows = (await session.execute(select(model).where(model.id.in_(ids)))).scalars()
    return {row.id: row for row in rows}


async def load_related(session: AsyncSession, events: list[ChatEvent]) -> Related:
    messages = await _by_id(session, Message, {e.message_id for e in events if e.message_id})
    media_ids = {e.media_id for e in events if e.media_id}
    media_ids |= {m.media_id for m in messages.values() if m.media_id}
    return Related(
        messages=messages,
        media=await _by_id(session, MediaAsset, media_ids),
        calls=await _by_id(session, Call, {e.call_id for e in events if e.call_id}),
        generations=await _by_id(session, Generation,
                                 {e.generation_id for e in events if e.generation_id}),
    )


async def load_events(session: AsyncSession, chat_id: int, *, since: datetime | None = None,
                      until: datetime | None = None,
                      after_id: int | None = None) -> list[ChatEvent]:
    """The events the agent sees, in order: created in [since, until), with id > after_id."""
    query = (select(ChatEvent)
             .where(ChatEvent.chat_id == chat_id, ChatEvent.in_context.is_(True))
             .order_by(ChatEvent.id))
    if since is not None:
        query = query.where(ChatEvent.created_at >= since)
    if until is not None:
        query = query.where(ChatEvent.created_at < until)
    if after_id is not None:
        query = query.where(ChatEvent.id > after_id)
    return list((await session.execute(query)).scalars())


async def timeline_lines(session: AsyncSession, chat_id: int, speakers: Speakers, *,
                         since: datetime | None = None, until: datetime | None = None,
                         after_id: int | None = None) -> list[TimelineLine]:
    """The rendered timeline of a period (the window, a past day, a recalled day)."""
    events = await load_events(session, chat_id, since=since, until=until, after_id=after_id)
    return render_events(events, await load_related(session, events), speakers)


def speakers_of(persona: Persona, client: Client) -> Speakers:
    return Speakers(client=client.name or client.username or "Собеседник",
                    persona=persona.name, tz=persona.timezone)


async def first_unanswered_at(session: AsyncSession, chat_id: int) -> datetime | None:
    """When the oldest client message after her last message came (None: none waiting)."""
    last_out = (select(func.max(ChatEvent.id))
                .where(ChatEvent.chat_id == chat_id, ChatEvent.kind == EventKind.MESSAGE_OUT)
                .scalar_subquery())
    return await session.scalar(
        select(func.min(ChatEvent.created_at))
        .where(ChatEvent.chat_id == chat_id, ChatEvent.kind == EventKind.MESSAGE_IN,
               ChatEvent.id > func.coalesce(last_out, 0)))


def window_start(now: datetime, tz: str, *, yesterday_ready: bool,
                 unanswered_at: datetime | None) -> datetime:
    """Where today's verbatim timeline starts (docs/memory.md): her local midnight minus
    `CARRY_OVER` (a conversation that crossed midnight); yesterday's midnight while yesterday
    has no summary yet; the oldest unanswered client message (up to `UNANSWERED_LOOKBACK`)."""
    today = local_day(now, tz)
    start = day_start(today, tz) - CARRY_OVER
    if not yesterday_ready:
        start = min(start, day_start(today - timedelta(days=1), tz))
    if unanswered_at is not None:
        start = min(start, max(unanswered_at, now - UNANSWERED_LOOKBACK))
    return start


class ContextBuilder:
    def __init__(self, clock: ClockPort, settings: Settings) -> None:
        self.clock = clock
        self.settings = settings

    # --- shared knowledge ----------------------------------------------------------------

    async def knowledge(self, session: AsyncSession, chat_id: int, *,
                        fixed_tokens: int = 0) -> Knowledge:
        """`fixed_tokens`: what the caller adds around the knowledge (prompt, catalog); the
        timeline gets the rest of the budget."""
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise ChatNotFound(f"chat {chat_id} not found")
        persona = await session.get(Persona, chat.persona_id)
        client = await session.get(Client, chat.client_id)
        if persona is None or client is None:
            raise ChatNotFound(f"chat {chat_id} has no persona or client")
        now = self.clock.now()
        speakers = speakers_of(persona, client)
        state = await load_life(session, persona, now, full=True)
        caps = with_camera(capabilities_of(persona), state.scene() if state.has_life else None)
        facts = compute_facts(await self._facts_input(session, chat, persona, client, caps))
        card = client_card(client)
        life = life_blocks(state)
        today = local_day(now, persona.timezone)
        notes = notes_of(chat.day_notes, today)
        diary, days = await load_diary(session, chat.id, today, chat.summary, notes)
        start = window_start(now, persona.timezone,
                             yesterday_ready=usable(days.get(today - timedelta(days=1))),
                             unanswered_at=await first_unanswered_at(session, chat.id))
        all_lines = await timeline_lines(session, chat.id, speakers, since=start,
                                         after_id=notes.morning_until)
        fixed = fixed_tokens + estimate_tokens(
            facts.text + card + notes.morning + "\n\n".join(
                [*life.texts(), *diary.stable_blocks(), diary.notes_block()]))
        budget = max(self.settings.context_budget_tokens - fixed, MIN_TIMELINE_TOKENS)
        return Knowledge(
            persona=persona, client=client, chat=chat, speakers=speakers, facts=facts,
            capabilities=caps, client_card=card, life=life, diary=diary, today=today,
            window_start=start, morning=notes.morning, all_lines=all_lines,
            lines=fit_newest(all_lines, budget), timeline_budget=budget,
        )

    async def _facts_input(self, session: AsyncSession, chat: Chat, persona: Persona,
                           client: Client, caps: Capabilities) -> FactsInput:
        stats = (await session.execute(
            select(ChatEvent.kind, func.count(), func.min(ChatEvent.created_at),
                   func.max(ChatEvent.created_at))
            .where(ChatEvent.chat_id == chat.id,
                   ChatEvent.kind.in_((EventKind.MESSAGE_IN, EventKind.MESSAGE_OUT,
                                       EventKind.CALL)))
            .group_by(ChatEvent.kind)
        )).all()
        by_kind = {kind: (count, first, last) for kind, count, first, last in stats}
        incoming = by_kind.get(EventKind.MESSAGE_IN, (0, None, None))
        outgoing = by_kind.get(EventKind.MESSAGE_OUT, (0, None, None))
        calls = by_kind.get(EventKind.CALL, (0, None, None))
        active_call = (await session.execute(
            select(func.count()).select_from(Call)
            .where(Call.chat_id == chat.id,
                   Call.status.in_((CallStatus.RINGING, CallStatus.ACTIVE)))
        )).scalar_one() > 0
        running = (await session.execute(
            select(Generation)
            .where(Generation.chat_id == chat.id,
                   Generation.status.in_((GenerationStatus.QUEUED, GenerationStatus.RUNNING)))
            .order_by(Generation.id)
        )).scalars()
        return FactsInput(
            now=self.clock.now(), persona_tz=persona.timezone, birth_date=persona.birth_date,
            client_tz=client_timezone(client), first_contact_at=incoming[1],
            last_client_at=incoming[2],
            last_persona_at=outgoing[2], messages_count=incoming[0] + outgoing[0],
            calls_count=calls[0], active_call=active_call,
            generations=tuple(f"{GENERATION_LABELS.get(g.kind, 'медиа')} «{one_line(g.caption)}»"
                              for g in running),
            capabilities=caps,
        )

    # --- reply ----------------------------------------------------------------------------

    async def reply_frame(self, session: AsyncSession, chat_id: int) -> ReplyFrame:
        """Knowledge sized for the reply prompt (persona prompt + rules + catalog). The memory
        step uses the same frame, so "does not fit" means the same thing for both."""
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise ChatNotFound(f"chat {chat_id} not found")
        persona = await session.get(Persona, chat.persona_id)
        if persona is None:
            raise ChatNotFound(f"chat {chat_id} has no persona")
        available, sent = await catalog_for_chat(session, persona.id, chat.id)
        catalog = catalog_text(available, sent, persona.timezone)
        system = f"{persona.prompt.strip()}\n\n{FORMAT_RULES}".strip()
        know = await self.knowledge(session, chat_id,
                                    fixed_tokens=estimate_tokens(system + catalog))
        return ReplyFrame(knowledge=know, system=system, catalog=catalog,
                          allowed={asset.id: asset.kind for asset in available})

    async def for_reply(self, session: AsyncSession, chat_id: int) -> ReplyContext:
        frame = await self.reply_frame(session, chat_id)
        know, system, catalog, allowed = (frame.knowledge, frame.system, frame.catalog,
                                          frame.allowed)
        chat, persona = know.chat, know.persona
        stable = "\n\n".join([*know.shared_blocks(), know.timeline_block()])
        user = "\n\n".join([stable, *know.volatile_blocks(), f"# Каталог медиа\n{catalog}",
                            REPLY_TURN])
        event_ids = [line.event_id for line in know.lines]
        client_lines = [line.text for line in know.lines
                        if line.kind == EventKind.MESSAGE_IN][-GUARD_CLIENT_LINES:]
        snapshot = {**know.snapshot(), "catalog": catalog, "catalog_ids": sorted(allowed)}
        return ReplyContext(
            chat_id=chat.id, persona_id=persona.id, client_id=know.client.id,
            messages=[_text_message("system", system), _text_message("user", user)],
            snapshot=snapshot, catalog=allowed, capabilities=know.capabilities,
            facts=know.facts, client_lines=client_lines,
            last_event_id=know.all_lines[-1].event_id if know.all_lines else None,
            needs_compression=know.needs_compression, event_ids=event_ids,
            stable_prefix=stable, today=know.today,
        )

    # --- calls ----------------------------------------------------------------------------

    async def for_call(self, session: AsyncSession, chat_id: int) -> CallContext:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise ChatNotFound(f"chat {chat_id} not found")
        persona = await session.get(Persona, chat.persona_id)
        if persona is None:
            raise ChatNotFound(f"chat {chat_id} has no persona")
        head = f"{persona.prompt.strip()}\n\n{CALL_RULES}".strip()
        know = await self.knowledge(session, chat_id, fixed_tokens=estimate_tokens(head))
        blocks = [head, *know.shared_blocks(), know.timeline_block(), *know.volatile_blocks()]
        live = agent_config(persona, AgentKey.LIVE)
        return CallContext(
            system="\n\n".join(blocks),
            greeting=CALL_GREETINGS.get(persona.language, DEFAULT_GREETING),
            language=persona.language, voice=persona.voice_id, model=live.model,
            max_seconds=self.settings.call_max_seconds, key_tier=live.key_tier,
        )
