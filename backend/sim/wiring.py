"""Dependency assembly: one Container builds the services for a unit of work (a request or a job)."""
from __future__ import annotations

import logging
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sim.adapters.crypto import SecretBox
from sim.adapters.db.session import make_engine, make_session_factory
from sim.adapters.llm.broker import BrokerLLM
from sim.adapters.media import MediaStore, download
from sim.clock import Clock, utcnow
from sim.config import Settings
from sim.connectors.spec import Connector
from sim.domain.enums import ChannelKind
from sim.ports.llm import LLMPort
from sim.services.analytics import AnalyticsService
from sim.services.auth import AuthService, LoginRateLimiter
from sim.services.channels import ChannelService
from sim.services.content import ContentPublisher, ContentService
from sim.services.conversation import ConversationService
from sim.services.followup import FollowupService
from sim.services.ingest import Downloader, IngestService
from sim.services.llm_audit import AuditedLLM
from sim.services.outbound import OutboundService
from sim.services.partner import PartnerService

log = logging.getLogger(__name__)

Registry = Mapping[ChannelKind, Connector]


def default_registry() -> Registry:
    """The connector registry built by the connectors module; empty until it exists."""
    try:
        from sim.connectors.registry import REGISTRY
    except ImportError:
        log.warning("sim.connectors.registry not found: no channel connectors available")
        return {}
    return REGISTRY


def make_box(settings: Settings) -> SecretBox:
    if settings.secret_key:
        return SecretBox(settings.secret_key)
    if settings.env == "prod":
        raise RuntimeError("SIM_SECRET_KEY is required in production")
    log.warning("SIM_SECRET_KEY is empty: using an ephemeral key (dev only)")
    return SecretBox(Fernet.generate_key().decode())


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    llm: LLMPort
    registry: Registry
    box: SecretBox
    media: MediaStore
    limiter: LoginRateLimiter = field(default_factory=LoginRateLimiter)
    download: Downloader = download
    clock: Clock = utcnow
    rng: Callable[[float, float], float] = random.uniform
    publisher: ContentPublisher | None = None

    # ---- per-unit-of-work services -------------------------------------------------------
    def audited(self) -> AuditedLLM:
        return AuditedLLM(self.llm, self.factory, self.clock)

    def auth(self, s: AsyncSession) -> AuthService:
        return AuthService(
            s, ttl_h=self.settings.session_ttl_h, limiter=self.limiter, clock=self.clock
        )

    def outbound(self, s: AsyncSession) -> OutboundService:
        return OutboundService(s, self.registry, self.box, clock=self.clock, rng=self.rng)

    def conversation(self, s: AsyncSession) -> ConversationService:
        return ConversationService(s, self.audited(), self.outbound(s), clock=self.clock)

    def followup(self, s: AsyncSession) -> FollowupService:
        return FollowupService(s, clock=self.clock)

    def ingest(self, s: AsyncSession) -> IngestService:
        return IngestService(
            s,
            self.llm,
            self.media,
            debounce_s=self.settings.reply_debounce_s,
            download=self.download,
            clock=self.clock,
        )

    def channels(self, s: AsyncSession) -> ChannelService:
        return ChannelService(s, self.registry, self.box, clock=self.clock)

    def content(self, s: AsyncSession) -> ContentService:
        return ContentService(s, self.audited(), publisher=self.publisher, clock=self.clock)

    def partner(self, s: AsyncSession) -> PartnerService:
        return PartnerService(s, clock=self.clock)

    def analytics(self, s: AsyncSession) -> AnalyticsService:
        return AnalyticsService(s)


def build_container(settings: Settings, *, registry: Registry | None = None) -> Container:
    engine = make_engine(settings.database_url)
    return Container(
        settings=settings,
        engine=engine,
        factory=make_session_factory(engine),
        llm=BrokerLLM(
            settings.broker_url,
            settings.broker_project_key,
            poll_budget_s=settings.broker_poll_budget_s,
            subscription_budget_s=settings.broker_subscription_budget_s,
        ),
        registry=registry if registry is not None else default_registry(),
        box=make_box(settings),
        media=MediaStore(settings.media_dir),
    )
