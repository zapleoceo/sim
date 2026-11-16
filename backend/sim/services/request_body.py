"""The body of a traced request, as the system log may keep it (`detail.request_body`).

Only text bodies (JSON, form, plain text) are kept: secret-looking keys are hidden at any depth,
string values go through `safe_payload`, and the result is cut at `LIMIT`. Files and binary
bodies are described by type and size only. Sign-in and OAuth bodies are never kept.

Stored shapes: `{"content": <json or text>, "truncated": bool}` or `{"omitted": "<why>"}`.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl

from sim.services.safe_payload import safe_payload

LIMIT = 16 * 1024          # what the log keeps
READ_LIMIT = 1024 * 1024   # bigger bodies are not even read into the log
BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")
PRIVATE_PREFIXES = ("/api/auth/", "/api/oauth/")
REDACTED = "[REDACTED]"
NOT_KEPT = "тело не сохраняется (вход или авторизация)"

_SECRET_PART = re.compile(
    r"^(password|passwd|pwd|token|tokens|secret|key|apikey|session|hash|cookie|cookies|"
    r"authorization|auth|csrf|xsrf|signature|sig|otp|credential|credentials|private)$")
_SECRET_CODE = re.compile(r"(?i)^(code|(auth|login|verify|verification|otp|sms|phone|confirm|access)_?code)$")
_TEXT_TYPES = ("application/json", "application/x-www-form-urlencoded", "text/", "application/xml")


def key_parts(name: str) -> list[str]:
    """`apiKey`, `api_key`, `X-Api-Key` -> ["api", "key"]."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [p for p in re.split(r"[^a-z0-9]+", spaced.lower()) if p]


def is_secret_key(name: str) -> bool:
    parts = key_parts(name)
    return bool(_SECRET_CODE.match("_".join(parts))) or any(_SECRET_PART.match(p) for p in parts) \
        or "".join(parts) in ("apikey", "accesstoken", "refreshtoken", "csrftoken")


def redact(value: Any) -> Any:
    """Hide the values of secret-looking keys at any depth."""
    if isinstance(value, dict):
        return {k: REDACTED if is_secret_key(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def wants_body(method: str, path: str) -> bool:
    return path.startswith("/hooks/") or method in BODY_METHODS


def is_text(content_type: str) -> bool:
    kind = content_type.split(";", 1)[0].strip().lower()
    return not kind or kind.startswith(_TEXT_TYPES) or kind.endswith("+json")


def omitted(content_type: str, size: int | None) -> dict[str, str]:
    kind = content_type.split(";", 1)[0].strip() or "без типа"
    return {"omitted": f"{kind}, {size if size is not None else '?'} байт — тело не сохраняется"}


def parse(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="replace")
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind.endswith("x-www-form-urlencoded"):
        return dict(parse_qsl(text, keep_blank_values=True))
    try:
        return json.loads(text)
    except ValueError:
        return text


def capture(raw: bytes, content_type: str) -> dict[str, Any]:
    """What the log keeps of a text body that was read."""
    clean = safe_payload(redact(parse(raw, content_type)))
    content, truncated = clean["content"], clean["truncated"]
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    if len(text) > LIMIT:
        return {"content": text[:LIMIT], "truncated": True}
    return {"content": content, "truncated": truncated}


def body_out(value: Any) -> dict[str, Any] | None:
    """The API projection: secrets are hidden again, in case of rows written by older code."""
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("omitted"), str):
        return {"omitted": value["omitted"][:200]}
    if "content" not in value:
        return None
    clean = safe_payload(redact(value["content"]))
    return {"content": clean["content"], "truncated": clean["truncated"] or bool(value.get("truncated")),
            "redacted": True}
