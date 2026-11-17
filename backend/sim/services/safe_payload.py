"""Bounded server-side diagnostic projection. Never includes HTTP headers or credentials."""
from __future__ import annotations

import json
import re
from typing import Any

LIMIT = 65536
SECRET_KEY = re.compile(r"(?i)(authorization|headers|cookie|password|passwd|secret|api.?key|project.?key|private.?key|access.?token|refresh.?token|credential|session.?token|^(key|token|session)$)")
PEM = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
ASSIGNMENT = re.compile(r'''(?ix)(["']?\b(?:authorization|cookie|password|passwd|secret|token|key|api[_-]?key|project[_-]?key|access[_-]?token|refresh[_-]?token)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')
BEARER = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
HEADER_LINE = re.compile(r"(?im)\b(?:authorization|set-cookie|cookie)\s*:\s*[^\r\n]+")
QUERY_SECRET = re.compile(r"(?i)([?&](?:token|key|secret|password|code|session)=)[^&#\s]+")
USERINFO = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")
KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|\d{6,}:[A-Za-z0-9_-]{20,})\b")
DATA_URL = re.compile(r"(?i)data:[^\s,;]+(?:;[^\s,]*)?;base64,[A-Za-z0-9+/=_-]+")


def exchange_out(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out = {}
    for key in ("request", "response", "accepted"):
        entry = value.get(key)
        if isinstance(entry, dict) and "content" in entry:
            clean = safe_payload(entry["content"])
            clean["truncated"] = clean["truncated"] or bool(entry.get("truncated"))
            out[key] = clean
    return out


def safe_payload(value: Any, *, secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    remaining = LIMIT
    nodes = 2000
    truncated = False

    def clean(item: Any, depth: int = 0) -> Any:
        nonlocal remaining, truncated, nodes
        nodes -= 1
        if remaining <= 0 or depth > 12 or nodes <= 0:
            truncated = True
            return "[TRUNCATED]"
        if isinstance(item, dict):
            out = {}
            for index, (key, val) in enumerate(item.items()):
                if index >= 500 or remaining <= 0 or nodes <= 0:
                    truncated = True
                    break
                original_name = str(key)
                name = str(clean(original_name, depth + 1))[:120]
                out[name] = "[REDACTED]" if SECRET_KEY.search(original_name) else clean(val, depth + 1)
            return out
        if isinstance(item, (list, tuple)):
            out = []
            for val in item[:500]:
                if remaining <= 0 or nodes <= 0:
                    break
                out.append(clean(val, depth + 1))
            if len(out) < len(item):
                truncated = True
            return out
        if isinstance(item, str):
            # Embedded media is transport data, never diagnostic text (including copy/export).
            item = DATA_URL.sub("[REDACTED MEDIA]", item)
            # JSON embedded in model content is still subject to key-based redaction.
            if item.lstrip().startswith(("{", "[")):
                try:
                    return json.dumps(clean(json.loads(item), depth + 1), ensure_ascii=False)
                except (ValueError, RecursionError):
                    pass
            for secret in secrets:
                if secret:
                    item = item.replace(secret, "[REDACTED]")
            item = PEM.sub("[REDACTED PRIVATE KEY]", item)
            item = HEADER_LINE.sub("[REDACTED HEADER]", item)
            item = QUERY_SECRET.sub(r"\1[REDACTED]", item)
            item = USERINFO.sub(r"\1[REDACTED]@", item)
            item = BEARER.sub("[REDACTED AUTH]", item)
            item = ASSIGNMENT.sub(r"\1[REDACTED]", item)
            item = KEY.sub("[REDACTED KEY]", item)
            if len(item) > remaining:
                truncated = True
            result = item[:remaining]
            remaining -= len(result)
            return result
        remaining -= 24
        return item if item is None or isinstance(item, (bool, int, float)) else "[UNSUPPORTED]"

    return {"content": clean(value), "truncated": truncated, "redacted": True}
