"""The role game in a dialog (rules and vocabulary: sim.domain.game, docs/game.md).

Who may play is decided here, by code. The game exists only on the channel named by the owner
setting `game_channel_id` (0 = nowhere) and only while an active game persona (`persona.is_game`)
exists. The state lives in the dialog: `game_state` and `game_consent_at`.

  speaker()  who answers a dialog right now: the game persona in role, the channel's own persona
             (openly an AI assistant once a game ended there), or nobody.
  play()     turns the interlocutor's unanswered messages into a move, applies it and returns the
             fixed notice to send; None when the messages are plain talk.

A game persona never answers outside a running game: attached to a channel directly, she stays
silent for everyone who has not started it."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Dialog, Persona
from sim.adapters.db.repositories.conversations import ClientRepo
from sim.clock import Clock, utcnow
from sim.domain.enums import ChannelKind
from sim.domain.game import (
    ASSISTANT_IDENTITY,
    CONSENT_PROMPT,
    MINOR_NOTICE,
    OUT_OF_ROLE,
    RESTART_NOTICE,
    SAFETY_NOTICE,
    START_NOTICE,
    STOP_NOTICE,
    GameState,
    Move,
    next_move,
)
from sim.services.app_settings import AppSettingsService
from sim.services.dialog_reset import DialogReset

CHANNEL_KEY = "game_channel_id"
OWNER_KEY = "game_owner_telegram_id"
TELEGRAM_KINDS = (ChannelKind.TELEGRAM, ChannelKind.TELEGRAM_USER)

# Where a move leaves the dialog. A wipe ends a running game and forgets an unanswered consent
# screen; DECLINE is "no game": the channel's persona goes on as if nothing was asked.
AFTER_RESTART = {GameState.ON: GameState.OVER, GameState.ASK: GameState.IDLE}
AFTER = {
    Move.ASK: GameState.ASK, Move.START: GameState.ON, Move.STOP: GameState.OVER,
    Move.SAFETY: GameState.OVER, Move.MINOR: GameState.MINOR, Move.DECLINE: GameState.IDLE,
}


@dataclass(frozen=True)
class Speaker:
    persona: Persona
    playing: bool = False       # the game persona, in role
    assistant: bool = False     # the channel's persona after a game: openly an AI assistant

    def identity(self) -> str:
        """The out-of-role rule for the reply's dynamic context ("" while it does not apply)."""
        return ASSISTANT_IDENTITY.format(name=self.persona.name) if self.assistant else ""


def state_of(dialog: Dialog) -> GameState:
    try:
        return GameState(dialog.game_state or "")
    except ValueError:              # a value from a newer release: treat the game as over
        return GameState.OVER


class GameService:
    def __init__(self, session: AsyncSession, *, clock: Clock = utcnow) -> None:
        self.session = session
        self.clock = clock
        self.settings = AppSettingsService(session)
        self.clients = ClientRepo(session)

    async def number(self, key: str) -> int:
        return int((await self.settings.item(key)).value)

    async def persona(self) -> Persona | None:
        """The active game persona (the oldest one, should the owner ever create several)."""
        return await self.session.scalar(
            select(Persona).where(Persona.is_game.is_(True), Persona.is_active.is_(True))
            .order_by(Persona.id).limit(1)
        )

    async def enabled(self, dialog: Dialog) -> bool:
        channel_id = await self.number(CHANNEL_KEY)
        return channel_id > 0 and dialog.channel_id == channel_id

    async def is_owner(self, dialog: Dialog) -> bool:
        """The owner's private Telegram chat: the chat itself and the sender carry his id."""
        owner = str(await self.number(OWNER_KEY))
        if owner == "0" or dialog.channel.kind not in TELEGRAM_KINDS:
            return False
        if dialog.external_thread_id != owner:
            return False
        return any(
            identity.channel_id == dialog.channel_id and identity.external_id == owner
            for identity in await self.clients.identities_of(dialog.client_id)
        )

    async def player(self, dialog: Dialog) -> Persona | None:
        """The game persona when a game is running in this dialog right now."""
        if state_of(dialog) is not GameState.ON or not await self.enabled(dialog):
            return None
        return await self.persona()

    async def speaker(self, dialog: Dialog, base: Persona) -> Speaker | None:
        """Who answers this dialog; `base` is the persona the client belongs to."""
        game = await self.player(dialog)
        if game is not None:
            return Speaker(game, playing=True)
        if base.is_game:
            return None
        return Speaker(base, assistant=state_of(dialog) in OUT_OF_ROLE)

    async def caller(self, dialog: Dialog, base: Persona) -> Persona | None:
        """Who picks up a phone call in this dialog. The game persona takes only the owner's
        calls; any other player is answered by the channel's own persona."""
        speaker = await self.speaker(dialog, base)
        if speaker is None:
            return None
        if speaker.playing and not await self.is_owner(dialog):
            return None if base.is_game else base
        return speaker.persona

    async def voice(self, dialog: Dialog) -> Persona | None:
        """Whose voice an AI voice note of this dialog is rendered with."""
        return await self.player(dialog) or await self.session.get(Persona, dialog.client.persona_id)

    async def play(self, dialog: Dialog, base: Persona, texts: Sequence[str | None]) -> str | None:
        """Apply what the unanswered messages ask of the game. Returns the fixed notice to send,
        or None for plain talk. Replies of one dialog run one at a time (the reply lock)."""
        if not await self.enabled(dialog):
            return None
        game = await self.persona()
        if game is None:
            return None
        state = state_of(dialog)
        consented = dialog.game_consent_at is not None or await self.is_owner(dialog)
        move = next_move(state, texts, consented=consented)
        if move is None:
            return None
        if move is Move.RESTART:
            await DialogReset(self.session).reset(dialog, dialog.client)
            dialog.game_state = AFTER_RESTART.get(state, state).value
            return RESTART_NOTICE
        if move is Move.START and dialog.game_consent_at is None:
            dialog.game_consent_at = self.clock()      # the owner's start is his consent too
        dialog.game_state = AFTER[move].value
        return self.notice(move, game, base)

    def leave(self, dialog: Dialog) -> str:
        """The game persona herself asked to stop the game (her `handoff_to_operator`): the same
        exit as the distress phrases."""
        dialog.game_state = AFTER[Move.SAFETY].value
        return SAFETY_NOTICE

    @staticmethod
    def notice(move: Move, game: Persona, base: Persona) -> str | None:
        return {
            Move.ASK: CONSENT_PROMPT,
            Move.START: START_NOTICE.format(name=game.name),
            Move.STOP: STOP_NOTICE.format(name=base.name),
            Move.SAFETY: SAFETY_NOTICE,
            Move.MINOR: MINOR_NOTICE,
        }.get(move)
