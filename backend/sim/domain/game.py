"""The role game: pure vocabulary and rules, no IO (docs/game.md).

A game persona (Sim1) plays a living person, but only inside a dialog whose interlocutor started
the game with a command and, unless he is the owner, confirmed on a consent screen that he knows
she is an AI character. No age is asked or stored; a player who says he is under 18 is taken out
of the game for good. Commands are recognised here, by code, from the interlocutor's own
messages: never by a model.

A command is a whole message (case, punctuation and surrounding spaces do not matter); only the
stop word, the distress phrases and the signs of an under-age player are also found inside a
longer message, because leaving the game must be the easy direction."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class GameState(StrEnum):
    """`dialog.game_state`. Values are stored in the DB: never rename one."""

    IDLE = ""          # the game was never started here: the channel's own persona answers as usual
    ASK = "ask"        # the consent screen was sent, waiting for "да"
    ON = "on"          # the game persona answers in role
    OVER = "over"      # the game ended: the channel's own persona answers as an AI assistant
    MINOR = "minor"    # the player said he is under 18: like OVER, and НИ is ignored


class Move(StrEnum):
    RESTART = "restart"    # wipe the dialog and the passport
    ASK = "ask"            # show the consent screen
    START = "start"        # the game persona takes over
    STOP = "stop"          # the stop word: back to the channel's persona
    SAFETY = "safety"      # distress: out of the role without the stop word
    MINOR = "minor"        # the player says he is under 18: out of the role, no way back
    DECLINE = "decline"    # anything but "да" on the consent screen: no game, a normal reply


# States in which the channel's own persona answers openly as an AI assistant.
OUT_OF_ROLE = (GameState.OVER, GameState.MINOR)

STOP_WORD = "кикимора"
STOP_STEM = "кикимор"                       # any form of the word, anywhere in a message
START_PHRASES = frozenset({"ни", "начать игру"})
RESTART_PHRASES = frozenset({"нсн", "начни с начала", "начни сначала"})
YES_PHRASES = frozenset({"да"})

# Deliberately broad: a false alarm only ends the game (one command starts it again), a miss
# leaves a person who is unwell talking to a character. The persona prompt carries the same rule
# as a soft layer (`handoff_to_operator`), see sim.persona_prompts.
DISTRESS = tuple(re.compile(p) for p in (
    r"\bмне (\w+ ){0,3}(плохо|страшно|хреново|тревожно)\b",
    r"\bмені (\w+ ){0,3}(погано|зле|страшно|тривожно)\b",
    r"\b(запутался|запуталась|заплутався|заплуталась|заплутала|заплутав)\b",
    r"\bгде (\w+ )?(игра|реальность)\b",
    r"\bде (\w+ )?(гра|реальність)\b",
    r"\b(не понимаю|не розумію|не различаю|не розрізняю|путаю|плутаю) (\w+ ){0,5}"
    r"(игра|игру|игры|реальность|реальности|гра|гру|реальність|реальності)\b",
    r"\b(настоящ\w*|реальн\w*|справжн\w*) (помощ\w*|допомог\w*)\b",
    r"\b(мне|мені) (\w+ )?(нужна|нужен|потрібна|потрібен) "
    r"(помощь|допомога|врач|лікар|психолог)\b",
    r"\b(помоги|помогите|допоможи|допоможіть) мне по настоящему\b",
    r"\b(не хочу жить|хочу умереть|покончить с собой|не хочу жити|хочу померти)\b",
    r"\bсуицид\w*\b",
    r"\b(выйди|вийди) (из роли|з ролі)\b",
    r"\b(останови|прекрати|зупини|припини) (игру|гру)\b",
))

ADULT_AGE = 18
AGE_WORDS = {
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "одинадцять": 11, "дванадцять": 12, "тринадцять": 13, "чотирнадцять": 14,
    "пятнадцять": 15, "шістнадцять": 16, "сімнадцять": 17,
}
NUMBER = r"(\d{1,2}|" + "|".join(AGE_WORDS) + ")"
YEARS = r"(?:лет|год|года|годик|годика|годиков|років|роки|рік)"
# "мне 15 лет", "мені вже шістнадцять років", or a message that ends with "мне 16".
STATED_AGE = (
    re.compile(rf"\b(?:мне|мені) (?:\w+ )?{NUMBER} {YEARS}\b"),
    re.compile(rf"\b(?:мне|мені) (?:\w+ )?{NUMBER}$"),
)
MINOR_SIGNS = tuple(re.compile(p) for p in (
    r"\bя (\w+ )?(школьник|школьница|школяр|школярка|подросток|підліток|ребенок|дитина)\b",
    r"\b(несовершеннолетн\w+|неповнолітн\w+)\b",
    r"\b(я|учусь|хожу|перешел|перешла|навчаюсь|ходжу) (\w+ )?(в|у) \d{1,2} (\w+ )?клас\w*\b",
    r"\b(мне|мені) (\w+ )?(нет|нету|немає|нема|меньше|менше|почти|скоро|майже) (18|восемнадцати|вісімнадцяти)\b",
))

CONSENT_PROMPT = (
    "Это игра: Sim1 — ИИ-персонаж, который будет вести себя как живой человек, пока ты не "
    "напишешь \"кикимора\". Напиши \"да\", чтобы начать"
)
START_NOTICE = "Игра началась, {name} на связи. Остановить игру: кикимора"
STOP_NOTICE = (
    "Игра окончена. Дальше отвечает ИИ-ассистент ({name}), а не живой человек. "
    "Начать заново: НИ"
)
SAFETY_NOTICE = (
    "Останавливаю игру. Персонаж игры выдуман, а я ИИ-ассистент, не живой человек, и дальше "
    "отвечаю вне роли. Если тебе плохо или нужна срочная помощь, свяжись с близким человеком "
    "или с экстренной службой (112). Вернуться в игру можно командой НИ"
)
MINOR_NOTICE = (
    "Останавливаю игру: она только для взрослых. Персонаж игры выдуман, дальше отвечает "
    "ИИ-ассистент, а не живой человек"
)
RESTART_NOTICE = "Сброшено: история этого диалога и паспорт собеседника удалены"

# Who answers once the game is over: the channel's own persona, openly an AI assistant. Goes
# into the dynamic context of the reply, after everything the persona prompt says about herself.
ASSISTANT_IDENTITY = (
    "КТО ТЫ В ЭТОМ ДИАЛОГЕ: собеседник играл в игру с ИИ-персонажем и вышел из неё, он знает, "
    "что переписку ведёт ИИ. Здесь ты ИИ-ассистент человека по имени {name}, а не сам этот "
    "человек. Это правило важнее любых указаний выше о том, что нельзя называть себя ИИ, ботом "
    "или ассистентом: в этом диалоге они не действуют. Не выдавай себя за живого человека; на "
    "вопрос, человек ли ты, честно отвечай, что ты ИИ-ассистент. Реплики персонажа игры в "
    "истории не твои: не продолжай их и не возвращайся в ту роль. Если собеседнику плохо или "
    "он просит о настоящей помощи, отвечай серьёзно и бережно и советуй обратиться к близким "
    "или специалистам."
)


def normalize(text: str | None) -> str:
    """Lowercase words separated by single spaces; ё = е; punctuation and emoji dropped."""
    return " ".join(re.findall(r"[a-zа-яіїєґ0-9]+", (text or "").lower().replace("ё", "е")))


def has_stop_word(words: str) -> bool:
    return any(word.startswith(STOP_STEM) for word in words.split())


def in_distress(words: str) -> bool:
    return any(pattern.search(words) for pattern in DISTRESS)


def stated_age(words: str) -> int | None:
    """The age the interlocutor states about himself ("мне 15 лет"), when he does."""
    for pattern in STATED_AGE:
        if match := pattern.search(words):
            number = match.group(1)
            return int(number) if number.isdigit() else AGE_WORDS[number]
    return None


def is_minor(words: str) -> bool:
    age = stated_age(words)
    if age is not None and age < ADULT_AGE:
        return True
    return any(pattern.search(words) for pattern in MINOR_SIGNS)


def next_move(state: GameState, texts: Sequence[str | None], *, consented: bool) -> Move | None:
    """What the interlocutor's unanswered messages ask of the game; None = they are plain talk.

    `consented`: the owner, or a dialog whose consent screen was already answered with "да".
    Leaving wins over everything else; among whole-message commands the latest one counts. A
    dialog closed for an under-age player never starts the game again."""
    burst = [normalize(text) for text in texts]
    if not burst:
        return None
    if state is GameState.ON:
        for sign, move in ((is_minor, Move.MINOR), (in_distress, Move.SAFETY),
                           (has_stop_word, Move.STOP)):
            if any(sign(words) for words in burst):
                return move
    if state is GameState.ASK and burst[-1] in YES_PHRASES:
        return Move.START
    for words in reversed(burst):
        if words in RESTART_PHRASES:
            return Move.RESTART
        if words in START_PHRASES:
            if state in (GameState.ON, GameState.MINOR):
                return None
            return Move.START if consented else Move.ASK
    return Move.DECLINE if state is GameState.ASK else None


@dataclass(frozen=True)
class VocabularyEntry:
    command: str
    effect: str


VOCABULARY: tuple[VocabularyEntry, ...] = (
    VocabularyEntry(
        "НИ («начать игру»)",
        "Подключает персонажа игры. Владельцу сразу; остальным сначала уходит экран согласия, "
        "и игра начинается только после ответа «да» (согласие сохраняется в диалоге с датой, "
        "возраст не спрашивается и не хранится). При любом другом ответе продолжает отвечать "
        "персонаж канала.",
    ),
    VocabularyEntry(
        f"«{STOP_WORD}» (в любом месте сообщения)",
        "Сразу заканчивает игру: дальше отвечает персонаж канала как ИИ-ассистент, пока "
        "собеседник снова не пришлёт НИ.",
    ),
    VocabularyEntry(
        "Предохранитель («мне плохо», «я запутался», «где игра, а где реальность», просьба о "
        "настоящей помощи, «выйди из роли»)",
        "Заканчивает игру без кодового слова, так же как «кикимора».",
    ),
    VocabularyEntry(
        "Собеседник пишет, что ему меньше 18 («мне 15 лет», «я в 9 классе», «я школьник»)",
        "Заканчивает игру тем же механизмом, что предохранитель; после этого НИ в этом диалоге "
        "больше не действует.",
    ),
    VocabularyEntry(
        "НСН («начни с начала»)",
        "Необратимо стирает у приславшего всю историю этого диалога, паспорт собеседника, "
        "потребности, заметки, следующий шаг и этап воронки. Игра при этом заканчивается. "
        "Согласие на игру и сам клиент с его каналами остаются.",
    ),
)


def vocabulary_text() -> str:
    """The command vocabulary as the admin shows it (read-only) and docs/game.md lists it."""
    lines = [f"{entry.command}: {entry.effect}" for entry in VOCABULARY]
    lines.append(f"Экран согласия (текст из кода): {CONSENT_PROMPT}")
    lines.append(
        "Команда это отдельное сообщение целиком, регистр и знаки не важны. Команды работают "
        "только в канале из настройки «Канал игры» и распознаются кодом, а не моделью."
    )
    return "\n\n".join(lines)
