"""The Broker model catalog (`GET /v1/models`) as `ModelInfo`, cached for a short time: the
persona page asks for it once per model picker."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from sim.adapters.broker.http import BrokerHttp
from sim.ports import ModelInfo

MODELS_TTL_S = 30.0


def _names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def model_info(row: Any) -> ModelInfo | None:
    """One catalog row; rows without an id are skipped. `available` missing means unknown,
    which counts as available (only an explicit `false` hides a model)."""
    if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) or not row["id"]:
        return None
    price = row.get("price")
    return ModelInfo(
        id=row["id"],
        available=row.get("available") is not False,
        input_modalities=_names(row.get("modalities", row.get("input_modalities"))),
        output_modalities=_names(row.get("output_modalities")),
        capabilities=_names(row.get("capabilities")),
        price=dict(price) if isinstance(price, Mapping) else {},
    )


class BrokerModelCatalog:
    def __init__(self, http: BrokerHttp, *, ttl_s: float = MODELS_TTL_S) -> None:
        self.http = http
        self.ttl_s = ttl_s
        self._cached: tuple[float, list[ModelInfo]] | None = None
        self._lock = asyncio.Lock()

    async def list_models(self) -> list[ModelInfo]:
        async with self._lock:
            now = self.http.clock()
            if self._cached is not None and now - self._cached[0] < self.ttl_s:
                return list(self._cached[1])
            body = await self.http.json("GET", "/v1/models", what="models")
            rows = body.get("data")
            models = [info for row in (rows if isinstance(rows, list) else [])
                      if (info := model_info(row)) is not None]
            self._cached = (now, models)
            return list(models)

    def invalidate(self) -> None:
        self._cached = None
