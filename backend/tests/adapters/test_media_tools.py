"""media_tools without ffmpeg: pure helpers, document text, and the tool calls over a fake
runner (the real ffmpeg runs in test_media_tools_ffmpeg.py)."""
from __future__ import annotations

import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import docx
import pytest

from sim.adapters.ambience import voice_note_args
from sim.adapters.media_tools import (
    CALL_RECORDING_ARGS,
    DOCUMENT_CHAR_LIMIT,
    OGG_OPUS_ARGS,
    PCM16K_ARGS,
    TRUNCATED_MARK,
    VIDEO_NOTE_ARGS,
    FfmpegMediaTools,
    ProcessResult,
    decode_text,
    document_kind,
    extract_document_text,
    probe_from,
    run_process,
    sniff_mime,
    suffix_for,
    truncate,
    wav_duration,
)
from sim.domain.life import Ambience
from sim.ports import PortError
from tests.adapters.helpers import pdf_bytes, png_bytes, wav_bytes


def _write_output(path: str, data: bytes) -> None:
    Path(path).write_bytes(data)


class FakeRunner:
    """Records the command; ffmpeg 'writes' `output` to its last argument, ffprobe prints
    `probe` as JSON."""

    def __init__(self, *, code: int = 0, output: bytes | None = b"converted",
                 probe: dict | None = None, stderr: bytes = b"") -> None:
        self.code = code
        self.output = output
        self.probe = probe or {}
        self.stderr = stderr
        self.calls: list[list[str]] = []
        self.limits: list[float] = []

    async def __call__(self, args: Sequence[str], limit_s: float) -> ProcessResult:
        self.calls.append(list(args))
        self.limits.append(limit_s)
        if args[0] == "ffprobe":
            return ProcessResult(self.code, json.dumps(self.probe).encode(), self.stderr)
        if self.code == 0 and self.output is not None:
            _write_output(args[-1], self.output)
        return ProcessResult(self.code, b"", self.stderr)


def tools(runner: FakeRunner) -> FfmpegMediaTools:
    return FfmpegMediaTools(runner=runner, limit_s=7.0)


# --- pure helpers ----------------------------------------------------------------------------


def test_sniff_mime() -> None:
    assert sniff_mime(png_bytes(2, 2), "application/octet-stream") == "image/png"
    assert sniff_mime(b"plain words", "text/plain") == "text/plain"
    assert sniff_mime(b"", "") == "application/octet-stream"
    assert sniff_mime(pdf_bytes("x"), "") == "application/pdf"


def test_suffix_for() -> None:
    assert suffix_for("image/HEIC") == ".heic"
    assert suffix_for("audio/x-wav") == ".wav"
    assert suffix_for("application/x-unknown") == ".bin"


def test_probe_from_video_with_rotation() -> None:
    info = {"streams": [{"codec_type": "audio", "duration": "9.0"},
                        {"codec_type": "video", "width": 1920, "height": 1080,
                         "side_data_list": [{"rotation": -90}]}],
            "format": {"duration": "12.48"}}
    probe = probe_from(info, "video/mp4")
    assert (probe.width, probe.height, probe.duration_s) == (1080, 1920, 12.48)


def test_probe_from_rotate_tag_and_stream_duration() -> None:
    info = {"streams": [{"codec_type": "video", "width": 640, "height": 480,
                         "tags": {"rotate": "270"}, "duration": "3.5"}], "format": {}}
    probe = probe_from(info, "video/quicktime")
    assert (probe.width, probe.height, probe.duration_s) == (480, 640, 3.5)


def test_probe_from_image_has_no_duration() -> None:
    info = {"streams": [{"codec_type": "video", "width": 800, "height": 600,
                         "tags": {"rotate": "180"}}], "format": {"duration": "0.04"}}
    probe = probe_from(info, "image/jpeg")
    assert (probe.width, probe.height, probe.duration_s) == (800, 600, None)


def test_probe_from_audio() -> None:
    probe = probe_from({"streams": [{"codec_type": "audio"}], "format": {"duration": "N/A"}},
                       "audio/ogg")
    assert (probe.width, probe.height, probe.duration_s) == (None, None, None)


def test_wav_duration() -> None:
    assert wav_duration(wav_bytes(0.5)) == pytest.approx(0.5)
    assert wav_duration(b"not a wav") is None


