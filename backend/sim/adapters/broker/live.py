"""LivePort over the Broker WebSocket `/v1/live` (Gemini Live). One socket is one call:

  -> {"type": "start", model, voice, system, language, greeting, max_seconds, workflow}
  <- {"type": "ready", input_rate_hz: 16000, output_rate_hz: 24000, max_seconds}
  -> binary 16 kHz mono s16le PCM ... {"type": "end"}
  <- binary 24 kHz PCM, {"type": "transcript", role, text}, {"type": "interrupted"} ...
  <- {"type": "done", transcript, duration_s, tokens_in, tokens_out, cost_usd, end_reason}
A refusal is {"type": "error", code, detail} and a close with 4000 + HTTP status.

One reader task owns the receive side, so `events()` and `finish()` never read concurrently."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from websockets.asyncio.client import connect as ws_connect

from sim.adapters.broker.http import USER_AGENT, BrokerHttp, as_int
from sim.ports import LiveEvent, PortError

log = logging.getLogger(__name__)

LIVE_PATH = "/v1/live"
CONNECT_TIMEOUT_S = 15.0
READY_TIMEOUT_S = 45.0          # the Broker walks its keys while connecting to Google
DONE_TIMEOUT_S = 15.0
DEFAULT_WORKFLOW = "sim-call"
RETRYABLE_LIVE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
ERROR_TEXT_LIMIT = 300

type Connect = Callable[[str, dict[str, str]], Awaitable[Any]]


def live_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base.removeprefix("https://")
    elif base.startswith("http://"):
        base = "ws://" + base.removeprefix("http://")
    return base + LIVE_PATH


async def websocket_connect(url: str, headers: dict[str, str]) -> Any:  # pragma: no cover
    # Cloudflare in front of the Broker refuses a client without a User-Agent.
    return await ws_connect(url, additional_headers=headers, user_agent_header=USER_AGENT,
                            max_size=None, open_timeout=CONNECT_TIMEOUT_S)


def start_message(*, model: str, system: str, voice: str, language: str, greeting: str,
                  max_seconds: int, workflow: str) -> dict[str, Any]:
    message: dict[str, Any] = {"type": "start", "system": system, "workflow": workflow}
    for name, value in (("model", model), ("voice", voice), ("language", language),
                        ("greeting", greeting)):
        if value:
            message[name] = value
    if max_seconds > 0:
        message["max_seconds"] = int(max_seconds)
    return message


def parse_message(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str | bytes | bytearray):
        return None
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


def refusal_error(message: Mapping[str, Any]) -> PortError:
    """`{"type": "error", "code", "detail"}` → PortError; the code is an HTTP status or the
    WebSocket close code (4000 + status). Caps, overload and timeouts are retryable."""
    status = as_int(message.get("code"))
    if status is not None and status >= 4000:
        status -= 4000
    detail = str(message.get("detail") or message.get("type") or "no answer")[:ERROR_TEXT_LIMIT]
    return PortError(f"live: refused ({message.get('code')}): {detail}",
                     retryable=status in RETRYABLE_LIVE_STATUS)


def event_of(message: Mapping[str, Any]) -> LiveEvent | None:
    """A JSON frame as a LiveEvent. `interrupted` (the caller talked over the model) is passed
    on so the call can drop the audio it has not played yet."""
    kind = message.get("type")
    if kind == "transcript":
        role = message.get("role")
        text = str(message.get("text") or "")
        if role == "user":
            return LiveEvent("transcript_in", text=text)
        if role == "assistant":
            return LiveEvent("transcript_out", text=text)
        return None
    if kind == "interrupted":
        return LiveEvent("interrupted")
    if kind == "error":     # after `ready`: the call goes quiet, the Broker closes it
        return LiveEvent("error", text=str(message.get("detail") or "")[:ERROR_TEXT_LIMIT],
                         data={"code": message.get("code")})
    return None


async def close_quietly(ws: Any) -> None:
    with contextlib.suppress(Exception):
        await ws.close()


class BrokerLiveSession:
    def __init__(self, ws: Any, ready: Mapping[str, Any], *,
                 done_timeout_s: float = DONE_TIMEOUT_S) -> None:
        self.ws = ws
        self.ready = dict(ready)
        self.done_timeout_s = done_timeout_s
        self.summary: dict[str, Any] = {}
        self._finished = False
        self._queue: asyncio.Queue[LiveEvent | None] = asyncio.Queue()
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes | bytearray):
                    self._queue.put_nowait(LiveEvent("audio", audio=bytes(raw)))
                    continue
                message = parse_message(raw)
                if message is None:
                    continue
                if message.get("type") == "done":
                    self.summary = message
                    self._queue.put_nowait(LiveEvent("done", data=message))
                    break
                event = event_of(message)
                if event is not None:
                    self._queue.put_nowait(event)
        except Exception as exc:    # a dropped socket ends the call; it is not a crash
            log.warning("live session read ended: %s", type(exc).__name__)
            self._queue.put_nowait(LiveEvent("error", text=f"socket: {type(exc).__name__}"))
        finally:
            self._queue.put_nowait(None)

    async def send_audio(self, pcm16k: bytes) -> None:
        if self._finished or not pcm16k:
            return
        try:
            await self.ws.send(pcm16k)
        except Exception as exc:
            raise PortError(f"live: send failed: {type(exc).__name__}") from exc

    async def events(self) -> AsyncIterator[LiveEvent]:
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.put_nowait(None)     # every later reader stops too
                return
            yield item

    async def finish(self) -> dict[str, Any]:
        """Hang up, wait briefly for the Broker's `done` summary and close. Returns the summary
        (transcript, duration_s, tokens, cost_usd, end_reason) or {} when none arrived."""
        if not self._finished:
            self._finished = True
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"type": "end"}))
            try:
                await asyncio.wait_for(asyncio.shield(self._reader), self.done_timeout_s)
            except TimeoutError:
                self._reader.cancel()
            await close_quietly(self.ws)
        return dict(self.summary)


class BrokerLive:
    def __init__(self, base_url: str, project_key: str, *, connect: Connect = websocket_connect,
                 ready_timeout_s: float = READY_TIMEOUT_S, done_timeout_s: float = DONE_TIMEOUT_S,
                 workflow: str = DEFAULT_WORKFLOW) -> None:
        self.url = live_url(base_url)
        self.project_key = project_key
        self.connect = connect
        self.ready_timeout_s = ready_timeout_s
        self.done_timeout_s = done_timeout_s
        self.workflow = workflow

    @classmethod
    def from_http(cls, http: BrokerHttp, **kwargs: Any) -> BrokerLive:
        return cls(http.base_url, http.project_key, **kwargs)

    async def open(self, *, model: str, system: str, voice: str, language: str,
                   greeting: str, max_seconds: int) -> BrokerLiveSession:
        start = start_message(model=model, system=system, voice=voice, language=language,
                              greeting=greeting, max_seconds=max_seconds,
                              workflow=self.workflow)
        try:
            ws = await self.connect(self.url, {"X-Project-Key": self.project_key})
        except Exception as exc:
            raise PortError(f"live: connect failed: {type(exc).__name__}: {exc}"[:ERROR_TEXT_LIMIT],
                            retryable=True) from exc
        try:
            await ws.send(json.dumps(start, ensure_ascii=False))
            first = parse_message(await asyncio.wait_for(ws.recv(), self.ready_timeout_s))
        except Exception as exc:
            await close_quietly(ws)
            raise PortError(f"live: no answer to start: {type(exc).__name__}",
                            retryable=True) from exc
        if first is None or first.get("type") != "ready":
            await close_quietly(ws)
            raise refusal_error(first or {})
        return BrokerLiveSession(ws, first, done_timeout_s=self.done_timeout_s)
