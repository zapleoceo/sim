"""MediaToolsPort: ffmpeg / ffprobe for audio, video and images, pypdf / python-docx for files.

ffmpeg runs as a subprocess (`asyncio.create_subprocess_exec`, no shell) on files in a private
temporary directory, with a time limit and a cap on what is read back. Blocking file and
document work runs in a worker thread so the event loop never stalls."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import tempfile
import wave
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docx
import filetype
from pypdf import PdfReader

from sim.adapters.ambience import SPEED_EPSILON, voice_note_args
from sim.ports import PortError, Probe

log = logging.getLogger(__name__)

PROCESS_LIMIT_S = 120.0
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
STDERR_LIMIT = 400
DOCUMENT_CHAR_LIMIT = 30_000
TRUNCATED_MARK = "\n[...]"
MODEL_IMAGE_MAX_SIDE = 2048
MODEL_IMAGE_MAX_BYTES = 4 * 1024 * 1024
MODEL_IMAGE_MIMES = frozenset({"image/jpeg", "image/png"})
OGG_MIME = "audio/ogg"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log"})
TEXT_MIMES = frozenset({"application/json", "application/csv", "application/x-ndjson"})
SUFFIX_BY_MIME: Mapping[str, str] = {
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg", "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a", "audio/aac": ".aac", "audio/webm": ".webm", "audio/flac": ".flac",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    "image/heic": ".heic", "image/heif": ".heif", "image/avif": ".avif",
}
# 32 kbit/s mono Opus at 48 kHz: what the Telegram apps themselves record for voice notes.
OGG_OPUS_ARGS = ("-vn", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "32k",
                 "-application", "voip", "-f", "ogg")
CALL_RECORDING_ARGS = ("-ac", "2", "-c:a", "libopus", "-b:a", "32k", "-application", "voip",
                       "-f", "ogg")
PCM16K_ARGS = ("-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-c:a", "pcm_s16le")
# First frame, shrunk to fit a 2048 px box (never enlarged), as a good-quality JPEG.
FIT_FILTER = (f"scale=w='min({MODEL_IMAGE_MAX_SIDE},iw)':h='min({MODEL_IMAGE_MAX_SIDE},ih)'"
              ":force_original_aspect_ratio=decrease")
JPEG_ARGS = ("-frames:v", "1", "-vf", FIT_FILTER, "-q:v", "3", "-c:v", "mjpeg",
             "-f", "image2", "-update", "1")
# Telegram round video message («кружок»): the centre square of the first video stream at
# 640x640, H.264 yuv420p + mono AAC (if the clip has sound), at most 60 s, `moov` first so the
# circle starts playing before it is fully downloaded.
VIDEO_NOTE_SIDE = 640
VIDEO_NOTE_MAX_S = 60
VIDEO_NOTE_MIME = "video/mp4"
VIDEO_NOTE_ARGS = (
    "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1",
    "-vf", (f"crop='min(iw,ih)':'min(iw,ih)',scale={VIDEO_NOTE_SIDE}:{VIDEO_NOTE_SIDE},"
            "setsar=1"),
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "64k", "-ac", "1",
    "-t", str(VIDEO_NOTE_MAX_S), "-movflags", "+faststart", "-f", "mp4",
)


@dataclass(frozen=True)
class ProcessResult:
    code: int
    stdout: bytes
    stderr: bytes


type Runner = Callable[[Sequence[str], float], Awaitable[ProcessResult]]


async def run_process(args: Sequence[str], limit_s: float) -> ProcessResult:
    """Run a tool without a shell; kill it after `limit_s`. stderr keeps only its tail."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise PortError(f"{args[0]} unavailable: {type(exc).__name__}") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), limit_s)
    except TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise PortError(f"{args[0]} timed out after {limit_s:g}s", retryable=True) from exc
    return ProcessResult(code=proc.returncode or 0, stdout=out[:MAX_OUTPUT_BYTES],
                         stderr=err[-STDERR_LIMIT:])


def sniff_mime(data: bytes, declared: str) -> str:
    """The type the bytes really are (magic numbers), else the declared one."""
    guessed = filetype.guess_mime(data) if data else None
    return str(guessed or declared or "application/octet-stream").lower()


def suffix_for(mime: str) -> str:
    return SUFFIX_BY_MIME.get(mime.lower(), ".bin")


def is_audio_visual(mime: str) -> bool:
    return mime.startswith(("audio/", "video/", "image/"))


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _rotated(stream: Mapping[str, Any]) -> bool:
    """A phone video stored landscape with a 90° rotation flag is shown portrait."""
    tags = stream.get("tags") if isinstance(stream.get("tags"), Mapping) else {}
    side = stream.get("side_data_list") if isinstance(stream.get("side_data_list"), list) else []
    angles = [tags.get("rotate"), *(e.get("rotation") for e in side if isinstance(e, Mapping))]
    for angle in angles:
        try:
            degrees = int(float(angle))
        except (TypeError, ValueError):
            continue
        if degrees % 180 == 90:
            return True
    return False


