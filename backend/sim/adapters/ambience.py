"""Smartphone-recording ambience for persona voice notes (ffmpeg filter graphs).

Gemini TTS is studio-clean. A real voice note is recorded by a phone microphone somewhere: the
voice and the room around it go through the same mic (band-limited, presence bump, automatic gain
that lifts the background between phrases, mild saturation) and a low-bitrate Opus encoder.
Pronunciation is untouched: only the "recording" changes.

Presets (approved by the owner on 2026-11-12 by ear): `room` (ventilation hiss, fridge hum, wall
reflections), `street` (passing traffic, city rumble, wind), `cafe` (distant chatter, hall noise,
reverb). `random` picks one of them per note. Noise seeds differ per note, so no two notes share
the same background. The cafe chatter is a bundled clip (`assets/ambience/cafe_chatter.ogg`,
three Gemini voices mixed far away) looped from a random offset."""
from __future__ import annotations

import random
from pathlib import Path

from sim.domain.enums import VoiceAmbience

CHATTER = Path(__file__).resolve().parent.parent / "assets" / "ambience" / "cafe_chatter.ogg"
CHATTER_S = 9.0          # length of the bundled chatter clip, for the random start offset

PRESETS: tuple[VoiceAmbience, ...] = (VoiceAmbience.ROOM, VoiceAmbience.STREET, VoiceAmbience.CAFE)

# The phone microphone: everything (voice + background) passes through it, in this order.
PHONE_MIC = (
    "highpass=f=160,lowpass=f=7200,"
    "equalizer=f=3000:t=q:w=1.4:g=4,equalizer=f=250:t=q:w=1:g=-3,"
    "acompressor=threshold=-22dB:ratio=4:attack=4:release=120:makeup=3,"
    "asoftclip=type=tanh:param=1.2"
)
# Messenger voice notes are low-bitrate Opus.
LOFI_OUT: tuple[str, ...] = (
    "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "24k", "-application", "voip",
    "-f", "ogg", "pipe:1",
)


def room_graph(seed: int) -> str:
    return (
        "[0]aresample=48000,aecho=0.8:0.55:12|23|37:0.22|0.14|0.08[v];"
        f"anoisesrc=color=pink:amplitude=0.035:r=48000:seed={seed},bandpass=f=900:w=1800[fan];"
        "sine=f=100:r=48000,volume=0.012[h1];sine=f=200:r=48000,volume=0.008[h2];"
        f"[v][fan][h1][h2]amix=inputs=4:duration=first:normalize=0,{PHONE_MIC}"
    )


def street_graph(seed: int) -> str:
    return (
        "[0]aresample=48000[v];"
        f"anoisesrc=color=brown:amplitude=0.5:r=48000:seed={seed},highpass=180,lowpass=1600,"
        "tremolo=f=0.12:d=0.75[t];"
        f"anoisesrc=color=pink:amplitude=0.04:r=48000:seed={seed + 1},bandpass=f=600:w=900,"
        "tremolo=f=0.1:d=0.6[c];"
        f"anoisesrc=color=white:amplitude=0.012:r=48000:seed={seed + 2},highpass=3000,"
        "tremolo=f=0.5:d=0.6[w];"
        f"[v][t][c][w]amix=inputs=4:duration=first:normalize=0,{PHONE_MIC}"
    )


def cafe_graph(seed: int) -> str:
    # Approved version: the voice goes through the mic, chatter and hall noise are mixed after.
    return (
        f"[0]aresample=48000,aecho=0.8:0.6:18|35:0.25|0.15,{PHONE_MIC}[v];"
        "[1]aresample=48000,volume=0.16[b];"
        f"anoisesrc=color=pink:amplitude=0.005:r=48000:seed={seed},lowpass=4000[n];"
        "[v][b][n]amix=inputs=3:duration=first:normalize=0"
    )


def resolve(ambience: str, rng: random.Random) -> VoiceAmbience:
    """`random` → one of the presets; unknown values → off."""
    try:
        value = VoiceAmbience(ambience)
    except ValueError:
        return VoiceAmbience.OFF
    return rng.choice(PRESETS) if value is VoiceAmbience.RANDOM else value


def ffmpeg_args(ambience: VoiceAmbience, rng: random.Random) -> tuple[str, ...] | None:
    """ffmpeg argv that reads the TTS audio from stdin and writes a lo-fi OGG/Opus note;
    None for `off` (the caller uses the clean encoding)."""
    seed = rng.randrange(1, 1_000_000)
    head: tuple[str, ...] = ("ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0")
    if ambience is VoiceAmbience.ROOM:
        return (*head, "-filter_complex", room_graph(seed), *LOFI_OUT)
    if ambience is VoiceAmbience.STREET:
        return (*head, "-filter_complex", street_graph(seed), *LOFI_OUT)
    if ambience is VoiceAmbience.CAFE:
        offset = f"{rng.uniform(0, CHATTER_S):.2f}"
        chatter = ("-stream_loop", "-1", "-ss", offset, "-i", str(CHATTER))
        return (*head, *chatter, "-filter_complex", cafe_graph(seed), *LOFI_OUT)
    return None