@pytest.mark.parametrize(("data", "mime", "name", "kind"), [
    (b"%PDF-1.4", "application/octet-stream", "", "pdf"),
    (b"x", "application/pdf", "", "pdf"),
    (b"x", "", "Report.PDF", "pdf"),
    (b"PK", "", "contract.docx", "docx"),
    (b"PK", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "",
     "docx"),
    (b"a,b", "text/csv; charset=utf-8", "", "text"),
    (b"{}", "application/json", "", "text"),
    (b"# hi", "application/octet-stream", "notes.md", "text"),
    (b"PK", "application/zip", "archive.zip", None),
    (b"\xff\xd8", "image/jpeg", "photo.jpg", None),
])
def test_document_kind(data: bytes, mime: str, name: str, kind: str | None) -> None:
    assert document_kind(data, mime, name) == kind


def test_decode_text_encodings() -> None:
    assert decode_text("﻿привет".encode()) == "привет"
    assert decode_text("привет".encode("cp1251")) == "привет"
    assert decode_text(b"\x98") == "\x98"           # undefined in cp1251: Latin-1


def test_truncate() -> None:
    assert truncate("  short  ") == "short"
    long = truncate("x" * (DOCUMENT_CHAR_LIMIT + 10))
    assert len(long) == DOCUMENT_CHAR_LIMIT
    assert long.endswith(TRUNCATED_MARK)


# --- documents -------------------------------------------------------------------------------


def test_text_document() -> None:
    assert extract_document_text(b"a;b\n1;2\n","text/csv", "t.csv") == "a;b\n1;2"


def test_unsupported_document_is_none() -> None:
    assert extract_document_text(b"PK\x03\x04", "application/zip", "a.zip") is None


def test_docx_document() -> None:
    document = docx.Document()
    document.add_paragraph("Договор аренды")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Цена"
    table.cell(0, 1).text = "100"
    buffer = io.BytesIO()
    document.save(buffer)
    text = extract_document_text(buffer.getvalue(), "", "contract.docx")
    assert text == "Договор аренды\nЦена | 100"


def test_pdf_document() -> None:
    text = extract_document_text(pdf_bytes("Hello PDF"), "application/pdf", "a.pdf")
    assert text is not None
    assert "Hello PDF" in text


@pytest.mark.parametrize(("data", "name"), [(b"%PDF-1.4 broken", "a.pdf"),
                                            (b"not a zip", "a.docx")])
def test_broken_document_raises(data: bytes, name: str) -> None:
    with pytest.raises(PortError, match="cannot read") as info:
        extract_document_text(data, "", name)
    assert info.value.retryable is False


async def test_document_text_runs_in_a_thread() -> None:
    result = await tools(FakeRunner()).document_text(b"hello", "text/plain", "a.txt")
    assert result == "hello"


# --- tool calls over a fake runner -----------------------------------------------------------


async def test_wav_to_ogg_opus_command() -> None:
    runner = FakeRunner(output=b"OggS...")
    ogg, duration = await tools(runner).wav_to_ogg_opus(wav_bytes(0.25))
    assert ogg == b"OggS..."
    assert duration == pytest.approx(0.25)
    command = runner.calls[0]
    assert command[0] == "ffmpeg"
    assert command[-1].endswith("out.ogg")
    assert command[command.index("-i") + 1].endswith("input.wav")
    assert tuple(command[-1 - len(OGG_OPUS_ARGS):-1]) == OGG_OPUS_ARGS
    assert runner.limits == [7.0]


async def test_voice_note_records_the_scene_background() -> None:
    runner = FakeRunner(output=b"OggS...")
    ogg, duration = await tools(runner).voice_note(wav_bytes(0.25), "cafe", 42)
    assert ogg == b"OggS..." and duration == pytest.approx(0.25)
    command = runner.calls[0]
    assert command[command.index("-i") + 1].endswith("input.wav")
    assert "-filter_complex" in command and "libopus" in command
    assert command[-1].endswith("out.ogg")
    assert any(part.endswith("cafe_chatter.ogg") for part in command)
    with pytest.raises(PortError):
        await tools(runner).voice_note(b"", "none", 1)


