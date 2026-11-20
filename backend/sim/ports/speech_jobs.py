"""Durable asynchronous Broker speech job interface."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field, StrictInt

from sim.ports.speech import SpeechError

SpeechState = Literal["pending", "running", "done", "error", "unknown", "cancelled"]


class SpeechJob(BaseModel):
    job_id: StrictInt = Field(gt=0, le=2**63 - 1)
    status: SpeechState
    poll_after_s: float | None = None


class InvalidSpeechAudio(SpeechError):
    pass


class SpeechResultExpired(SpeechError):
    pass


class SpeechJobs(Protocol):
    async def submit_speech(self, key: str, body: dict[str, str]) -> SpeechJob: ...
    async def speech_by_key(self, key: str) -> SpeechJob | None: ...
    async def speech_status(self, job_id: int) -> SpeechJob: ...
    async def speech_result(self, job_id: int) -> bytes: ...
