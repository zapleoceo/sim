"""Read-only projection of known operational boilerplate in derived conversation memory."""
from __future__ import annotations

import re

# Match a complete sentence at the end of a derived memory line, not a quoted
# phrase or general mentions of an operator or media. Real turns are never filtered here.
_OPERATIONAL_BODY = (
    r"(?:фактическая\s+)?передача(?:\s+оператору)?\s+и\s+ответ\s+человека\s+не\s+подтверждены"
    r"(?:;\s*(?:фото\s+и\s+кружочек|медиа)(?:\s+в\s+диалоге)?\s+не\s+отправлены)?[.!]?"
)
_OPERATIONAL_LINE = re.compile(_OPERATIONAL_BODY, re.IGNORECASE)
_OPERATIONAL_TAIL = re.compile(rf"(?<=[.!?])\s+{_OPERATIONAL_BODY}\s*$", re.IGNORECASE)


def conversational_memory(text: str) -> str:
    """Omit known audit boilerplate from derived memory, without changing saved text."""
    lines = []
    for line in text.splitlines():
        if _OPERATIONAL_LINE.fullmatch(line.lstrip(" -•*\t").rstrip()):
            continue
        lines.append(_OPERATIONAL_TAIL.sub("", line))
    return "\n".join(lines).strip()
