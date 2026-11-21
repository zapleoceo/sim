"""Shared bits of the adapter tests: a fake clock, Broker answers and tiny generated media."""
from __future__ import annotations

import io
import json
import math
import struct
import wave
import zlib
from typing import Any

import httpx
import respx

BASE_URL = "https://broker.test"
PROJECT_KEY = "test-project-key"


class FakeTime:
    """`clock` + `sleep` for BrokerHttp: sleeping advances the clock instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def submitted(job_id: int = 7, *, poll_url: str | None = None) -> httpx.Response:
    return httpx.Response(202, json={"job_id": job_id, "status": "pending",
                                     "poll_url": poll_url or f"/v1/jobs/{job_id}",
                                     "poll_after_s": 2})


def pending(job_id: int = 7, poll_after_s: float = 5) -> httpx.Response:
    return httpx.Response(200, json={"job_id": job_id, "status": "pending",
                                     "poll_after_s": poll_after_s})


def done(job_id: int = 7, **fields: Any) -> httpx.Response:
    return httpx.Response(200, json={"job_id": job_id, "status": "done", **fields})


def failed(job_id: int = 7, error: str = "boom", **fields: Any) -> httpx.Response:
    return httpx.Response(200, json={"job_id": job_id, "status": "error", "error": error,
                                     **fields})


def sent_json(route: respx.Route, index: int = -1) -> dict[str, Any]:
    return json.loads(route.calls[index].request.content)


def wav_bytes(seconds: float = 0.5, rate: int = 24_000) -> bytes:
    """A mono 16-bit sine tone, like the Broker's TTS output."""
    frames = int(seconds * rate)
    samples = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / rate)))
                       for n in range(frames))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples)
    return buffer.getvalue()


def png_bytes(width: int, height: int) -> bytes:
    """A plain RGB PNG built by hand (no imaging library needed)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    row = b"\x00" + b"\x80\x20\x20" * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def pdf_bytes(text: str) -> bytes:
    """A one-page PDF with `text` in Helvetica, with a correct xref table."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return bytes(out)