def probe_from(info: Mapping[str, Any], mime: str) -> Probe:
    """Dimensions and duration from ffprobe's JSON (`-show_format -show_streams`)."""
    streams = [s for s in info.get("streams") or [] if isinstance(s, Mapping)]
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    width = height = None
    if video is not None:
        width, height = _num(video.get("width")), _num(video.get("height"))
        if width is not None and height is not None and _rotated(video):
            width, height = height, width
    duration = None
    if not mime.startswith("image/"):
        fmt = info.get("format") if isinstance(info.get("format"), Mapping) else {}
        duration = _num(fmt.get("duration"))
        if duration is None:
            duration = next((d for s in streams if (d := _num(s.get("duration"))) is not None),
                            None)
    return Probe(mime=mime, width=int(width) if width is not None else None,
                 height=int(height) if height is not None else None, duration_s=duration)


def wav_duration(wav: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(wav), "rb") as reader:
            frames, rate = reader.getnframes(), reader.getframerate()
    except (wave.Error, EOFError, ValueError):
        return None
    return frames / rate if frames > 0 and rate > 0 else None


# --- documents -------------------------------------------------------------------------------


def document_kind(data: bytes, mime: str, file_name: str) -> str | None:
    """text | docx | pdf, or None for anything SIM does not read itself."""
    suffix = Path(file_name).suffix.lower() if file_name else ""
    kind = mime.lower().split(";", 1)[0].strip()
    if data.startswith(b"%PDF") or kind == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if kind == DOCX_MIME or suffix == ".docx":
        return "docx"
    if kind.startswith("text/") or kind in TEXT_MIMES or suffix in TEXT_SUFFIXES:
        return "text"
    return None


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), else Windows-1251 (Russian files), else Latin-1."""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def docx_text(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    lines = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        lines += [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
    return "\n".join(line for line in lines if line.strip())


def pdf_text(data: bytes, limit: int = DOCUMENT_CHAR_LIMIT) -> str:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")
    pages: list[str] = []
    size = 0
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(text)
            size += len(text)
        if size >= limit:
            break
    return "\n\n".join(pages)


def truncate(text: str, limit: int = DOCUMENT_CHAR_LIMIT) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - len(TRUNCATED_MARK)].rstrip() + TRUNCATED_MARK


def extract_document_text(data: bytes, mime: str, file_name: str) -> str | None:
    """Readable text of a document, at most DOCUMENT_CHAR_LIMIT characters; None when the type
    is not one SIM reads (images, audio, archives...)."""
    kind = document_kind(data, mime, file_name)
    if kind is None:
        return None
    readers: dict[str, Callable[[bytes], str]] = {
        "text": decode_text, "docx": docx_text, "pdf": pdf_text}
    try:
        text = readers[kind](data)
    except Exception as exc:    # a broken or encrypted file: the parsers raise many types
        raise PortError(f"document: cannot read {kind}: {type(exc).__name__}") from exc
    return truncate(text)


# --- files of one tool run -------------------------------------------------------------------


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _read_output(path: Path, tool: str) -> bytes:
    if not path.is_file():
        raise PortError(f"{tool}: no output")
    size = path.stat().st_size
    if size == 0:
        raise PortError(f"{tool}: empty output")
    if size > MAX_OUTPUT_BYTES:
        raise PortError(f"{tool}: output larger than {MAX_OUTPUT_BYTES} bytes")
    return path.read_bytes()


def _stderr(result: ProcessResult) -> str:
    return result.stderr.decode("utf-8", "replace").strip()[-STDERR_LIMIT:] or "no output"


class FfmpegMediaTools:
    """MediaToolsPort. `runner` is the subprocess seam for tests."""

    def __init__(self, *, runner: Runner = run_process, limit_s: float = PROCESS_LIMIT_S,
                 ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.runner = runner
        self.limit_s = limit_s
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    async def probe(self, data: bytes, mime: str) -> Probe:
        """Best effort: a file ffprobe cannot read still gets its sniffed type."""
        kind = sniff_mime(data, mime)
        if not data or not is_audio_visual(kind):
            return Probe(mime=kind)
        try:
            info = await self._ffprobe(data, kind)
        except PortError as exc:
            log.warning("probe of %s failed: %s", kind, exc)
            return Probe(mime=kind)
        return probe_from(info, kind)

    async def wav_to_ogg_opus(self, wav: bytes, speed: float = 1.0) -> tuple[bytes, float]:
        """TTS WAV -> a clean OGG/Opus voice note, `speed` times faster (pitch kept)."""
        if not wav:
            raise PortError("ffmpeg: empty audio")
        args = OGG_OPUS_ARGS if abs(speed - 1.0) < SPEED_EPSILON else (
            "-af", f"atempo={speed:.2f}", *OGG_OPUS_ARGS)
        return await self._voice_ogg(wav, args, speed)

    async def voice_note(self, wav: bytes, ambience: str, seed: int,
                         speed: float = 1.0) -> tuple[bytes, float]:
        """TTS WAV -> a voice note «recorded on a phone» in the scene's place
        (sim/adapters/ambience.py); the same seed gives the same background."""
        if not wav:
            raise PortError("ffmpeg: empty audio")
        return await self._voice_ogg(wav, voice_note_args(ambience, seed, speed), speed)

    async def _voice_ogg(self, wav: bytes, args: tuple[str, ...],
                         speed: float) -> tuple[bytes, float]:
        ogg = await self._convert(wav, "audio/wav", "out.ogg", args)
        duration = wav_duration(wav)
        if duration is None:
            duration = (await self.probe(ogg, OGG_MIME)).duration_s
        return ogg, round((duration or 0.0) / (speed or 1.0), 2)

    async def call_recording(self, stereo_pcm: bytes, rate_hz: int) -> tuple[bytes, float]:
        """Interleaved s16le stereo of a call (client left, persona right) -> OGG/Opus."""
        if not stereo_pcm:
            raise PortError("ffmpeg: empty recording")
        ogg = await self._convert(stereo_pcm, "audio/pcm", "out.ogg", CALL_RECORDING_ARGS,
                                  input_args=("-f", "s16le", "-ar", str(rate_hz), "-ac", "2"))
        return ogg, round(len(stereo_pcm) / (4 * rate_hz), 2)

    async def to_pcm16k(self, data: bytes, mime: str) -> bytes:
        """Any audio or video → raw 16 kHz mono signed 16-bit little-endian PCM."""
        if not data:
            raise PortError("ffmpeg: empty audio")
        return await self._convert(data, sniff_mime(data, mime), "out.pcm", PCM16K_ARGS)

    async def document_text(self, data: bytes, mime: str, file_name: str) -> str | None:
        return await asyncio.to_thread(extract_document_text, data, mime, file_name)

    async def image_for_model(self, data: bytes, mime: str) -> tuple[bytes, str]:
        """JPEG or PNG within 2048 px and 4 MB is sent as is; anything else (HEIC, WebP, GIF,
        large photos) becomes a JPEG that fits a 2048 px box."""
        if not data:
            raise PortError("ffmpeg: empty image")
        kind = sniff_mime(data, mime)
        if kind in MODEL_IMAGE_MIMES and len(data) <= MODEL_IMAGE_MAX_BYTES:
            probe = await self.probe(data, kind)
            if (probe.width is not None and probe.height is not None
                    and max(probe.width, probe.height) <= MODEL_IMAGE_MAX_SIDE):
                return data, kind
        return await self._convert(data, kind, "out.jpg", JPEG_ARGS), "image/jpeg"

    async def to_video_note(self, mp4: bytes) -> tuple[bytes, float]:
        """Any video → a Telegram round video message (VIDEO_NOTE_ARGS) and its length."""
        if not mp4:
            raise PortError("ffmpeg: empty video")
        note = await self._convert(mp4, sniff_mime(mp4, VIDEO_NOTE_MIME), "out.mp4",
                                   VIDEO_NOTE_ARGS)
        duration = (await self.probe(note, VIDEO_NOTE_MIME)).duration_s
        return note, round(min(duration or 0.0, float(VIDEO_NOTE_MAX_S)), 2)

    async def _ffprobe(self, data: bytes, mime: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="sim-media-") as tmp:
            source = Path(tmp) / f"input{suffix_for(mime)}"
            await asyncio.to_thread(_write, source, data)
            result = await self.runner(
                [self.ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                 "-show_streams", str(source)], self.limit_s)
        if result.code != 0:
            raise PortError(f"ffprobe exited {result.code}: {_stderr(result)}")
        try:
            info = json.loads(result.stdout or b"{}")
        except ValueError as exc:
            raise PortError("ffprobe: output is not JSON") from exc
        return info if isinstance(info, dict) else {}

    async def _convert(self, data: bytes, mime: str, out_name: str,
                       args: Sequence[str], input_args: Sequence[str] = ()) -> bytes:
        with tempfile.TemporaryDirectory(prefix="sim-media-") as tmp:
            source = Path(tmp) / f"input{suffix_for(mime)}"
            target = Path(tmp) / out_name
            await asyncio.to_thread(_write, source, data)
            result = await self.runner(
                [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 *input_args, "-i", str(source), *args, str(target)], self.limit_s)
            if result.code != 0:
                raise PortError(f"ffmpeg exited {result.code}: {_stderr(result)}")
            return await asyncio.to_thread(_read_output, target, "ffmpeg")
