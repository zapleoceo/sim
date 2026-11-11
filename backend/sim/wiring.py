"""Dependency assembly: one Container builds the services for a unit of work (a request or a job)."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sim.adapters.audio import OggOpusEncoder
from sim.adapters.channels.telegram import BotApiTransport, TelegramTransport
from sim.adapters.crypto import SecretBox
from sim.adapters.db.session import make_engine, make_session_factory
from sim.adapters.llm.broker import BrokerLLM
from sim.adapters.media import MediaStore
from sim.clock import Clock, utcnow
from sim.config import Settings
from sim.connectors.spec import Connector
from sim.domain.enums import ChannelKind
from sim.ports.llm import LLMPort
from sim.ports.speech import SpeechPort
from sim.services.admin_bot import AdminBot
from sim.services.admins import AdminService
from sim.services.analytics import AnalyticsService
from sim.services.app_settings import env_defaults
from sim.services.auth import AuthService, LoginRateLimiter
from sim.services.channels import ChannelService
from sim.services.content import ContentPublisher, ContentService
from sim.services.conversation import ConversationService
from sim.services.followup import FollowupService
from sim.services.ingest import IngestService
from sim.services.instagram_content import InstagramContentPublisher
from sim.services.llm_audit import AuditedLLM
from sim.services.media import MediaService
from sim.services.outbound import OutboundService
from sim.services.partner import PartnerService
from sim.services.simulation import SimulationService
from sim.services.tracing import Tracer
from sim.services.voice import VoiceEncoder, VoiceRenderer

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
    clock: Clock = utcnow
    publisher: ContentPublisher | None = None
    admin_bot_transport: TelegramTransport | None = None   # tests inject a fake
    speech: SpeechPort | None = None                       # persona voice notes (Broker TTS)
    encoder: VoiceEncoder = field(default_factory=OggOpusEncoder)

    async def aclose(self) -> None:
        """Shutdown: release the LLM adapter's pooled HTTP client and the DB engine."""
        if (close := getattr(self.llm, "aclose", None)) is not None:
            await close()
        await self.engine.dispose()

    # ---- per-unit-of-work services -------------------------------------------------------
    def tracer(self, s: AsyncSession | None = None) -> Tracer:
        return Tracer(self.factory, self.clock, s)

    def audited(self) -> AuditedLLM:
        return AuditedLLM(self.llm, self.factory, self.clock, tracer=self.tracer())

    def auth(self, s: AsyncSession) -> AuthService:
        return AuthService(
            s,
            ttl_h=self.settings.session_ttl_h,
            limiter=self.limiter,
            bot_token=self.settings.tg_login_bot_token,
            clock=self.clock,
        )

    def admins(self, s: AsyncSession) -> AdminService:
        return AdminService(s)

    def outbound(self, s: AsyncSession) -> OutboundService:
        return OutboundService(
            s, self.registry, self.box, factory=self.factory, clock=self.clock,
            voice=self.voice_renderer(), tracer=self.tracer(s),
        )

    def voice_renderer(self) -> VoiceRenderer | None:
        if self.speech is None:
            return None
        return VoiceRenderer(self.speech, self.encoder, self.media)

    def conversation(self, s: AsyncSession) -> ConversationService:
        return ConversationService(
            s, self.audited(), self.outbound(s), clock=self.clock,
            settings_env=env_defaults(self.settings),
        )

    def simulation(self, s: AsyncSession) -> SimulationService:
        return SimulationService(s, self.conversation(s), clock=self.clock)

    def admin_transport(self) -> TelegramTransport | None:
        """Bot API transport of the login bot; None when no bot token is configured."""
        if self.admin_bot_transport is not None:
            return self.admin_bot_transport
        token = self.settings.tg_login_bot_token
        return BotApiTransport(token) if token else None

    def admin_bot(self, s: AsyncSession) -> AdminBot:
        transport = self.admin_transport()
        if transport is None:
            raise RuntimeError("SIM_TG_LOGIN_BOT_TOKEN is not set")
        return AdminBot(s, transport, self.simulation(s), self.llm, clock=self.clock)

    def followup(self, s: AsyncSession) -> FollowupService:
        return FollowupService(s, clock=self.clock)

    def ingest(self, s: AsyncSession) -> IngestService:
        return IngestService(
            s, debounce_s=self.settings.reply_debounce_s, clock=self.clock, tracer=self.tracer(s)
        )

    def media_service(self, s: AsyncSession) -> MediaService:
        return MediaService(s, self.registry, self.box, self.llm, self.media)

    def channels(self, s: AsyncSession) -> ChannelService:
        return ChannelService(s, self.registry, self.box, clock=self.clock)

    def content(self, s: AsyncSession) -> ContentService:
        publisher = self.publisher
        if publisher is None and self.settings.ig_content_enabled:
            publisher = InstagramContentPublisher(
                s, self.registry, self.box, self.media, self.settings.ig_content_post_ids, self.factory,
            )
        return ContentService(s, self.audited(), publisher=publisher, clock=self.clock)

    def partner(self, s: AsyncSession) -> PartnerService:
        return PartnerService(s, clock=self.clock, tracer=self.tracer(s))

    def analytics(self, s: AsyncSession) -> AnalyticsService:
        return AnalyticsService(s)


def build_container(settings: Settings, *, registry: Registry | None = None) -> Container:
    engine = make_engine(settings.database_url)
    broker = BrokerLLM(
        settings.broker_url,
        settings.broker_project_key,
        poll_budget_s=settings.broker_poll_budget_s,
        subscription_budget_s=settings.broker_subscription_budget_s,
    )
    return Container(
        settings=settings,
        engine=engine,
        factory=make_session_factory(engine),
        llm=broker,
        registry=registry if registry is not None else default_registry(),
        box=make_box(settings),
        media=MediaStore(settings.media_dir),
        speech=broker,   # the same Broker client serves LLMPort and SpeechPort
    )
