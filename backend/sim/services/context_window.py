"""The context window of a reply: which messages of the dialog the persona reads in full.

The window is every client-visible message of the last `context_window_hours` (owner setting,
48 = two days). Two corrections, both by code:

- a floor: when the window holds fewer than `MIN_MESSAGES` (the client came back after a long
  silence), the newest `MIN_MESSAGES` messages are taken instead, so the persona always sees the
  latest exchange word for word;
- a ceiling: when the window is larger than `context_token_limit` (an estimate, see
  `estimate_tokens`), the OLDEST messages of the window are cut and the cut is stated in the
  context. The newest message always stays.

Everything older than the first kept message belongs to the summary (sim.services.summary):
`Window.boundary_id` is where the summary may grow to."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Message
from sim.adapters.db.repositories.conversations import MessageRepo
from sim.clock import Clock, utcnow
from sim.services.ai_media import history_text

MIN_MESSAGES = 10          # the floor: the latest exchange is always read word for word
MAX_ROWS = 400             # messages loaded for one window before the token limit is applied
CHARS_PER_TOKEN = 3        # Cyrillic chat text: about three characters per token
MESSAGE_OVERHEAD = 4       # role and separators of one message


def estimate_tokens(text: str) -> int:
    """A cheap upper-side estimate; the real tokenizer is the provider's."""
    return -(-len(text) // CHARS_PER_TOKEN) + MESSAGE_OVERHEAD


@dataclass(frozen=True)
class Window:
    messages: list[Message]        # what the persona reads in full, oldest first
    since: datetime                # the start of the time window
    hours: int
    trimmed: int = 0               # messages of the window cut by the token limit
    tokens: int = 0                # the estimate for `messages`

    @property
    def boundary_id(self) -> int | None:
        """The first message read in full: the summary covers what is older. None: no messages."""
        return self.messages[0].id if self.messages else None

    def note(self) -> str:
        """The line of the context that states a cut; "" when nothing was cut."""
        if not self.trimmed:
            return ""
        return (f"ИСТОРИЯ ОБРЕЗАНА: за последние {self.hours} ч сообщений больше, чем помещается "
                f"в лимит контекста. {self.trimmed} самых старых из них в историю выше не вошли; "
                "что было раньше, смотри в саммари.")


def fit(messages: list[Message], token_limit: int) -> tuple[list[Message], int, int]:
    """(kept, trimmed, tokens): the newest messages that fit the limit, oldest first. The newest
    message is kept even when it alone is over the limit."""
    kept: list[Message] = []
    total = 0
    for message in reversed(messages):
        cost = estimate_tokens(history_text(message))
        if kept and total + cost > token_limit:
            break
        kept.append(message)
        total += cost
    kept.reverse()
    return kept, len(messages) - len(kept), total


class ContextWindow:
    def __init__(self, session: AsyncSession, *, clock: Clock = utcnow) -> None:
        self.messages = MessageRepo(session)
        self.clock = clock

    async def load(self, dialog_id: int, *, hours: int, token_limit: int,
                   min_messages: int = MIN_MESSAGES) -> Window:
        since = self.clock() - timedelta(hours=hours)
        rows = await self.messages.visible_since(dialog_id, since, MAX_ROWS)
        in_window = len(rows)
        if in_window < min_messages:
            rows = await self.messages.recent_visible(dialog_id, min_messages)
        kept, _, tokens = fit(rows, token_limit)
        # Only messages of the time window count as cut; the floor's older messages that did
        # not fit simply stay with the summary.
        return Window(kept, since, hours, max(0, in_window - len(kept)), tokens)
