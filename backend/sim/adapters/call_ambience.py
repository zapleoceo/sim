"""Quiet local ambience for live calls, never another model/provider request.

One bounded ffmpeg render before the call produces an eight-second mono PCM bed from the
same presets/assets as voice notes. Only the bed is levelled; model speech is not filtered
or normalised. The real-time path does no IO and allocates only one 10 ms frame at a time.
"""
from __future__ import annotations

import logging
import random
from array import array

from sim.adapters.ambience import CHATTER, cafe_graph, room_graph, street_graph
from sim.adapters.audio import AudioError, Runner, run_process
from sim.adapters.pcm import _bytes, _samples, frame_bytes
from sim.domain.enums import VoiceAmbience

log = logging.getLogger(__name__)

BED_SECONDS = 8
PREPARE_TIMEOUT_S = 3.0
BED_PEAK = 256             # at most -42 dBFS; perceptual balance still needs a live listen
SPEECH_GAIN = 0.25         # another 12 dB down while model speech is present
SUPPORTED_RATES = (16000, 24000, 48000)


class CallAmbience:
    """A per-call loop with smooth gain changes and saturation-safe additive mixing."""

    def __init__(self, pcm: bytes, rate_hz: int) -> None:
        if rate_hz not in SUPPORTED_RATES:
            raise ValueError("unsupported ambience sample rate")
        self.frame_size = frame_bytes(rate_hz)
        self.frame_samples = self.frame_size // 2
        samples = _samples(pcm)
        if len(samples) < self.frame_samples or len(pcm) % 2:
            raise ValueError("empty or incomplete ambience PCM")
        # Keep complete frames, bound a corrupted/injected runner result, and remove DC.
        count = min(len(samples) // self.frame_samples * self.frame_samples,
                    rate_hz * BED_SECONDS)
        samples = samples[:count]
        mean = sum(samples) / len(samples)
        centred = [x - mean for x in samples]
        peak = max(abs(x) for x in centred)
        if not peak:
            raise ValueError("silent ambience PCM")
        scale = BED_PEAK / peak
        fade = min(rate_hz // 50, len(samples) // 2)  # 20 ms fade through each loop seam
        self.bed = array("h", (
            round(value * scale * min(1.0, i / fade, (count - 1 - i) / fade))
            for i, value in enumerate(centred)
        ))
        self.position = 0
        self.gain = 0.0       # first frames fade in rather than clicking at answer

    def mix(self, frame: bytes | None) -> bytes:
        """Mix a model frame, or a silent frame while listening. Caller audio is never mixed."""
        if frame is not None and len(frame) != self.frame_size:
            raise ValueError("ambience expects one complete 10 ms PCM frame")
        voice = _samples(frame) if frame is not None else array("h", [0]) * self.frame_samples
        # Duck whenever model audio exists, including quiet speech and encoded pauses.
        # A loudness threshold would otherwise leave the bed louder around whispers.
        speaking = frame is not None
        target = SPEECH_GAIN if speaking else 1.0
        # Immediate 10 ms duck, slower 250 ms release, with a per-sample ramp.
        next_gain = target if target < self.gain else min(target, self.gain + 0.04)
        delta = (next_gain - self.gain) / self.frame_samples
        out = array("h")
        for i, sample in enumerate(voice):
            noise = round(self.bed[self.position + i] * (self.gain + delta * (i + 1)))
            out.append(max(-32768, min(32767, sample + noise)))
        self.gain = next_gain
        self.position = (self.position + self.frame_samples) % len(self.bed)
        return _bytes(out)


def bed_args(preset: str, rate_hz: int) -> tuple[str, ...] | None:
    """A finite local render. Unknown/auto/random must be resolved before this adapter."""
    if preset not in (VoiceAmbience.ROOM, VoiceAmbience.STREET, VoiceAmbience.CAFE):
        return None
    rng = random.Random()  # noqa: S311 - a noise seed, not security
    seed = rng.randrange(1, 1_000_000)
    head = ("ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            f"anullsrc=r={rate_hz}:cl=mono:d={BED_SECONDS}")
    if preset == VoiceAmbience.CAFE:
        head += ("-stream_loop", "-1", "-i", str(CHATTER))
        graph = cafe_graph(seed)
    elif preset == VoiceAmbience.STREET:
        graph = street_graph(seed)
    else:
        graph = room_graph(seed)
    return (*head, "-filter_complex", graph, "-t", str(BED_SECONDS), "-ac", "1",
            "-ar", str(rate_hz), "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1")


async def prepare_call_ambience(
    preset: str, rate_hz: int, *, runner: Runner = run_process,
) -> CallAmbience | None:
    """Best effort: missing assets/ffmpeg, timeout or invalid PCM leave a clean call.

Cancellation is not swallowed: the shared process runner terminates/reaps its child.
No shell, network, paid generation or unbounded background process is involved.
"""
    if rate_hz not in SUPPORTED_RATES:
        return None
    args = bed_args(preset, rate_hz)
    if args is None:
        return None
    try:
        code, out, _ = await runner(args, b"", PREPARE_TIMEOUT_S)
        expected = rate_hz * BED_SECONDS * 2
        if code or len(out) != expected:
            raise AudioError("ambience render returned invalid PCM")
        return CallAmbience(out, rate_hz)
    except (AudioError, OSError, ValueError, TimeoutError) as exc:
        log.warning("call ambience unavailable (%s); using clean audio", type(exc).__name__)
        return None