async def test_wav_to_ogg_opus_probes_when_header_is_odd() -> None:
    runner = FakeRunner(output=b"OggS...", probe={"format": {"duration": "1.234"}})
    _, duration = await tools(runner).wav_to_ogg_opus(b"RIFF????")
    assert duration == pytest.approx(1.23)
    assert [call[0] for call in runner.calls] == ["ffmpeg", "ffprobe"]


async def test_a_faster_voice_note_is_stretched_and_shorter() -> None:
    """persona.voice_speed (docs/persona-life.md): `atempo` keeps the pitch."""
    runner = FakeRunner(output=b"OggS...")
    ogg, duration = await tools(runner).wav_to_ogg_opus(wav_bytes(0.26), 1.3)
    assert ogg == b"OggS..." and duration == pytest.approx(0.2)
    command = runner.calls[0]
    assert command[command.index("-i") + 2:command.index("-i") + 4] == ["-af", "atempo=1.30"]
    assert tuple(command[-1 - len(OGG_OPUS_ARGS):-1]) == OGG_OPUS_ARGS
    # The model's own pace adds no filter.
    plain = FakeRunner(output=b"OggS...")
    await tools(plain).wav_to_ogg_opus(wav_bytes(0.25), 1.0)
    assert "-af" not in plain.calls[0]


async def test_a_voice_note_in_a_place_stretches_only_the_voice() -> None:
    runner = FakeRunner(output=b"OggS...")
    _, duration = await tools(runner).voice_note(wav_bytes(0.25), "kitchen", 7, 0.8)
    assert duration == pytest.approx(0.25 / 0.8, abs=0.01)
    graph = runner.calls[0][runner.calls[0].index("-filter_complex") + 1]
    assert graph.startswith("[0]atempo=0.80,aresample=48000")


def test_voice_note_args_put_the_speed_before_the_background() -> None:
    for ambience in Ambience:
        args = voice_note_args(ambience.value, 9, 1.3)
        graph = args[args.index("-filter_complex") + 1]
        assert graph.startswith("[0]atempo=1.30,aresample=48000"), ambience
        assert graph.count("atempo") == 1               # the background keeps its pace
        normal = voice_note_args(ambience.value, 9)
        assert normal[normal.index("-filter_complex") + 1].startswith("[0]aresample=48000")
    assert voice_note_args("none", 9, 1.004) == voice_note_args("none", 9)


async def test_call_recording_reads_raw_stereo() -> None:
    runner = FakeRunner(output=b"OggS...")
    stereo = b"\x00\x00" * 2 * 16_000                   # one second, two channels
    ogg, seconds = await tools(runner).call_recording(stereo, 16_000)
    assert ogg == b"OggS..." and seconds == 1.0
    command = runner.calls[0]
    raw = command.index("-i")
    assert command[raw - 6:raw] == ["-f", "s16le", "-ar", "16000", "-ac", "2"]
    assert tuple(command[-1 - len(CALL_RECORDING_ARGS):-1]) == CALL_RECORDING_ARGS
    assert command[-1].endswith("out.ogg")
    with pytest.raises(PortError):
        await tools(runner).call_recording(b"", 16_000)


async def test_to_pcm16k_command() -> None:
    runner = FakeRunner(output=b"\x00\x00" * 10)
    pcm = await tools(runner).to_pcm16k(b"OggS voice", "audio/ogg")
    assert pcm == b"\x00\x00" * 10
    assert tuple(runner.calls[0][-1 - len(PCM16K_ARGS):-1]) == PCM16K_ARGS


async def test_ffmpeg_failure_carries_stderr() -> None:
    runner = FakeRunner(code=1, stderr=b"Invalid data found when processing input")
    with pytest.raises(PortError, match="ffmpeg exited 1: Invalid data") as info:
        await tools(runner).to_pcm16k(b"junk", "audio/ogg")
    assert info.value.retryable is False


async def test_empty_ffmpeg_output_is_an_error() -> None:
    with pytest.raises(PortError, match="empty output"):
        await tools(FakeRunner(output=b"")).to_pcm16k(b"junk", "audio/ogg")
    with pytest.raises(PortError, match="no output"):
        await tools(FakeRunner(output=None)).to_pcm16k(b"junk", "audio/ogg")


