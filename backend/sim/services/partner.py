"""Partner confirmations: registrations and payments. Only this service may move a client to
registered / paid (Actor.PARTNER) - never the AI and never an operator by hand."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import time
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import PartnerEvent
from sim.adapters.db.repositories.conversations import ClientRepo, DialogRepo
from sim.adapters.db.repositories.records import PartnerEventRepo
from sim.clock import Clock, to_naive_utc, utcnow
from sim.domain.enums import PartnerEventKind, Stage
from sim.domain.funnel import Actor
from sim.services import tracing
from sim.services.followup import FollowupService
from sim.services.funnel import FunnelService
from sim.services.tracing import Tracer

REPLAY_WINDOW_S = 300
CSV_COLUMNS = ("client_ref", "event", "amount", "currency", "payout", "occurred_at", "external_id")
EVENT_ALIASES = {
    "registration": PartnerEventKind.REGISTRATION,
    "register": PartnerEventKind.REGISTRATION,
    "payment": PartnerEventKind.PAYMENT,
    "paid": PartnerEventKind.PAYMENT,
}
STAGE_FOR = {PartnerEventKind.REGISTRATION: Stage.REGISTERED, PartnerEventKind.PAYMENT: Stage.PAID}


class PartnerAuthError(Exception):
    """Bad or missing signature, stale timestamp."""


class PartnerDataError(ValueError):
    """The event itself is unusable (unknown client ref, malformed fields)."""


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """hex(hmac_sha256(secret, ts + "." + body)) - the scheme in docs/api.md."""
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    *,
    now: float | None = None,
    window_s: int = REPLAY_WINDOW_S,
) -> None:
    if not secret:
        raise PartnerAuthError("partner webhook secret is not configured")
    if not timestamp or not signature:
        raise PartnerAuthError("missing signature headers")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise PartnerAuthError("bad timestamp") from exc
    if abs((time.time() if now is None else now) - ts) > window_s:
        raise PartnerAuthError("timestamp outside the replay window")
    if not hmac.compare_digest(sign(secret, timestamp, body), signature.strip().lower()):
        raise PartnerAuthError("bad signature")


@dataclass(frozen=True)
class PartnerEventIn:
    client_ref: str
    kind: PartnerEventKind
    external_id: str
    occurred_at: datetime
    amount: float | None = None
    currency: str = ""
    payout: float | None = None
    source: str = "webhook"


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class PartnerService:
    def __init__(
        self, session: AsyncSession, *, clock: Clock = utcnow, tracer: Tracer | None = None
    ) -> None:
        self.session = session
        self.clock = clock
        self.tracer = tracer
        self.clients = ClientRepo(session)
        self.events = PartnerEventRepo(session)
        self.dialogs = DialogRepo(session)
        self.funnel = FunnelService(session)
        self.followups = FollowupService(session, clock=clock)

    async def record(self, event: PartnerEventIn) -> bool:
        """Store the event and move the client. False = already seen (idempotent).
        Each event is a `partner` step of the trace log (an unknown client is an error step)."""
        if self.tracer is None:
            return await self.store(event)
        async with self.tracer.step(tracing.PARTNER, f"partner {event.kind.value}") as step:
            step.detail = {"source": event.source, "amount": event.amount, "currency": event.currency}
            stored = await self.store(event)
            step.status = tracing.OK if stored else tracing.SKIPPED
            step.detail["duplicate"] = not stored
            client = await self.clients.by_ref(event.client_ref)
            step.links["client_id"] = client.id if client else None
        return stored

    async def store(self, event: PartnerEventIn) -> bool:
        client = await self.clients.by_ref(event.client_ref)
        if client is None:
            raise PartnerDataError(f"unknown client_ref {event.client_ref!r}")
        if await self.events.find(event.kind, event.external_id):
            return False
        try:
            # Savepoint: a concurrent twin that won the race trips UniqueConstraint(kind,
            # external_id) here; only the savepoint rolls back, the outer transaction lives on.
            async with self.session.begin_nested():
                await self.events.add(
                    PartnerEvent(
                        client_id=client.id,
                        kind=event.kind,
                        external_id=event.external_id,
                        amount=event.amount,
                        currency=event.currency,
                        payout=event.payout,
                        source=event.source,
                        occurred_at=to_naive_utc(event.occurred_at),
                    )
                )
        except IntegrityError:
            return False
        await self.funnel.advance(
            client, STAGE_FOR[event.kind], Actor.PARTNER, f"partner {event.kind.value}"
        )
        for dialog in await self.dialogs.for_client(client.id):
            await self.followups.cancel(dialog.id)
        return True

    async def import_csv(self, content: bytes) -> ImportResult:
        result = ImportResult()
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        missing = {"client_ref", "event"} - set(reader.fieldnames or [])
        if missing:
            result.errors.append(f"missing columns: {', '.join(sorted(missing))}")
            return result
        for line, row in enumerate(reader, start=2):
            try:
                event = self.parse_row(row)
                if await self.record(event):
                    result.imported += 1
                else:
                    result.skipped += 1
            except (PartnerDataError, ValueError) as exc:
                result.errors.append(f"line {line}: {exc}")
        return result

    @staticmethod
    def parse_row(row: dict[str, str | None]) -> PartnerEventIn:
        def cell(name: str) -> str:
            return (row.get(name) or "").strip()

        kind = EVENT_ALIASES.get(cell("event").lower())
        if kind is None:
            raise PartnerDataError(f"unknown event {cell('event')!r}")
        if not cell("client_ref"):
            raise PartnerDataError("empty client_ref")
        if not cell("external_id") and not cell("occurred_at"):
            raise PartnerDataError("external_id or occurred_at is required for repeatable CSV imports")
        occurred = to_naive_utc(datetime.fromisoformat(cell("occurred_at").replace("Z", "+00:00"))) \
            if cell("occurred_at") else utcnow()
        external_id = cell("external_id") or (
            f"csv:{cell('client_ref')}:{kind.value}:{occurred.isoformat()}"
        )
        return PartnerEventIn(
            client_ref=cell("client_ref"),
            kind=kind,
            external_id=external_id,
            occurred_at=occurred,
            amount=float(cell("amount")) if cell("amount") else None,
            currency=cell("currency"),
            payout=float(cell("payout")) if cell("payout") else None,
            source="csv",
        )
