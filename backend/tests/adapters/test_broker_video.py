"""BrokerVideoGen: /v1/video/jobs with a first frame, then the poll."""
from __future__ import annotations

import base64

import pytest
import respx

from sim.adapters.broker.http import BrokerHttp
from sim.adapters.broker.video import MAX_FIRST_FRAME_BYTES, BrokerVideoGen, video_job_body
from sim.ports import PortError
from tests.adapters.helpers import FakeTime, done, failed, pending, sent_json, submitted

MP4 = b"\x00\x00\x00\x18ftypmp42 fake clip"
FRAME = (b"\xff\xd8\xff frame", "image/jpeg")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_generate_video(http: BrokerHttp, broker: respx.MockRouter) -> None:
    post = broker.post("/v1/video/jobs").mock(return_value=submitted(21))
    broker.get("/v1/jobs/21").mock(return_value=done(
        21, text=None, video_base64=b64(MP4), mime_type="video/mp4", video_s=5.0,
        cost_usd=0.35, model="gemini/gemini-omni-1.2-flash"))
    result = await BrokerVideoGen(http).generate_video(
        prompt="she waves", first_frame=FRAME, model="gemini/gemini-omni-1.2-flash")
    assert sent_json(post) == {
        "prompt": "she waves", "duration_s": 5, "aspect_ratio": "9:16", "resolution": "720p",
        "workflow": "sim-video", "model": "gemini/gemini-omni-1.2-flash",
        "image_base64": b64(FRAME[0]), "image_mime": "image/jpeg"}
    assert (result.data, result.mime) == (MP4, "video/mp4")
    assert result.usage.cost_usd == pytest.approx(0.35)
    assert result.usage.job_id == "21"


def test_body_clamps_duration_and_skips_empty_options() -> None:
    body = video_job_body(prompt="p", first_frame=None, model="", duration_s=60,
                          aspect_ratio="9:16", resolution="720p", workflow="w")
    assert body["duration_s"] == 15
    assert "model" not in body
    assert "image_base64" not in body
    short = video_job_body(prompt="p", first_frame=None, model="", duration_s=1,
                           aspect_ratio="9:16", resolution="720p", workflow="w")
    assert short["duration_s"] == 3


@pytest.mark.parametrize("frame", [(b"x", "image/heic"),
                                   (b"x" * (MAX_FIRST_FRAME_BYTES + 1), "image/jpeg")])
async def test_bad_first_frame_is_refused_before_any_call(
        http: BrokerHttp, broker: respx.MockRouter, frame: tuple[bytes, str]) -> None:
    post = broker.post("/v1/video/jobs")
    with pytest.raises(PortError, match="first frame") as info:
        await BrokerVideoGen(http).generate_video(prompt="p", first_frame=frame, model="")
    assert info.value.retryable is False
    assert post.call_count == 0


async def test_empty_prompt(http: BrokerHttp) -> None:
    with pytest.raises(PortError, match="empty prompt"):
        await BrokerVideoGen(http).generate_video(prompt="", first_frame=None, model="")


async def test_rejected_generation(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/video/jobs").mock(return_value=submitted(21))
    broker.get("/v1/jobs/21").mock(return_value=failed(21, error="VideoRejected: blocked"))
    with pytest.raises(PortError, match="VideoRejected") as info:
        await BrokerVideoGen(http).generate_video(prompt="p", first_frame=None, model="")
    assert info.value.retryable is False


async def test_slow_generation_is_retryable(http: BrokerHttp, broker: respx.MockRouter,
                                            fake_time: FakeTime) -> None:
    broker.post("/v1/video/jobs").mock(return_value=submitted(21))
    broker.get("/v1/jobs/21").mock(return_value=pending(21, poll_after_s=20))
    with pytest.raises(PortError, match="still pending") as info:
        await BrokerVideoGen(http).generate_video(prompt="p", first_frame=None, model="")
    assert info.value.retryable is True
    assert fake_time.now - 1000.0 >= 600       # the media budget, not the 60 s chat one


async def test_done_without_clip(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.post("/v1/video/jobs").mock(return_value=submitted(21))
    broker.get("/v1/jobs/21").mock(return_value=done(21, text=None))
    with pytest.raises(PortError, match="no media") as info:
        await BrokerVideoGen(http).generate_video(prompt="p", first_frame=None, model="")
    assert info.value.retryable is False