@pytest.mark.parametrize("call", ["wav", "pcm", "image"])
async def test_empty_input_is_refused(call: str) -> None:
    media = tools(FakeRunner())
    with pytest.raises(PortError, match="empty"):
        if call == "wav":
            await media.wav_to_ogg_opus(b"")
        elif call == "pcm":
            await media.to_pcm16k(b"", "audio/ogg")
        else:
            await media.image_for_model(b"", "image/png")


async def test_probe_uses_ffprobe() -> None:
    runner = FakeRunner(probe={"streams": [{"codec_type": "video", "width": 4,
                                            "height": 2}]})
    probe = await tools(runner).probe(png_bytes(4, 2), "application/octet-stream")
    assert (probe.mime, probe.width, probe.height) == ("image/png", 4, 2)
    assert runner.calls[0][0] == "ffprobe"
    assert runner.calls[0][-1].endswith("input.png")


async def test_probe_is_best_effort() -> None:
    probe = await tools(FakeRunner(code=1)).probe(png_bytes(4, 2), "image/png")
    assert (probe.mime, probe.width, probe.duration_s) == ("image/png", None, None)


async def test_probe_skips_documents() -> None:
    runner = FakeRunner()
    probe = await tools(runner).probe(pdf_bytes("x"), "")
    assert probe.mime == "application/pdf"
    assert runner.calls == []


async def test_small_jpeg_or_png_is_kept() -> None:
    runner = FakeRunner(probe={"streams": [{"codec_type": "video", "width": 1024,
                                            "height": 2048}]})
    image = png_bytes(4, 2)
    assert await tools(runner).image_for_model(image, "image/png") == (image, "image/png")
    assert [call[0] for call in runner.calls] == ["ffprobe"]


async def test_large_image_is_converted() -> None:
    runner = FakeRunner(output=b"\xff\xd8jpeg",
                        probe={"streams": [{"codec_type": "video", "width": 4000,
                                            "height": 3000}]})
    result = await tools(runner).image_for_model(png_bytes(4, 2), "image/png")
    assert result == (b"\xff\xd8jpeg", "image/jpeg")
    command = runner.calls[-1]
    assert command[0] == "ffmpeg"
    assert command[-1].endswith("out.jpg")
    assert any("min(2048,iw)" in arg for arg in command)


async def test_heic_is_converted_without_probe() -> None:
    runner = FakeRunner(output=b"\xff\xd8jpeg")
    result = await tools(runner).image_for_model(b"heic bytes", "image/heic")
    assert result == (b"\xff\xd8jpeg", "image/jpeg")
    assert [call[0] for call in runner.calls] == ["ffmpeg"]
    assert runner.calls[0][runner.calls[0].index("-i") + 1].endswith("input.heic")


# --- the real subprocess runner --------------------------------------------------------------


async def test_run_process_success() -> None:
    result = await run_process([sys.executable, "-c", "print('ok')"], 30.0)
    assert result.code == 0
    assert result.stdout.strip() == b"ok"


async def test_run_process_missing_tool() -> None:
    with pytest.raises(PortError, match="unavailable") as info:
        await run_process(["sim-no-such-tool-xyz"], 5.0)
    assert info.value.retryable is False


async def test_run_process_timeout_is_retryable() -> None:
    with pytest.raises(PortError, match="timed out") as info:
        await run_process([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    assert info.value.retryable is True


async def test_to_video_note_command_and_duration() -> None:
    runner = FakeRunner(output=b"\x00\x00\x00\x18ftypisom", probe={"format": {"duration": "7.96"}})
    note, duration = await tools(runner).to_video_note(b"\x00\x00\x00\x18ftypisomCLIP")
    assert note == b"\x00\x00\x00\x18ftypisom"
    assert duration == pytest.approx(7.96)
    command = runner.calls[0]
    assert command[-1].endswith("out.mp4")
    assert tuple(command[-1 - len(VIDEO_NOTE_ARGS):-1]) == VIDEO_NOTE_ARGS
    assert "crop='min(iw,ih)':'min(iw,ih)',scale=640:640" in command[command.index("-vf") + 1]
    assert [call[0] for call in runner.calls] == ["ffmpeg", "ffprobe"]


async def test_to_video_note_refuses_an_empty_clip() -> None:
    with pytest.raises(PortError, match="empty video"):
        await tools(FakeRunner()).to_video_note(b"")
