"""Resource-contained MP4 to Telegram video-note normalization, with truthful AI label."""
from __future__ import annotations

import json
import math
import struct
import tempfile
from pathlib import Path

from sim.adapters.audio import AudioError, Runner
from sim.adapters.video_process import ENCODE_LIMIT, run_video_encoder, run_video_process
from sim.ports.video import VideoError
from sim.services.studio_files import valid_artifact

VIDEO_NOTE_SIDE = 384
VIDEO_NOTE_MAX_S = 60
VIDEO_NOTE_INPUT_LIMIT = 20 * 1024 * 1024
FFMPEG_VIDEO_TIMEOUT_S = 45.0
CHAT_AUDIO_RATE = 8_000
CHAT_AUDIO_FRAME_SAMPLES = CHAT_AUDIO_RATE // 50  # 20 ms
CHAT_AUDIO_MIN_RMS = 8  # signed 16-bit PCM, approximately -72 dBFS
CHAT_AUDIO_ACTIVE_FRAMES = 2  # need not be consecutive; pauses are allowed


def has_chat_audio_signal(pcm: bytes) -> bool:
    frame_bytes = 2 * CHAT_AUDIO_FRAME_SAMPLES
    active = 0
    for offset in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        frame = pcm[offset:offset + frame_bytes]
        energy = sum(sample * sample for (sample,) in struct.iter_unpack("<h", frame))
        if energy >= CHAT_AUDIO_FRAME_SAMPLES * CHAT_AUDIO_MIN_RMS**2:
            active += 1
            if active >= CHAT_AUDIO_ACTIVE_FRAMES:
                return True
    return False


def video_note_args(src: str, dst: str = "pipe:1") -> tuple[str, ...]:
    """Single video/audio stream, centre crop; fragmented MP4 needs no output file."""
    side = VIDEO_NOTE_SIDE
    return (
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-max_alloc", "16777216", "-protocol_whitelist", "file,pipe",
        "-threads", "1", "-i", src, "-map", "0:v:0", "-map", "0:a:0?",
        "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
        "-filter_threads", "1", "-vf",
        f"crop='min(iw,ih)':'min(iw,ih)',scale={side}:{side},setsar=1,fps=30,"
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
        "text='AI generated':fontsize=18:fontcolor=white:box=1:boxcolor=black@0.8:"
        "boxborderw=5:x=(w-tw)/2:y=h*0.72",
        "-c:v", "libx264", "-threads", "1", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "1",
        "-t", str(VIDEO_NOTE_MAX_S), "-movflags", "+frag_keyframe+empty_moov",
        "-brand", "mp42", "-f", "mp4", dst,
    )


class VideoNoteEncoder:
    def __init__(self, runner: Runner = run_video_encoder,
                 timeout_s: float = FFMPEG_VIDEO_TIMEOUT_S, *,
                 audio_runner: Runner = run_video_process) -> None:
        self.runner, self.timeout_s = runner, min(timeout_s, FFMPEG_VIDEO_TIMEOUT_S)
        self.audio_runner = audio_runner

    async def probe(self, path: Path, *, output: bool = False) -> bool:
        code, data, _ = await self.runner((
            "ffprobe", "-v", "error", "-max_alloc", "16777216",
            "-protocol_whitelist", "file", "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt:format=duration",
            "-of", "json", str(path)), b"", min(self.timeout_s, 8))
        try:
            if code or len(data) > 16384:
                raise ValueError
            info = json.loads(data)
            streams = info["streams"]
            videos = [s for s in streams if s.get("codec_type") == "video"]
            audio = [s for s in streams if s.get("codec_type") == "audio"]
            v = videos[0]
            width, height = int(v["width"]), int(v["height"])
            duration = float(info["format"]["duration"])
            if (not math.isfinite(duration) or not 0 < duration <= VIDEO_NOTE_MAX_S
                    or width <= 0 or height <= 0 or width * height > 1920 * 1080):
                raise ValueError
            if output and (len(videos) != 1 or len(audio) > 1
                           or width != VIDEO_NOTE_SIDE or height != VIDEO_NOTE_SIDE
                           or v.get("codec_name") != "h264" or v.get("pix_fmt") != "yuv420p"
                           or any(a.get("codec_name") != "aac" for a in audio)):
                raise ValueError
            return bool(audio)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            raise VideoError("Видео не соответствует ограничениям формата или длительности") from exc

    async def check_chat_audio(self, path: Path) -> None:
        # 60 s × 8 kHz mono × 2 bytes = 960 KB, below the bounded decoder pipe limit.
        code, pcm, _ = await self.audio_runner((
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-max_alloc", "16777216",
            "-protocol_whitelist", "file,pipe", "-threads", "1", "-i", str(path),
            "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(CHAT_AUDIO_RATE),
            "-t", str(VIDEO_NOTE_MAX_S), "-f", "s16le", "pipe:1",
        ), b"", min(self.timeout_s, 8))
        if code or not pcm or len(pcm) % 2 or len(pcm) > VIDEO_NOTE_MAX_S * CHAT_AUDIO_RATE * 2:
            raise VideoError("Не удалось проверить звук готового кружочка")
        if not has_chat_audio_signal(pcm):
            raise VideoError("В готовом кружочке не обнаружен звуковой сигнал")

    async def to_video_note(self, video: bytes, *, require_audio: bool = False) -> bytes:
        if not video:
            raise VideoError("empty video")
        if len(video) > VIDEO_NOTE_INPUT_LIMIT or not valid_artifact(video, "video/mp4", "video"):
            raise VideoError("Нужен корректный MP4 размером не более 20 МБ")
        with tempfile.TemporaryDirectory(prefix="sim-video-note-") as folder:
            src, dst = Path(folder) / "in.mp4", Path(folder) / "out.mp4"
            src.write_bytes(video)
            try:
                had_audio = await self.probe(src)
                if require_audio and not had_audio:
                    raise VideoError("В видео от провайдера нет звуковой дорожки")
                code, note, _ = await self.runner(video_note_args(str(src)), b"", self.timeout_s)
                if (code or len(note) > ENCODE_LIMIT
                        or not valid_artifact(note, "video/mp4", "video")):
                    raise VideoError("Не удалось безопасно подготовить кружочек")
                dst.write_bytes(note)
                if await self.probe(dst, output=True) != had_audio:
                    raise VideoError("Не удалось сохранить звук кружочка")
                if require_audio:
                    await self.check_chat_audio(dst)
            except AudioError as exc:
                raise VideoError("Обработка видео недоступна или превысила лимит ресурсов") from exc
        return note
