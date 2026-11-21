"""GET /v1/models → ModelInfo, cached for 30 s."""
from __future__ import annotations

import httpx
import pytest
import respx

from sim.adapters.broker.chat import BrokerLlm
from sim.adapters.broker.http import BrokerHttp
from sim.adapters.broker.models import MODELS_TTL_S, BrokerModelCatalog, model_info
from sim.ports import PortError
from tests.adapters.helpers import FakeTime

CATALOG = {"object": "list", "data": [
    {"id": "gemini/gemini-3.5-flash", "capabilities": ["chat:smart", "structured", "vision"],
     "modalities": ["text", "image"], "output_modalities": ["text"],
     "price": {"kind": "litellm", "input_usd_per_mtok": 0.3}},
    {"id": "codex-sub/gpt-6.2", "available": False, "capabilities": ["chat:smart"],
     "modalities": None, "output_modalities": ["text", "image"], "price": "?"},
    {"name": "no id"},
    "garbage",
]}


async def test_list_models(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.get("/v1/models").mock(return_value=httpx.Response(200, json=CATALOG))
    models = await BrokerLlm(http).list_models()
    assert [model.id for model in models] == ["gemini/gemini-3.5-flash", "codex-sub/gpt-6.2"]
    flash, codex = models
    assert flash.available is True
    assert flash.input_modalities == ("text", "image")
    assert flash.output_modalities == ("text",)
    assert flash.capabilities == ("chat:smart", "structured", "vision")
    assert flash.price == {"kind": "litellm", "input_usd_per_mtok": 0.3}
    assert codex.available is False
    assert codex.input_modalities == ()
    assert codex.output_modalities == ("text", "image")
    assert codex.price == {}


async def test_catalog_is_cached(http: BrokerHttp, broker: respx.MockRouter,
                                 fake_time: FakeTime) -> None:
    route = broker.get("/v1/models").mock(return_value=httpx.Response(200, json=CATALOG))
    catalog = BrokerModelCatalog(http)
    first = await catalog.list_models()
    first.clear()                          # callers get a copy
    assert len(await catalog.list_models()) == 2
    assert route.call_count == 1
    fake_time.now += MODELS_TTL_S
    await catalog.list_models()
    assert route.call_count == 2
    catalog.invalidate()
    await catalog.list_models()
    assert route.call_count == 3


async def test_errors_are_not_cached(http: BrokerHttp, broker: respx.MockRouter) -> None:
    route = broker.get("/v1/models").mock(side_effect=[httpx.Response(503, text="down"),
                                                       httpx.Response(200, json=CATALOG)])
    catalog = BrokerModelCatalog(http)
    with pytest.raises(PortError) as info:
        await catalog.list_models()
    assert info.value.retryable is True
    assert len(await catalog.list_models()) == 2
    assert route.call_count == 2


async def test_missing_data_is_empty(http: BrokerHttp, broker: respx.MockRouter) -> None:
    broker.get("/v1/models").mock(return_value=httpx.Response(200, json={"object": "list"}))
    assert await BrokerModelCatalog(http).list_models() == []


def test_model_info_accepts_input_modalities_name() -> None:
    info = model_info({"id": "x/live", "input_modalities": ["audio"], "available": None})
    assert info is not None
    assert info.input_modalities == ("audio",)
    assert info.available is True
    assert model_info({"id": ""}) is None
