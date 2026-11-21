"""`Idempotency-Key` of the Broker's queued jobs (`/v1/jobs`, `/v1/images/jobs`,
`/v1/video/jobs`), docs/broker.md §2b.

The key comes from the call scope (`sim.call_scope`) and the request, never from the attempt:
- an ordinary scope hashes the request itself (route, query, body), so a retry of the same
  task with the same request re-attaches to the Broker job of the first attempt, and any other
  request (a repair round, another chat) gets a new key;
- a sequential scope (one reply turn) hashes the call's position and identity (route, query,
  model, workflow) instead of its text, because the retried turn's prompt carries a newer time;
- no scope, no key (the Broker's own in-flight dedup stays as it was).

Keys are `sim-job-<sha256>` (72 characters of `[a-z0-9-]`, the Broker allows 128 of
`[A-Za-z0-9._:-]`); transcription keeps its own `sim-tr-` keys (transcribe.py)."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sim.call_scope import CallScope

HEADER = "Idempotency-Key"
KEY_PREFIX = "sim-job-"


def _digest(*parts: Any) -> str:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)
    return KEY_PREFIX + hashlib.sha256(text.encode()).hexdigest()


def job_key(scope: CallScope | None, path: str, params: Mapping[str, str] | None,
            body: Mapping[str, Any]) -> str | None:
    """The key of one submit in `scope` (None outside a scope). A sequential scope counts the
    call, so it must be asked exactly once per submit."""
    if scope is None:
        return None
    query = dict(params or {})
    if scope.sequential:
        subject: dict[str, Any] = {"call": scope.next_call(), "model": body.get("model"),
                                   "workflow": body.get("workflow")}
    else:
        subject = {"body": dict(body)}
    return _digest(scope.name, path, query, subject)


def next_key(key: str, failed_job_id: str) -> str:
    """The key after a replayed failed job: the Broker keeps a failed job under its key for
    days, so a retry walks on to a new key derived from the failed one (still deterministic:
    the next retry walks the same chain and re-attaches to the job started here)."""
    return _digest(key, "after", failed_job_id)


def conflict_job_id(status: int | None, payload: Any) -> str | None:
    """The job that already owns the key, from a 409 `idempotency_key_conflict` answer
    (`{"detail": {"error": ..., "existing_job_id": N}}`), else None."""
    if status != 409 or not isinstance(payload, Mapping):
        return None
    detail = payload.get("detail")
    if not isinstance(detail, Mapping) or detail.get("error") != "idempotency_key_conflict":
        return None
    existing = detail.get("existing_job_id")
    return str(existing) if isinstance(existing, int | str) and str(existing) else None
