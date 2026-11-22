"""The audio recording of a call (docs/calls.md, «Запись звонка»): the client on the left
channel, her on the right, each placed by the time it was heard, so pauses and interruptions
sound as they were. Kept in memory at 16 kHz (a 9-minute call is about 35 MB), turned into
stereo OGG/Opus by ffmpeg after the call (`MediaToolsPort.call_recording`). Pure Python."""
from __future__ import annotations

from array import array

from sim.telegram.calls.pcm import SAMPLE_BYTES, _bytes, _samples, resample

RECORD_HZ = 16_000


class CallRecorder:
    def __init__(self, rate_hz: int = RECORD_HZ) -> None:
        self.rate_hz = rate_hz
        self.sides: dict[str, array[int]] = {"client": array("h"), "persona": array("h")}

    def client(self, pcm: bytes, src_hz: int, at_s: float) -> None:
        """A frame of the other side heard `at_s` seconds after the call was picked up."""
        self._put("client", pcm, src_hz, at_s)

    def persona(self, pcm: bytes, src_hz: int, at_s: float) -> None:
        """A frame of hers sent into the call `at_s` seconds after it was picked up."""
        self._put("persona", pcm, src_hz, at_s)

    def _put(self, side: str, pcm: bytes, src_hz: int, at_s: float) -> None:
        samples = _samples(resample(pcm, src_hz, self.rate_hz))
        track = self.sides[side]
        start = max(int(at_s * self.rate_hz), len(track))
        if start > len(track):
            track.extend(array("h", bytes(SAMPLE_BYTES * (start - len(track)))))
        track.extend(samples)

    @property
    def seconds(self) -> float:
        return max(len(track) for track in self.sides.values()) / self.rate_hz

    def stereo(self) -> bytes:
        """Interleaved s16le stereo (left = client, right = persona), equal length; b"" when
        nothing was heard at all."""
        left, right = self.sides["client"], self.sides["persona"]
        size = max(len(left), len(right))
        if size == 0:
            return b""
        out = array("h", bytes(SAMPLE_BYTES * 2 * size))
        out[0:2 * len(left):2] = left
        out[1:2 * len(right):2] = right
        return _bytes(out)
