"""Raw voice-call audio: 16-bit little-endian mono PCM between Telegram calls (48 kHz) and the
live voice model (16 kHz in, 24 kHz out). Pure Python: a call moves ~100 KB/s, well within
what array arithmetic handles without numpy."""
from __future__ import annotations

import sys
from array import array

SAMPLE_BYTES = 2
FRAME_MS = 10          # Telegram call frames (ntgcalls) are 10 ms long


def frame_bytes(rate_hz: int, ms: int = FRAME_MS) -> int:
    return rate_hz * ms // 1000 * SAMPLE_BYTES


def _samples(pcm: bytes) -> array:
    data = array("h")
    data.frombytes(pcm[: len(pcm) - len(pcm) % SAMPLE_BYTES])
    if sys.byteorder != "little":  # pragma: no cover - the servers are little-endian
        data.byteswap()
    return data


def _bytes(data: array) -> bytes:
    if sys.byteorder != "little":  # pragma: no cover
        data.byteswap()
    return data.tobytes()


def resample(pcm: bytes, src_hz: int, dst_hz: int) -> bytes:
    """Rate conversion of one chunk. An integer downsampling factor averages each group (a crude
    low-pass, enough for speech); anything else interpolates linearly."""
    if src_hz == dst_hz or not pcm:
        return pcm[: len(pcm) - len(pcm) % SAMPLE_BYTES]
    src = _samples(pcm)
    if not src:
        return b""
    if src_hz % dst_hz == 0:
        step = src_hz // dst_hz
        out = array("h", (sum(src[i:i + step]) // len(src[i:i + step])
                          for i in range(0, len(src), step)))
        return _bytes(out)
    count = max(1, len(src) * dst_hz // src_hz)
    last = len(src) - 1
    ratio = src_hz / dst_hz
    out = array("h")
    for n in range(count):
        pos = n * ratio
        i = int(pos)
        frac = pos - i
        a = src[min(i, last)]
        b = src[min(i + 1, last)]
        out.append(int(a + (b - a) * frac))
    return _bytes(out)


class PcmBuffer:
    """FIFO of PCM that hands out fixed-size frames (the outgoing side of a call is paced at
    real time while the model produces speech in faster bursts)."""

    def __init__(self, frame_size: int) -> None:
        self.frame_size = frame_size
        self.data = bytearray()

    def push(self, pcm: bytes) -> None:
        self.data.extend(pcm)

    def pop_frame(self) -> bytes | None:
        if len(self.data) < self.frame_size:
            return None
        frame = bytes(self.data[: self.frame_size])
        del self.data[: self.frame_size]
        return frame

    def flush_tail(self) -> bytes | None:
        """The last partial frame, padded with silence (end of a model turn)."""
        if not self.data:
            return None
        frame = bytes(self.data).ljust(self.frame_size, b"\x00")
        self.data.clear()
        return frame

    def clear(self) -> None:
        self.data.clear()

    def __len__(self) -> int:
        return len(self.data)
