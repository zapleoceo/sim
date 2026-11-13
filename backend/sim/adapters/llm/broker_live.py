"""AIbroker live voice client: implements ports.live_voice.LiveVoicePort over WebSocket /v1/live.

One socket is one conversation:
  -> {"type": "start", voice, system, language, greeting, workflow, max_seconds}
  <- {"type": "ready", input_rate_hz, output_rate_hz, ...}
  -> binary caller PCM ... {"type": "end"}
  <- binary model PCM, {"type": "transcript" | "interrupted" | "turn_complete"} ...
  <- {"type": "done", transcript, duration_s, cost_usd, ...} and the socket closes.
A refusal is {"type": "error", "code", "detail"} followed by a close (4000 + HTTP status).

A single reader task owns the socket's receive side, so `events()` and `finish()` never read
concurrently."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sim.ports.live_voice import (
    AUDIO,
    INTERRUPTED,
    TRANSCRIPT,
    TURN_COMPLETE,
    LiveVoiceError,
    LiveVoiceEvent,
    LiveVoiceSummary,
)

log = logging.getLogger(__name__)

LIVE_PATH = "/v1/live"
USER_AGENT = "sim-live/1.0"          # Cloudflare in front of the Broker refuses a bare client
CONNECT_TIMEOUT_S = 15.0
READY_TIMEOUT_S = 45.0               # the Broker walks its keys while connecting to Google
DONE_TIMEOUT_S = 15.0
_END = object()

Connect = Callable[[str, dict[str, str]], Awaitable[Any]]


def live_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + LIVE_PATH


async def websocket_connect(url: str, headers: dict[str, str]) -> Any:  # pragma: no cover - network
    from websockets.asyncio.client import connect  # noqa: PLC0415

    return await connect(url, additional_headers=headers, user_agent_header=USER_AGENT,
                         max_size=None, open_timeout=CONNECT_TIMEOUT_S)


def summary_of(done: dict[str, Any]) -> LiveVoiceSummary:
    turns = [(str(t.get("role") or ""), str(t.get("text") or ""))
             for t in done.get("transcript") or [] if isinstance(t, dict)]

    def num(name: str) -> float | None:
        value = done.get(name)
        return float(value) if isinstance(value, int | float) else None

    tokens_in, tokens_out = num("tokens_in"), num("tokens_out")
    return LiveVoiceSummary(
        transcript=[(role, text) for role, text in turns if role and text.strip()],
        duration_s=num("duration_s"), cost_usd=num("cost_usd"),
        model=done.get("model") if isinstance(done.get("model"), str) else None,
        tokens_in=int(tokens_in) if tokens_in is not None else None,
        tokens_out=int(tokens_out) if tokens_out is not None else None,
        end_reason=str(done.get("end_reason") or ""),
    )


def event_of(message: dict[str, Any]) -> LiveVoiceEvent | None:
    kind = message.get("type")
    if kind == TRANSCRIPT:
        return LiveVoiceEvent(TRANSCRIPT, role=str(message.get("role") or ""),
                              text=str(message.get("text") or ""))
    if kind in (INTERRUPTED, TURN_COMPLETE):
        return LiveVoiceEvent(str(kind))
    return None


class BrokerLiveSession:
    def __init__(self, ws: Any, ready: dict[str, Any]) -> None:
        self.ws = ws
        self.input_rate_hz = int(ready.get("input_rate_hz") or 16000)
        self.output_rate_hz = int(ready.get("output_rate_hz") or 24000)
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.summary = LiveVoiceSummary(end_reason="no_summary")
        self.finished = False
        self.reader = asyncio.ensure_future(self.read())

    async def read(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    self.queue.put_nowait(LiveVoiceEvent(AUDIO, pcm=raw))
                    continue
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "done":
                    self.summary = summary_of(message)
                    break
                event = event_of(message)
                if event is not None:
                    self.queue.put_nowait(event)
        except Exception as exc:   # a dropped socket ends the conversation, it is not a crash
            log.warning("live session read ended: %s", type(exc).__name__)
        finally:
            self.queue.put_nowait(_END)

    async def send_audio(self, pcm: bytes) -> None:
        if not self.finished and pcm:
            await self.ws.send(pcm)

    async def events(self) -> AsyncIterator[LiveVoiceEvent]:
        while True:
            item = await self.queue.get()
            if item is _END:
                self.queue.put_nowait(_END)       # every later reader stops too
                return
            yield item

    async def finish(self) -> LiveVoiceSummary:
        if not self.finished:
            self.finished = True
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"type": "end"}))
            try:
                await asyncio.wait_for(asyncio.shield(self.reader), DONE_TIMEOUT_S)
            except TimeoutError:
                self.reader.cancel()
            with contextlib.suppress(Exception):
                await self.ws.close()
        return self.summary


class BrokerLiveVoice:
    def __init__(self, base_url: str, project_key: str, *,
                 connect: Connect = websocket_connect) -> None:
        self.url = live_url(base_url)
        self.key = project_key
        self.connect = connect

    async def open(
        self, *, voice: str, system: str, language: str | None = None,
        greeting: str | None = None, workflow: str | None = None,
        max_seconds: float | None = None,
    ) -> BrokerLiveSession:
        start: dict[str, Any] = {"type": "start", "voice": voice, "system": system}
        for name, value in (("language", language), ("greeting", greeting),
                            ("workflow", workflow), ("max_seconds", max_seconds)):
            if value:
                start[name] = value
        try:
            ws = await self.connect(self.url, {"X-Project-Key": self.key})
        except Exception as exc:
            raise LiveVoiceError(f"live: connect failed: {type(exc).__name__}: {exc}"[:300]) from exc
        try:
            await ws.send(json.dumps(start, ensure_ascii=False))
            first = json.loads(await asyncio.wait_for(ws.recv(), READY_TIMEOUT_S))
        except Exception as exc:
            with contextlib.suppress(Exception):
                await ws.close()
            raise LiveVoiceError(f"live: no answer to start: {type(exc).__name__}") from exc
        if not isinstance(first, dict) or first.get("type") != "ready":
            with contextlib.suppress(Exception):
                await ws.close()
            detail = first.get("detail") if isinstance(first, dict) else None
            code = first.get("code") if isinstance(first, dict) else None
            raise LiveVoiceError(f"live: refused ({code}): {detail}"[:300])
        return BrokerLiveSession(ws, first)
