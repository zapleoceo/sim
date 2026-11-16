"""Durable chat journal. Private bodies are encrypted, never included in logs/API.

The worker holds Job's row lock. These short independent commits intentionally
have no FK to Job, and every write is fenced by both generation and request key.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import BrokerRequest
from sim.clock import utcnow
from sim.ports.llm import LLMError
from sim.services.job_execution import Execution, ExecutionLost, require_live_execution

MAX_REQUEST_BYTES = 2_000_000
MAX_RESPONSE_BYTES = 1_000_000


def encoded(data: dict[str, Any], limit: int) -> str:
    value = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(value.encode()) > limit:
        raise LLMError("Broker recovery payload exceeds storage limit; no new request submitted")
    return value


@dataclass(frozen=True)
class RequestEntry:
    job_id: int
    call_index: int
    generation: int
    key: str
    remote_id: str | None
    status: str
    request: dict[str, Any] = field(repr=False)
    response: dict[str, Any] = field(repr=False)


class BrokerRequests:
    def __init__(self, factory: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        self.factory, self.box = factory, box

    def entry(self, row: BrokerRequest) -> RequestEntry:
        return RequestEntry(row.job_id, row.call_index, row.generation,
            row.idempotency_key, row.remote_id, row.status,
            self.box.decrypt_json(row.request_cipher), self.box.decrypt_json(row.response_cipher))

    @staticmethod
    def adopt(row: BrokerRequest, owner: Execution, scope: str) -> None:
        if row.generation > owner.generation:
            raise ExecutionLost("Broker request belongs to a newer execution")
        if row.scope != scope:
            raise LLMError("Broker authorization scope changed; reconcile the original request first")
        row.generation = owner.generation

    async def pending(self, owner: Execution, scope: str) -> list[RequestEntry]:
        await require_live_execution()
        async with self.factory() as s, s.begin():
            rows = list(await s.scalars(select(BrokerRequest).where(
                BrokerRequest.job_id == owner.job_id, BrokerRequest.status == "pending",
            ).order_by(BrokerRequest.call_index).with_for_update()))
            for row in rows:
                self.adopt(row, owner, scope)
            return [self.entry(row) for row in rows]

    async def prepare(
        self, owner: Execution, index: int, scope: str, request: dict[str, Any],
    ) -> RequestEntry:
        await require_live_execution()
        encoded(request, MAX_REQUEST_BYTES)
        identity = request
        if request.get("recovery_key"):
            # Only the conversation builder can identify time-only changes. Keep
            # model/capability/JSON/temperature settings in the identity as well.
            identity = {**request, "body": {k: v for k, v in request["body"].items() if k != "messages"}}
        fingerprint = hashlib.sha256(encoded(identity, MAX_REQUEST_BYTES).encode()).hexdigest()
        async with self.factory() as s, s.begin():
            row = await s.get(BrokerRequest, (owner.job_id, index), with_for_update=True)
            if row is not None:
                previous_generation = row.generation
                self.adopt(row, owner, scope)
                if row.status == "pending":
                    raise LLMError("Previous Broker request has an unknown outcome; reconcile first")
                if row.request_hash == fingerprint and (
                    row.status == "done" or previous_generation == owner.generation
                ):
                    return self.entry(row)
            else:
                row = BrokerRequest(job_id=owner.job_id, call_index=index, scope=scope)
                s.add(row)
            row.generation = owner.generation
            row.request_hash = fingerprint
            row.idempotency_key = "sim-" + uuid4().hex
            labelled = {**request, "body": {**request["body"],
                "labels": {"sim_request": row.idempotency_key}}}
            row.request_cipher = self.box.encrypt(encoded(labelled, MAX_REQUEST_BYTES))
            row.response_cipher = None
            row.remote_id = None
            row.status = "pending"
            row.created_at = row.updated_at = utcnow()
            return self.entry(row)

    async def checkpoint(
        self, entry: RequestEntry, *, remote_id: str | None = None,
        status: str = "pending", response: dict[str, Any] | None = None,
    ) -> RequestEntry:
        await require_live_execution()
        values: dict[str, Any] = {"status": status, "updated_at": utcnow()}
        if remote_id is not None:
            if not remote_id.isdecimal() or len(remote_id) > 80:
                raise LLMError("Invalid Broker job identity; outcome requires reconciliation")
            values["remote_id"] = remote_id
        if response is not None:
            values["response_cipher"] = self.box.encrypt(encoded(response, MAX_RESPONSE_BYTES))
        async with self.factory() as s, s.begin():
            result = await s.execute(update(BrokerRequest).where(
                BrokerRequest.job_id == entry.job_id, BrokerRequest.call_index == entry.call_index,
                BrokerRequest.generation == entry.generation,
                BrokerRequest.idempotency_key == entry.key,
                BrokerRequest.status == "pending",
            ).values(**values))
            row = await s.get(BrokerRequest, (entry.job_id, entry.call_index))
            if result.rowcount != 1:
                if (row is not None and row.generation == entry.generation
                        and row.idempotency_key == entry.key and row.status in {"done", "error"}):
                    return self.entry(row)  # concurrent observer cannot regress a terminal result
                raise ExecutionLost("Broker checkpoint rejected for stale execution")
            assert row is not None
            return self.entry(row)
