"""TranscriberPort over the Broker's keyed transcription jobs.

`POST /v1/transcribe/keyed-jobs` (multipart `file`, header `Idempotency-Key`), then the poll of
the typed `poll_url`. The key is derived from the audio bytes, file name and workflow, so every
retry of the same voice note sends the same key: the Broker replays the existing job instead of
paying for a second transcription. When the submit answer is lost, the job is looked up by key
(`GET /v1/transcribe/jobs/by-key/{key}`). Per the Broker contract there is never a fallback to
the unkeyed `/v1/transcribe/jobs` or the synchronous `/v1/transcribe`."""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

from sim.adapters.broker.http import BrokerHttp, BrokerHttpError, usage_of
from sim.ports import ChatResult, PortError

KEYED_PATH = "/v1/transcribe/keyed-jobs"
JOB_PATH = "/v1/transcribe/jobs/{job_id}"
BY_KEY_PATH = "/v1/transcribe/jobs/by-key/{key}"
KEY_PREFIX = "sim-tr-"
DEFAULT_FILENAME = "voice.ogg"
DEFAULT_WORKFLOW = "sim-voice"


def idempotency_key(audio: bytes, filename: str, workflow: str) -> str:
    """Stable per input: the Broker answers 409 to one key with different bytes, so the key
    covers everything it compares (audio, effective filename, workflow)."""
    digest = hashlib.sha256(f"{workflow}\n{filename}\n".encode() + audio).hexdigest()
    return KEY_PREFIX + digest


class BrokerTranscriber:
    def __init__(self, http: BrokerHttp, *, workflow: str = DEFAULT_WORKFLOW) -> None:
        self.http = http
        self.workflow = workflow

    async def transcribe(self, *, audio: bytes, filename: str, model: str = "") -> ChatResult:
        """`model` is accepted for the port but not sent: the keyed route has no pin, the
        Broker walks its transcription chain."""
        if not audio:
            raise PortError("transcribe: empty audio")
        name = filename or DEFAULT_FILENAME
        key = idempotency_key(audio, name, self.workflow)
        async with self.http.slot("transcribe"):
            start = self.http.clock()
            job = await self._submit(audio, name, key)
            job_id = str(job.get("job_id") or "")
            data = await self.http.poll(job, what="transcribe",
                                        budget_s=self.http.poll_budget_s,
                                        default_path=JOB_PATH.format(job_id=job_id))
        text = data.get("text")
        return ChatResult(
            text=text.strip() if isinstance(text, str) else "",
            usage=usage_of(data, model=model, latency_ms=self.http.elapsed_ms(start),
                           job_id=job_id or None),
            raw=data,
        )

    async def _submit(self, audio: bytes, filename: str, key: str) -> dict[str, Any]:
        try:
            response = await self.http.request(
                "POST", KEYED_PATH, what="transcribe", params={"workflow": self.workflow},
                files={"file": (filename, audio, "application/octet-stream")},
                headers={"Idempotency-Key": key},
            )
        except BrokerHttpError as exc:
            if exc.status == 404:
                # An older replica during a rolling upgrade does not know the keyed route yet.
                raise PortError(f"{exc} (keyed transcription route missing)",
                                retryable=True) from exc
            if not exc.retryable:
                raise
            found = await self._by_key(key)     # the job may have been admitted anyway
            if found is None:
                raise
            return found
        return self.http.decode(response, what="transcribe")

    async def _by_key(self, key: str) -> dict[str, Any] | None:
        try:
            return await self.http.json("GET", BY_KEY_PATH.format(key=quote(key, safe="")),
                                        what="transcribe")
        except PortError:
            return None
