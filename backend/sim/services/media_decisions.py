"""The journal of the persona's media decisions: every time she decides about a photo or a video
note (send, not send, later), the decision is written into the dialog as a service record and
comes back to her in the context of the next replies. The decision stays hers: nothing here
blocks or changes it.

A record is a `Message` row that is never sent (`delivery = cancelled`, no SEND job, like a
draft the guard blocked): `media_kind = "media_decision"` marks it, `text` is the readable
record, `original_text` holds its fields as JSON and `error` the warning, if any. It is not part
of the history the models read (only delivered messages are) and does not touch the dialog
preview. One decision is one row: the outcome of a generation updates the same row.

What a record holds:
- what was asked: a photo, a video note or nothing (`asked_kind` reads the client's unanswered
  messages; when she declines or postpones, her own `media.kind` says what it was about);
- what she decided and the reason she named (`prompt.MediaAsk`). When she was asked and returned
  no `media` field, nothing was sent and no decision was named (`SILENT`): the record keeps the
  start of her reply, so whatever she told the client (a made-up excuse, a promise) is visible;
- whether the dialog is with the owner (the allowlisted account of «Кружочки ИИ»);
- the outcome: declined, postponed, not sent, not queued, generation queued, generated, sent,
  Broker refusal, error;
- a warning when the reason cannot be confirmed by her routine at that moment (`reason_warning`:
  a word-stem comparison, a hint for the owner and nothing more).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Dialog, Message
from sim.adapters.db.repositories.conversations import MessageRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import Author, Delivery, Direction, MediaKind
from sim.services.persona_clock import local_time
from sim.services.persona_routine import doing_now
from sim.services.prompt import MEDIA_DECLINE, MEDIA_LATER, MEDIA_SEND

log = logging.getLogger(__name__)

MARKER = "media_decision"        # Message.media_kind of a record
TAG = "Решение медиа"            # how a record starts wherever only its text is shown
NOTHING = "none"                 # nothing was asked: the media is her own idea
SILENT = "silent"                # a decision the system records: she was asked, her reply has no `media`

# Outcomes (stored; never rename one).
DECLINED = "declined"            # she decided not to send
POSTPONED = "postponed"          # she decided to send later
UNSENT = "unsent"            # she was asked and her reply carries no media decision
UNAVAILABLE = "unavailable"      # she asked to send, but this kind is not on offer right now
ENDED = "ended"                  # she asked to send, but the reply ends the conversation
QUEUED = "queued"                # the generation job is queued
GENERATED = "generated"          # the file is made, the message is on its way
SENT = "sent"                    # the client got it
SEND_FAILED = "send_failed"      # generated, but the channel did not confirm the send
BROKER_REFUSED = "broker_refused"
FAILED = "failed"                # the generation failed
REFUSED = "refused"              # the system refused (the dialog is not with the allowlisted account)
SKIPPED = "skipped"              # the job did nothing (a human took over, the client refused)

OUTCOMES = {
    DECLINED: "отказ модели",
    POSTPONED: "отложено моделью",
    UNSENT: "не отправлено",
    UNAVAILABLE: "не поставлено: медиа этого вида сейчас недоступно",
    ENDED: "не поставлено: ответ завершает разговор",
    QUEUED: "запущена генерация",
    GENERATED: "сгенерировано, отправляется",
    SENT: "отправлено",
    SEND_FAILED: "ошибка отправки",
    BROKER_REFUSED: "отказ Broker",
    FAILED: "ошибка генерации",
    REFUSED: "отказ системы",
    SKIPPED: "пропущено",
}
ON_ITS_WAY = (QUEUED, GENERATED)
KINDS = {MediaKind.PHOTO.value: "фото", MediaKind.VIDEO_NOTE.value: "кружочек", NOTHING: "ничего"}
DECISIONS = {MEDIA_SEND: "отправить", MEDIA_DECLINE: "не отправлять", MEDIA_LATER: "отложить"}

HEADLINES = {
    MEDIA_SEND: "модель решила отправить",
    MEDIA_DECLINE: "отказ модели",
    MEDIA_LATER: "модель отложила",
    SILENT: "медиа не отправлено, решение не названо",
}
UNDECIDED = "просили медиа, а решения в ответе нет (поле media не заполнено)"

REPLY_MAX = 200                  # how much of her reply a record keeps when no reason is named
DETAIL_MAX = 300

# ---- what the client asked for ---------------------------------------------------------------
NOTE_WORDS = re.compile(r"кружоч|кружок|кружк|видеосообщ|видео|video", re.IGNORECASE)
PHOTO_WORDS = re.compile(r"фот|селфи|selfie|photo|снимок|снимк", re.IGNORECASE)
ASK_WORDS = re.compile(
    r"пришл|присла|скин|кинь|отправ|покаж|сдела|запиш|сфот|можно|можешь|хочу|давай|жду|"
    r"\bдай\b|send|show", re.IGNORECASE)
SHORT_ASK_WORDS = 4              # «а фото?»: a short message that names the media is a request
ASKED_LOOKBACK = 6               # how many of the latest visible messages are looked at
BROKER_REFUSAL = re.compile(r"\bHTTP 4\d\d\b")

# ---- the context of the next replies ----------------------------------------------------------
CONTEXT_RECORDS = 3
CONTEXT_HOURS = 24
EARLIER = (
    "РАНЕЕ ПО МЕДИА (записала система, это было на самом деле; не противоречь этим решениям, а "
    "если причину уже назвала, держись её и новых не придумывай):"
)
STEM_MIN = 4
WORD = re.compile(rf"[a-zа-яё]{{{STEM_MIN},}}")


def asked_kind(text: str) -> MediaKind | None:
    """The media a client message asks for: it names a photo or a video note and either has a
    request word («пришли», «покажи», «можно»...) or is a few words long."""
    asks = bool(ASK_WORDS.search(text)) or len(text.split()) <= SHORT_ASK_WORDS
    for words, kind in ((NOTE_WORDS, MediaKind.VIDEO_NOTE), (PHOTO_WORDS, MediaKind.PHOTO)):
        if asks and words.search(text):
            return kind
    return None


def broker_refused(error: str) -> bool:
    """The Broker answered the request with a 4xx (no scope, model not allowed, quota)."""
    return bool(BROKER_REFUSAL.search(error))


def stems(text: str) -> set[str]:
    return {word[:max(STEM_MIN, len(word) - 2)] for word in WORD.findall(text.lower())}


def reason_warning(reason: str, routine: str, timezone: str, now_utc: datetime) -> str:
    """Why the reason she named is doubtful, "" when the routine confirms it. The check compares
    the word stems of the reason with what her routine has her doing at this moment: «на
    тренировке» is confirmed by the entry «тренировка в зале». It cannot tell a preference («не
    хочу») from an invented event, so it is a warning for the owner and never blocks her."""
    if not reason:
        return "причина не названа"
    doing = "; ".join(doing_now(routine, local_time(timezone, now_utc)))
    if doing and any(stem in doing.lower() for stem in stems(reason)):
        return ""
    fact = f"сейчас по распорядку: {doing}" if doing else "на это время в распорядке записи нет"
    return f"причина не подтверждается распорядком ({fact})"


@dataclass(frozen=True)
class MediaDecision:
    requested: str               # "photo" | "video_note" | "none": what the client asked for
    kind: str                    # what the decision is about: "photo" | "video_note"
    decision: str                # "send" | "decline" | "later" (sim.services.prompt) | SILENT
    outcome: str                 # one of OUTCOMES
    reason: str = ""             # the reason she named, in her words
    reply: str = ""              # the start of her reply, kept when she named no reason
    scene: str = ""              # the shot she asked for
    owner: bool = False          # the dialog is with the owner (the allowlisted account)
    offered: tuple[str, ...] = ()    # the kinds that were on offer for this reply
    warning: str = ""
    detail: str = ""             # the error or refusal text of the outcome
    media_message_id: int | None = None   # the generated photo or note in the dialog

    @property
    def owner_request(self) -> bool:
        return self.owner and self.requested != NOTHING

    @property
    def needs_a_look(self) -> bool:
        """The owner asked and she refused, or her reason is doubtful: a warning in the log."""
        return bool(self.warning) or (self.owner_request and self.decision == MEDIA_DECLINE)

    @property
    def headline(self) -> str:
        what = HEADLINES.get(self.decision, self.decision)
        return f"запрос владельца, {what}" if self.owner_request else what

    def lines(self) -> list[str]:
        """The record as the admin shows it, line by line."""
        asked = KINDS[self.requested] + (" (инициатива модели)" if self.requested == NOTHING else "")
        said = [f"Ответ модели: «{self.reply}»"] if self.reply else []
        if self.decision == SILENT:
            decided = ["Решение: не названо (поля media в ответе нет)", *said]
        else:
            decided = [f"Решение: {DECISIONS.get(self.decision, self.decision)} {KINDS.get(self.kind, self.kind)}"]
            if self.reason:
                decided.append(f"Причина: «{self.reason}»")
            elif self.decision != MEDIA_SEND:
                decided += ["Причина: не названа", *said]
        outcome = OUTCOMES.get(self.outcome, self.outcome)
        return [
            f"Просили: {asked}",
            *decided,
            *([f"Сцена: {self.scene}"] if self.scene else []),
            f"Было доступно: {', '.join(KINDS.get(k, k) for k in self.offered) or 'ничего'}",
            f"Исход: {outcome}" + (f" ({self.detail})" if self.detail else ""),
        ]

    def text(self) -> str:
        warning = [f"Предупреждение: {self.warning}"] if self.warning else []
        return "\n".join([f"{TAG}: {self.headline}", *self.lines(), *warning])

    def context_line(self, when: datetime) -> str:
        """One line of the «РАНЕЕ ПО МЕДИА» block: what she decided, in the second person."""
        what = KINDS.get(self.kind, self.kind)
        stamp = when.strftime("%d.%m %H:%M")
        if self.decision == MEDIA_SEND:
            if self.outcome == SENT:
                result = "отправлено"
            elif self.outcome in ON_ITS_WAY:
                result = "готовится и уйдёт отдельным сообщением"
            else:
                result = "не ушло, собеседник его не получил: не пиши, что отправила"
            asked = " (просил владелец)" if self.owner_request else ""
            return f"- {stamp} ты решила прислать {what}{asked}; исход: {result}."
        said = f", а собеседнику ответила: «{self.reply}»" if self.reply else ""
        if self.decision == SILENT:
            who = "владелец просил" if self.owner_request else "тебя просили"
            return f"- {stamp} {who} {what}, ты не прислала и решение не записала{said}."
        reason = f"причина: «{self.reason}»" if self.reason else f"причину ты не записала{said}"
        if self.owner_request:
            did = "ты отказала" if self.decision == MEDIA_DECLINE else "ты решила прислать позже"
            return f"- {stamp} владелец просил {what}, {did}, {reason}."
        did = "не присылать" if self.decision == MEDIA_DECLINE else "прислать позже"
        return f"- {stamp} ранее ты решила {did} {what}, {reason}."

    def journal(self) -> dict[str, Any]:
        """What the system log keeps about the decision (the result of the reply job)."""
        out: dict[str, Any] = {"requested": self.requested, "kind": self.kind,
                               "decision": self.decision, "outcome": self.outcome,
                               "summary": self.headline}
        if self.owner_request:
            out["owner_request"] = True
        if self.warning:
            out["warning"] = self.warning
        return out

    def dump(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def load(message: Message) -> MediaDecision | None:
    """The decision a service record holds; None for any other message."""
    if message.media_kind != MARKER:
        return None
    try:
        raw = json.loads(message.original_text or "")
        known = {f.name for f in fields(MediaDecision)}
        data = {key: value for key, value in raw.items() if key in known}
        return MediaDecision(**{**data, "offered": tuple(data.get("offered") or ())})
    except (TypeError, ValueError, AttributeError):
        log.warning("message %s: unreadable media decision", message.id)
        return None


def store(message: Message, decision: MediaDecision) -> None:
    message.text = decision.text()
    message.original_text = decision.dump()
    message.error = decision.warning or None


def view(message: Message) -> dict[str, Any] | None:
    """The record for the admin API (`message.media_decision`); None for any other message."""
    decision = load(message)
    if decision is None:
        return None
    return {
        "requested": decision.requested, "kind": decision.kind, "decision": decision.decision,
        "reason": decision.reason, "owner_request": decision.owner_request,
        "outcome": decision.outcome, "headline": decision.headline, "lines": decision.lines(),
        "warning": decision.warning,
    }


def generation_outcome(result: dict[str, Any]) -> tuple[str, str, int | None]:
    """(outcome, detail, generated message id) from the result of the `persona_media` job."""
    error = str(result.get("error") or "")[:DETAIL_MAX]
    status = result.get("outcome")
    if status == "queued":
        return GENERATED, "", result.get("message_id")
    if status == "failed":
        return (BROKER_REFUSED if broker_refused(error) else FAILED), error, None
    return (REFUSED if status == "refused" else SKIPPED), error, None


class MediaDecisionLog:
    def __init__(self, session: AsyncSession, *, clock: Clock = utcnow) -> None:
        self.session = session
        self.clock = clock
        self.messages = MessageRepo(session)

    async def asked(self, dialog_id: int) -> MediaKind | None:
        """What the client's unanswered messages ask for (the latest request wins)."""
        recent = await self.messages.recent_visible(dialog_id, ASKED_LOOKBACK)
        for message in reversed(recent):
            if message.direction is not Direction.IN:
                break
            kind = asked_kind(message.text or "")
            if kind is not None:
                return kind
        return None

    async def add(self, dialog: Dialog, decision: MediaDecision) -> Message:
        """Write the record into the dialog. It is never sent and has no SEND job."""
        message = Message(dialog_id=dialog.id, direction=Direction.OUT, author=Author.AI,
                          media_kind=MARKER, delivery=Delivery.CANCELLED, created_at=self.clock())
        store(message, decision)
        await self.messages.add(message)
        return message

    async def recent(self, dialog_id: int, limit: int) -> list[tuple[Message, MediaDecision]]:
        """The latest records of a dialog, oldest first."""
        rows = (await self.session.scalars(
            select(Message)
            .where(Message.dialog_id == dialog_id, Message.media_kind == MARKER)
            .order_by(Message.id.desc()).limit(limit)
        )).all()
        loaded = ((row, load(row)) for row in reversed(rows))
        return [(row, decision) for row, decision in loaded if decision is not None]

    async def update(self, record_id: int | None, **changes: Any) -> MediaDecision | None:
        """Change the outcome of one record (the same row: one decision is one line)."""
        message = await self.messages.get(record_id) if record_id else None
        decision = load(message) if message is not None else None
        if message is None or decision is None:
            return None
        decision = replace(decision, **changes)
        store(message, decision)
        return decision

    async def generated(self, record_id: int | None, result: dict[str, Any]) -> None:
        """The `persona_media` job ended: keep what came of the generation."""
        outcome, detail, media_message_id = generation_outcome(result)
        await self.update(record_id, outcome=outcome, detail=detail, media_message_id=media_message_id)

    async def delivered(self, media: Message, *, ok: bool = True) -> None:
        """A generated photo or note was sent (or its send failed): close its decision."""
        for row, decision in reversed(await self.recent(media.dialog_id, CONTEXT_RECORDS)):
            if decision.media_message_id == media.id:
                await self.update(row.id, outcome=SENT if ok else SEND_FAILED)
                return

    async def brief(self, dialog_id: int, timezone: str) -> str:
        """The «РАНЕЕ ПО МЕДИА» block for the next reply: her latest decisions, "" when none."""
        since = self.clock() - timedelta(hours=CONTEXT_HOURS)
        lines = [
            decision.context_line(local_time(timezone, row.created_at))
            for row, decision in await self.recent(dialog_id, CONTEXT_RECORDS)
            if row.created_at >= since
        ]
        return "\n".join([EARLIER, *lines]) if lines else ""
