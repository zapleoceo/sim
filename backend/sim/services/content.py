"""Instagram content: draft -> approval -> plan -> publish.

Publishing itself belongs to a connector that is not built yet: without a `ContentPublisher` the
post is marked failed with "not implemented" (visible in the admin, never silently dropped)."""
from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import ContentPost, Persona
from sim.adapters.db.repositories.catalog import PersonaRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.repositories.records import ContentRepo
from sim.clock import Clock, to_naive_utc, utcnow
from sim.domain.enums import ContentStatus
from sim.services import jobkinds
from sim.services.llm_audit import AuditedLLM

NOT_IMPLEMENTED = "publishing to Instagram is not implemented yet"
EDITABLE = (ContentStatus.DRAFT, ContentStatus.APPROVED, ContentStatus.SCHEDULED, ContentStatus.FAILED)


class ContentError(Exception):
    """Operation not allowed in the post's current state."""


class ContentPublisher(Protocol):
    async def publish(self, post: ContentPost) -> None: ...


def caption_prompt(persona: Persona, kind: str, idea: str) -> list[dict[str, Any]]:
    system = (
        f"You write Instagram captions for the virtual persona {persona.name}.\n"
        f"Character: {persona.character}\nCommunication style: {persona.communication_style}\n"
        f"Language: {persona.language}\n"
        "Never present the persona as a real human. Output ONLY the caption text: no quotes, "
        "no explanations, at most 1500 characters, then 3-6 relevant hashtags."
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

    async def update(self, post: ContentPost, **fields: Any) -> ContentPost:
        if post.status not in EDITABLE:
            raise ContentError(f"a {post.status.value} post cannot be edited")
        for key, value in self.normalise(fields).items():
            setattr(post, key, value)
        if post.status in (ContentStatus.APPROVED, ContentStatus.SCHEDULED):
            await self.plan(post)
        elif post.status is ContentStatus.FAILED:
            post.status, post.error = ContentStatus.DRAFT, None
        return post

    async def approve(self, post: ContentPost) -> ContentPost:
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
        result = await self.llm.chat(
            caption_prompt(persona, post.kind.value, post.caption),
            capability="chat:sales",
            model=persona.model or None,
            workflow="content",
        )
        return result.text.strip()

    async def publish(self, post_id: int) -> None:
        """Worker hook. Marks the post failed when no publisher is wired."""
        post = await self.posts.get(post_id)
        if post is None or post.status not in (ContentStatus.APPROVED, ContentStatus.SCHEDULED):
            return
        if self.publisher is None:
            post.status, post.error = ContentStatus.FAILED, NOT_IMPLEMENTED
            return
        try:
            await self.publisher.publish(post)
        except Exception as exc:  # the connector decides what went wrong; keep it visible
            post.status, post.error = ContentStatus.FAILED, f"{type(exc).__name__}: {exc}"[:500]
            return
        post.status, post.published_at, post.error = ContentStatus.PUBLISHED, self.clock(), None

    @staticmethod
    def normalise(fields: dict[str, Any]) -> dict[str, Any]:
        out = dict(fields)
        if out.get("scheduled_at") is not None:
            out["scheduled_at"] = to_naive_utc(out["scheduled_at"])
        return out
