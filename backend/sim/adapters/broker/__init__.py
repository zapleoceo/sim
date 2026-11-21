"""AI Broker client (docs/broker.md): one shared `BrokerHttp` and one small adapter per port."""
from __future__ import annotations

from dataclasses import dataclass

from sim.adapters.broker.chat import BrokerLlm
from sim.adapters.broker.http import BrokerHttp, BrokerHttpError
from sim.adapters.broker.image import BrokerImageGen
from sim.adapters.broker.live import BrokerLive, BrokerLiveSession
from sim.adapters.broker.models import BrokerModelCatalog
from sim.adapters.broker.speech import BrokerSpeech
from sim.adapters.broker.transcribe import BrokerTranscriber
from sim.adapters.broker.video import BrokerVideoGen
from sim.config import Settings

__all__ = [
    "BrokerAdapters",
    "BrokerHttp",
    "BrokerHttpError",
    "BrokerImageGen",
    "BrokerLive",
    "BrokerLiveSession",
    "BrokerLlm",
    "BrokerModelCatalog",
    "BrokerSpeech",
    "BrokerTranscriber",
    "BrokerVideoGen",
]


@dataclass(frozen=True)
class BrokerAdapters:
    """Every Broker port over one connection pool; `aclose()` at process shutdown."""

    http: BrokerHttp
    llm: BrokerLlm
    transcriber: BrokerTranscriber
    speech: BrokerSpeech
    image: BrokerImageGen
    video: BrokerVideoGen
    live: BrokerLive

    @classmethod
    def build(cls, http: BrokerHttp) -> BrokerAdapters:
        return cls(http=http, llm=BrokerLlm(http), transcriber=BrokerTranscriber(http),
                   speech=BrokerSpeech(http), image=BrokerImageGen(http),
                   video=BrokerVideoGen(http), live=BrokerLive.from_http(http))

    @classmethod
    def from_settings(cls, settings: Settings) -> BrokerAdapters:
        return cls.build(BrokerHttp.from_settings(settings))

    async def aclose(self) -> None:
        await self.http.aclose()
