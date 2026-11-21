"""The phone-recording sound of voice notes (ffmpeg filter graphs), ported from v1 and extended
to every scene ambience (sim/domain/life.Ambience, docs/persona-life.md, «Звук»).

TTS is studio-clean. A real voice note is recorded by a phone microphone somewhere: the voice and
the place around it go through the same mic (band-limited, presence bump, automatic gain that
lifts the background between phrases, mild saturation) and a low-bitrate Opus encoder.
Pronunciation is untouched, only the "recording" changes. Levels are subtle on purpose.

The background is chosen by the scene, never at random; the noise seed comes from the scene's
event, so consecutive notes of one event sound like the same place. `none` = only the mic."""
from __future__ import annotations

from pathlib import Path

from sim.domain.life import Ambience

CHATTER = Path(__file__).resolve().parent.parent / "assets" / "ambience" / "cafe_chatter.ogg"
CHATTER_S = 9.0          # length of the bundled chatter clip (three far voices, from v1)

# The phone microphone: everything (voice + background) passes through it, in this order.
PHONE_MIC = (
    "highpass=f=160,lowpass=f=7200,"
    "equalizer=f=3000:t=q:w=1.4:g=4,equalizer=f=250:t=q:w=1:g=-3,"
    "acompressor=threshold=-22dB:ratio=4:attack=4:release=120:makeup=3,"
    "asoftclip=type=tanh:param=1.2"
)
# Messenger voice notes are low-bitrate Opus.
LOFI_OUT: tuple[str, ...] = ("-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "24k",
                             "-application", "voip", "-f", "ogg")
VOICE = "[0]aresample=48000"


def _mix(voice: str, beds: list[str]) -> str:
    """Voice + background beds -> one stream through the phone mic."""
    labels = "".join(f"[b{i}]" for i in range(len(beds)))
    parts = [f"{voice}[v]", *(f"{bed}[b{i}]" for i, bed in enumerate(beds))]
    return ";".join(parts) + (f";[v]{labels}amix=inputs={len(beds) + 1}:duration=first:"
                              f"normalize=0,{PHONE_MIC}")


def _room(seed: int, level: float = 0.025) -> list[str]:
    """Ventilation hiss and a fridge hum (v1 `room`, quieter)."""
    return [
        f"anoisesrc=color=pink:amplitude={level}:r=48000:seed={seed},bandpass=f=900:w=1800",
        "sine=f=100:r=48000,volume=0.008",
        "sine=f=200:r=48000,volume=0.005",
    ]


def mic_only(seed: int) -> str:
    return f"{VOICE},{PHONE_MIC}"


def quiet_home(seed: int) -> str:
    return _mix(f"{VOICE},aecho=0.8:0.55:12|23|37:0.22|0.14|0.08", _room(seed))


def kitchen(seed: int) -> str:
    beds = [*_room(seed, 0.02), "sine=f=50:r=48000,volume=0.012",
            f"anoisesrc=color=white:amplitude=0.006:r=48000:seed={seed + 1},highpass=f=3500,"
            "tremolo=f=0.4:d=0.9"]
    return _mix(f"{VOICE},aecho=0.8:0.5:9|19:0.25|0.12", beds)


def street(seed: int) -> str:
    return _mix(VOICE, [
        f"anoisesrc=color=brown:amplitude=0.4:r=48000:seed={seed},highpass=180,lowpass=1600,"
        "tremolo=f=0.12:d=0.75",
        f"anoisesrc=color=pink:amplitude=0.03:r=48000:seed={seed + 1},bandpass=f=600:w=900,"
        "tremolo=f=0.1:d=0.6",
        f"anoisesrc=color=white:amplitude=0.01:r=48000:seed={seed + 2},highpass=3000,"
        "tremolo=f=0.5:d=0.6",
    ])


def park(seed: int) -> str:
    return _mix(VOICE, [
        f"anoisesrc=color=pink:amplitude=0.03:r=48000:seed={seed},lowpass=f=800,"
        "tremolo=f=0.15:d=0.8",
        "sine=f=3400:r=48000,vibrato=f=7:d=0.6,tremolo=f=9:d=1,tremolo=f=0.35:d=1,"
        "volume=0.006",
        "sine=f=4100:r=48000,vibrato=f=5:d=0.5,tremolo=f=12:d=1,tremolo=f=0.23:d=1,"
        "volume=0.004",
    ])


def cafe(seed: int) -> str:
    # v1's approved version: the voice goes through the mic, chatter and hall noise after it.
    return (
        f"{VOICE},aecho=0.8:0.6:18|35:0.25|0.15,{PHONE_MIC}[v];"
        "[1]aresample=48000,volume=0.14[b];"
        f"anoisesrc=color=pink:amplitude=0.005:r=48000:seed={seed},lowpass=4000[n];"
        "[v][b][n]amix=inputs=3:duration=first:normalize=0"
    )


def car(seed: int) -> str:
    return _mix(f"{VOICE},aecho=0.8:0.4:6|11:0.2|0.1", [
        f"anoisesrc=color=brown:amplitude=0.3:r=48000:seed={seed},lowpass=f=220",
        "sine=f=45:r=48000,volume=0.02",
        f"anoisesrc=color=pink:amplitude=0.015:r=48000:seed={seed + 1},bandpass=f=400:w=500",
    ])


def transport(seed: int) -> str:
    return _mix(VOICE, [
        f"anoisesrc=color=brown:amplitude=0.3:r=48000:seed={seed},lowpass=f=300,"
        "tremolo=f=1.5:d=0.4",
        f"anoisesrc=color=pink:amplitude=0.02:r=48000:seed={seed + 1},bandpass=f=700:w=900,"
        "tremolo=f=0.2:d=0.5",
    ])


def office(seed: int) -> str:
    return _mix(f"{VOICE},aecho=0.8:0.5:14|28:0.2|0.1", [
        *_room(seed, 0.02),
        f"anoisesrc=color=white:amplitude=0.004:r=48000:seed={seed + 1},highpass=f=2000,"
        "tremolo=f=7:d=0.9,tremolo=f=0.2:d=1",
    ])


GRAPHS = {
    Ambience.NONE: mic_only, Ambience.QUIET_HOME: quiet_home, Ambience.KITCHEN: kitchen,
    Ambience.STREET: street, Ambience.PARK: park, Ambience.CAFE: cafe, Ambience.CAR: car,
    Ambience.TRANSPORT: transport, Ambience.OFFICE: office,
}


def resolve(ambience: str) -> Ambience:
    """An unknown value records with the mic only."""
    try:
        return Ambience(ambience)
    except ValueError:
        return Ambience.NONE


def voice_note_args(ambience: str, seed: int) -> tuple[str, ...]:
    """ffmpeg arguments after the voice input (`-i <wav>`): extra inputs, the filter graph and
    the lo-fi Opus output options; the caller appends the output path."""
    value = resolve(ambience)
    extra: tuple[str, ...] = ()
    if value == Ambience.CAFE:
        # The same seed starts the clip at the same place: one event, one cafe.
        offset = f"{(seed % int(CHATTER_S * 100)) / 100:.2f}"
        extra = ("-stream_loop", "-1", "-ss", offset, "-i", str(CHATTER))
    return (*extra, "-filter_complex", GRAPHS[value](seed), *LOFI_OUT)
