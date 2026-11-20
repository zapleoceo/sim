"""Optional speech stage. Commit a one-shot submission intent before any Broker POST."""
from __future__ import annotations

import base64
import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.audio import AudioError
from sim.adapters.db.models import AdminUser, Persona, PreviewOperation, PreviewSpeech
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.session import transaction
from sim.domain.voice import speaks
from sim.ports.speech import SpeechError
from sim.ports.speech_jobs import InvalidSpeechAudio, SpeechResultExpired
from sim.services.job_execution import require_live_execution
from sim.services.jobkinds import PREVIEW_SPEECH
from sim.services.persona_ambience import voice_ambience

if TYPE_CHECKING:
    from sim.wiring import Container

TERMINAL = frozenset(("done", "error", "unknown", "cancelled"))


def enabled(c: Container) -> bool:
    return c.settings.preview_async_enabled and c.settings.preview_speech_jobs_enabled and c.preview_speech is not None


def voice_plan(c: Container, persona: Persona, voice_input: bool) -> dict[str, str]:
    if not speaks(persona.voice_mode, persona.voice_id, "voice" if voice_input else None):
        return {}
    return {
        "voice": persona.voice_id, "style": persona.voice_style, "workflow": "voice-reply",
        "ambience": voice_ambience(persona.voice_ambience, persona.routine, persona.timezone, c.clock()),
    }


async def stage(c: Container, s: AsyncSession, operation: PreviewOperation, reply: str,
                plan: dict[str, str]) -> None:
    if not plan or not reply:
        return
    now = c.clock()
    active = enabled(c)
    millis = int(now.replace(tzinfo=UTC).timestamp() * 1000)
    s.add(PreviewSpeech(
        operation_id=operation.id, broker_key=f"tts-{millis}-{uuid4()}",
        status="ready" if active else "cancelled",
        deadline_at=min(operation.expires_at, now + timedelta(minutes=3)),
        request_cipher=c.box.encrypt_json({**plan, "text": reply}) if active else None,
    ))
    if active:
        await JobRepo(s).enqueue(PREVIEW_SPEECH, {"key": operation.id}, now)


async def reserve_submission(c: Container, operation_id: str) -> bool:
    await require_live_execution()
    async with transaction(c.factory) as s:
        # A crash after this commit permits lookup only. It never grants another POST.
        reserved = await s.scalar(update(PreviewSpeech).where(
            PreviewSpeech.operation_id == operation_id, PreviewSpeech.status == "ready",
        ).values(status="lookup").returning(PreviewSpeech.operation_id))
        return reserved is not None


def finish(row: PreviewSpeech, status: str) -> None:
    row.status = status
    row.request_cipher = None


async def advance(c: Container, s: AsyncSession, payload: dict[str, Any]) -> dict[str, str]:
    key = payload["key"]
    operation = await s.get(PreviewOperation, key)
    row = await s.get(PreviewSpeech, key)
    if operation is None or row is None or row.status in TERMINAL:
        return {"reason": "settled"}
    if row.deadline_at <= c.clock():
        finish(row, "unknown" if row.status != "ready" else "cancelled")
        return {"reason": row.status}
    owner = await s.get(AdminUser, operation.owner_id) if operation.owner_id is not None else None
    if owner is None or not owner.is_active or await s.get(Persona, operation.persona_id) is None:
        finish(row, "cancelled")
        return {"reason": row.status}
    port = c.preview_speech
    if port is None or (row.status == "ready" and not enabled(c)):
        finish(row, "cancelled")
        return {"reason": row.status}

    request = c.box.decrypt_json(row.request_cipher)
    ambience = str(request.pop("ambience", "off"))
    delay = 3.0
    try:
        if row.status == "ready" and await reserve_submission(c, key):
            receipt = await port.submit_speech(row.broker_key, request)
        elif row.broker_id is None:
            receipt = await port.speech_by_key(row.broker_key)
        else:
            receipt = await port.speech_status(row.broker_id)
        if receipt is not None:
            row.broker_id = receipt.job_id
            if receipt.status in ("error", "unknown", "cancelled"):
                finish(row, receipt.status)
            elif receipt.status == "done":
                wav = await port.speech_result(receipt.job_id)
                encoded = await c.encoder.to_voice_note(wav, ambience)
                if len(encoded) > 2 * 1024 * 1024:
                    raise AudioError("Encoded preview speech exceeds limit")
                if operation.expires_at > c.clock():
                    row.audio_cipher = c.box.encrypt_json({"ogg": base64.b64encode(encoded).decode()})
                    finish(row, "done")
                else:
                    finish(row, "cancelled")
            else:
                row.status = "poll"
                if receipt.poll_after_s is not None and math.isfinite(receipt.poll_after_s):
                    delay = min(15.0, max(2.0, receipt.poll_after_s))
    except (AudioError, InvalidSpeechAudio, SpeechResultExpired):
        finish(row, "error")
    except SpeechError:
        # Timeout, lost acknowledgement, and ambiguous rejection only permit read retries.
        row.status = "lookup" if row.broker_id is None else "poll"
    if row.status not in TERMINAL:
        next_run = min(c.clock() + timedelta(seconds=delay), row.deadline_at)
        await JobRepo(s).enqueue(PREVIEW_SPEECH, {"key": key}, next_run)
    return {"reason": row.status}


async def expire(s: AsyncSession, now: datetime) -> None:
    await s.execute(update(PreviewSpeech).where(
        PreviewSpeech.operation_id.in_(select(PreviewOperation.id).where(PreviewOperation.expires_at <= now)),
        PreviewSpeech.request_cipher.is_not(None) | PreviewSpeech.audio_cipher.is_not(None),
    ).values(request_cipher=None, audio_cipher=None, status="cancelled"))
