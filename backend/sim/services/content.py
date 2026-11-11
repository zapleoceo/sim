"""Instagram content: draft -> approval -> plan -> publish.

Instagram publishing is opt-in. Without a publisher, posts fail visibly."""
from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import ContentPost, ContentPublishIntent, Persona
from sim.adapters.db.repositories.catalog import PersonaRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.repositories.records import ContentRepo
from sim.clock import Clock, to_naive_utc, utcnow
from sim.domain.enums import ContentStatus
from sim.services import jobkinds
from sim.services.llm_audit import AuditedLLM

NOT_IMPLEMENTED = "Instagram publishing is disabled or no publisher is configured"
EDITABLE = (ContentStatus.DRAFT, ContentStatus.APPROVED, ContentStatus.SCHEDULED, ContentStatus.FAILED)


class ContentError(Exception):
    """Operation not allowed in the post's current state."""


class ContentPublisher(Protocol):
    async def publish(self, post: ContentPost) -> None: ...


# Owner-editable (app_setting "caption_rules").
CAPTION_RULES = (
    "Never present the persona as a real human. Output ONLY the caption text: no quotes, "
    "no explanations, at most 1500 characters, then 3-6 relevant hashtags."
)


def caption_prompt(
    persona: Persona, kind: str, idea: str, rules: str = CAPTION_RULES
) -> list[dict[str, Any]]:
    system = (
        f"You write Instagram captions for the virtual persona {persona.name}.\n"
        f"Persona description:\n{persona.description}\n"
        f"Language: {persona.language}\n"
        f"{rules.strip()}"
    )
    user = f"Post type: {kind}.\nIdea or draft: {idea or 'free choice about strategic thinking'}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class ContentService:
    def __init__(
        self,
        session: AsyncSession,
        llm: AuditedLLM,
        *,
        publisher: ContentPublisher | None = None,
        clock: Clock = utcnow,
    ) -> None:
        self.session = session
        self.posts = ContentRepo(session)
        self.personas = PersonaRepo(session)
        self.jobs = JobRepo(session)
        self.llm = llm
        self.publisher = publisher
        self.clock = clock

    async def create(self, **fields: Any) -> ContentPost:
        if await self.personas.get(fields["persona_id"]) is None:
            raise ContentError("persona not found")
        return await self.posts.add(ContentPost(**self.normalise(fields)))

    async def update(self, post: ContentPost, *, by_owner: bool, **fields: Any) -> ContentPost:
        """Only the owner approves. An operator's edit of an approved/scheduled post changes
        text nobody approved, so the post goes back to DRAFT and its publish job is cancelled."""
        post = await self.lock_post(post)
        if await self.session.get(ContentPublishIntent, post.id) is not None:
            raise ContentError("Publication already attempted; reconciliation required")
        if post.status not in EDITABLE:
            raise ContentError(f"a {post.status.value} post cannot be edited")
        for key, value in self.normalise(fields).items():
            setattr(post, key, value)
        if post.status in (ContentStatus.APPROVED, ContentStatus.SCHEDULED):
            if by_owner:
                await self.plan(post)
            else:
                await self.jobs.cancel_pending(jobkinds.CONTENT_PUBLISH, f"content:{post.id}")
                post.status = ContentStatus.DRAFT
        elif post.status is ContentStatus.FAILED:
            post.status, post.error = ContentStatus.DRAFT, None
        return post

    async def lock_post(self, post: ContentPost) -> ContentPost:
        locked = await self.posts.for_publish(post.id)
        if locked is None:
            raise ContentError("post not found")
        return locked

    async def approve(self, post: ContentPost) -> ContentPost:
        post = await self.lock_post(post)
        if await self.session.get(ContentPublishIntent, post.id) is not None:
            raise ContentError("Publication already attempted; reconciliation required")
        if post.status not in (ContentStatus.DRAFT, ContentStatus.FAILED):
            raise ContentError(f"a {post.status.value} post cannot be approved")
        post.status, post.error = ContentStatus.APPROVED, None
        await self.plan(post)
        return post

    async def plan(self, post: ContentPost) -> None:
        """APPROVED + a time -> SCHEDULED with one publish job at that time."""
        key = f"content:{post.id}"
        await self.jobs.cancel_pending(jobkinds.CONTENT_PUBLISH, key)
        if post.scheduled_at is not None:
            post.status = ContentStatus.SCHEDULED
            await self.jobs.enqueue(
                jobkinds.CONTENT_PUBLISH, {"post_id": post.id}, post.scheduled_at, dedup_key=key
            )
        else:
            post.status = ContentStatus.APPROVED

    async def generate_caption(self, post: ContentPost) -> str:
        persona = await self.personas.get(post.persona_id)
        if persona is None:
            raise ContentError("persona not found")
        from sim.services.app_settings import AppSettingsService  # local: it imports CAPTION_RULES

        cfg = await AppSettingsService(self.session).load()
        result = await self.llm.chat(
            caption_prompt(persona, post.kind.value, post.caption, cfg.caption_rules),
            capability="chat:sales",
            model=persona.model or None,
            workflow="content",
        )
        return result.text.strip()

    async def publish(self, post_id: int) -> None:
        """Worker hook. Marks the post failed when no publisher is wired."""
        post = await self.posts.for_publish(post_id)
        if post is None or post.status not in (ContentStatus.APPROVED, ContentStatus.SCHEDULED):
            return
        if post.scheduled_at is not None and post.scheduled_at > to_naive_utc(self.clock()):
            return
        if self.publisher is None:
            post.status, post.error = ContentStatus.FAILED, NOT_IMPLEMENTED
            return
        try:
            await self.publisher.publish(post)
        except ContentError as exc:
            post.status, post.error = ContentStatus.FAILED, str(exc)[:500]
            return
        except Exception:
            post.status, post.error = ContentStatus.FAILED, "Publication failed; reconcile before retrying"
            return
        post.status, post.published_at, post.error = (
            ContentStatus.PUBLISHED, post.published_at or self.clock(), None
        )

    @staticmethod
    def normalise(fields: dict[str, Any]) -> dict[str, Any]:
        out = dict(fields)
        if out.get("scheduled_at") is not None:
            out["scheduled_at"] = to_naive_utc(out["scheduled_at"])
        return out
