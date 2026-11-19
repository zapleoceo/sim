"""Read-only projection of known operational boilerplate in derived conversation memory."""
from __future__ import annotations

import re

# Match an entire system-style memory line, not mentions of an operator or media in dialogue.
_OPERATIONAL_LINE = re.compile(
    r"(?:фактическая )?передача(?: оператору)? и ответ человека не подтверждены"
    r"(?:;\s*(?:фото и кружочек|медиа)(?: в диалоге)? не отправлены)?[.!]?",
    re.IGNORECASE,
)


def conversational_memory(text: str) -> str:
    """Keep conversation facts; omit known audit boilerplate without changing saved text."""
    return "\n".join(
        line for line in text.splitlines()
        if not _OPERATIONAL_LINE.fullmatch(" ".join(line.lstrip(" -•*\t").split()))
    ).strip()
