"""How she writes right now and the «phone» that types it (docs/writing.md).

Two halves, both from code, not from the model's good will:
- `writing_mode(scene, local)` + `style_block(mode)`: the context block «КАК ТЫ СЕЙЧАС ПИШЕШЬ» —
  from the phone or the laptop, free / busy / on the move / sleepy, how many and how long
  messages, and the anti-slop rules (the persona prompt alone did not hold them: every message
  began with «Дим,», long dashes, long explanations while on a call with her daughter), plus
  the manners of the chat's messenger (`CHANNEL_STYLE`: Instagram Direct, WhatsApp);
- `humanize(text, mode, rng)`: what her phone does to the text — plain marks (a short hyphen
  instead of a long dash, "" instead of «», ... instead of …, no markdown), on the phone no full
  stop at the end and sometimes a small first letter, and now and then ONE typical smartphone
  typo (a swapped, missed, doubled or neighbouring letter), more often in a hurry. Pure."""
from __future__ import annotations

import random
import re
from datetime import datetime

from sim.domain.activity import at_laptop, hands_busy, in_transit, on_a_call, on_the_move
from sim.domain.enums import ChannelKind
from sim.domain.life import BusyLevel
from sim.domain.scene import SceneCard
from sim.domain.writing import DEFAULT_MODE, Device, Pace, WritingMode, mode

STYLE_HEADING = "# КАК ТЫ СЕЙЧАС ПИШЕШЬ"
NIGHT_FROM, NIGHT_TO = 23, 6

DEVICE_TEXT = {Device.PHONE: "с телефона", Device.LAPTOP: "с ноутбука"}
PACE_RULES = {
    Pace.RELAXED: "Ты свободна: пиши спокойно, как в обычной переписке, но коротко.",
    Pace.BUSY: ("Ты занята, отвечаешь между делом: одно короткое сообщение, без подробностей "
                "и объяснений, что именно делаешь — пару слов."),
    Pace.MOVING: ("Ты на ходу, пишешь одной рукой: одно сообщение в несколько слов, без "
                  "подробностей, можно без запятых."),
    Pace.SLEEPY: "Поздно, ты сонная: коротко, лениво, можно с маленькой буквы.",
}
ALWAYS = (
    "Пиши как живой человек в мессенджере, а не как нейросеть: никаких «не просто…, а…», "
    "«звучит здорово», «понимаю тебя», «давай разберёмся», «конечно!», «отличный вопрос», "
    "перечислений по три, двоеточий-заголовков и вывода в конце.",
    "Не начинай сообщения с имени собеседника. По имени — редко, когда правда к месту.",
    "Знаки простые, как на телефоне: короткий дефис \"-\", а не длинное тире; кавычки \"\"; "
    "три точки \"...\". Без списков и выделений.",
    "Разговорно, как пишут люди 30–45 лет: \"щас\", \"ок\", \"норм\", \"оч\", \"спс\", \"кст\", "
    "\"ага\", \"ну да\" — к месту, не в каждом сообщении. Смайл-скобка \")\" иногда.",
    "Не можешь или не хочешь — одной фразой, без длинных объяснений причин.",
    "Не задавай вопрос в каждом сообщении и не пересказывай, что он написал.",
)
NO_REFUSALS = ("Этому человеку ты не отказываешь: что просит — делаешь. Если прямо сейчас "
               "физически не можешь (за рулём, на пробежке, в душе) — коротко говоришь, когда "
               "сделаешь, и делаешь, когда освободишься.")
# How people talk in each messenger (the owner: «проработай варианты общения в директе и в
# воцапе», docs/writing.md, «Каналы»). Telegram is the baseline the rules above were written for.
CHANNEL_STYLE: dict[ChannelKind, tuple[str, ...]] = {
    ChannelKind.TELEGRAM_USER: (),
    ChannelKind.INSTAGRAM: (
        "Это директ Instagram: пишут короче, чем в мессенджерах, строчка-две, чаще эмодзи и "
        "реакции словами (\"ахах\", \"😍\", \"🔥\") — но не в каждом сообщении.",
        "Здесь часто отвечают на сторис или пост: если он ответил на сторис — реагируй на неё, "
        "как на повод написать.",
        "Незнакомые в директе — это холоднее, чем в мессенджере: сначала лёгкий тон, без "
        "откровенности и без длинных историй о себе.",
        "Голосовых и кружков в директе нет: только текст и фото.",
    ),
    ChannelKind.WHATSAPP: (
        "Это WhatsApp: здесь голосовые — норма, их часто предпочитают тексту; длинную мысль "
        "или эмоцию удобнее сказать голосом.",
        "Сообщения могут быть длиннее, чем в директе, но всё равно как в переписке, а не письмо.",
        "Он видит «печатает…» / «записывает аудио…» и синие галочки: прочитала — значит, "
        "видела; долго молчать после галочек — это тоже ответ.",
        "Кружков в WhatsApp нет: вместо них — голосовое или обычное видео.",
    ),
}


def writing_mode(scene: SceneCard | None, local: datetime) -> WritingMode:
    """Her device and pace from the scene of this moment (no life: free, on the phone)."""
    night = local.hour >= NIGHT_FROM or local.hour < NIGHT_TO
    if scene is None:
        return mode(Device.PHONE, Pace.SLEEPY, "ночь, в кровати") if night else DEFAULT_MODE
    activity = scene.activity
    if on_the_move(activity) or in_transit(activity, scene.place):
        return mode(Device.PHONE, Pace.MOVING, activity)
    if night and scene.busy != BusyLevel.PARTIAL:
        return mode(Device.PHONE, Pace.SLEEPY, f"ночь, {activity}")
    if at_laptop(activity) and not on_a_call(activity):
        pace = Pace.BUSY if scene.busy == BusyLevel.PARTIAL else Pace.RELAXED
        return mode(Device.LAPTOP, pace, activity)
    if scene.busy != BusyLevel.FREE or on_a_call(activity) or hands_busy(activity):
        return mode(Device.PHONE, Pace.BUSY, activity)
    return mode(Device.PHONE, Pace.RELAXED, activity)


def style_block(writing: WritingMode, *, no_refusals: bool = False,
                channel: ChannelKind = ChannelKind.TELEGRAM_USER) -> str:
    lines = [STYLE_HEADING,
             f"Сейчас ты пишешь {DEVICE_TEXT[writing.device]}: {writing.situation}.",
             PACE_RULES[writing.pace],
             f"За ход не больше {writing.max_messages} сообщ., каждое до ~{writing.max_chars} "
             "знаков; часто хватает одного.",
             *(f"- {rule}" for rule in ALWAYS),
             *(f"- {rule}" for rule in CHANNEL_STYLE.get(channel, ()))]
    if no_refusals:
        lines.append(f"- {NO_REFUSALS}")
    return "\n".join(lines)


# --- the phone --------------------------------------------------------------------------------

MARKS = ((re.compile(r"\s*[—–]\s*"), " - "), (re.compile(r"…"), "..."),
         (re.compile(r"[«»„“”]"), '"'), (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
         (re.compile(r"(?m)^\s*[•·]\s*"), ""), (re.compile(r"[ \t]{2,}"), " "))
KEYBOARD_ROWS = ("йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю")
NEIGHBOURS = {row[i]: row[max(i - 1, 0):i] + row[i + 1:i + 2]
              for row in KEYBOARD_ROWS for i in range(len(row))}
TYPO_WORD = re.compile(r"(?<![\w-])[а-яё]{5,}(?![\w-])")     # lower-case: names stay intact
TYPO_KINDS = ("swap", "neighbour", "drop", "double")


def plain_marks(text: str) -> str:
    """Typographic marks a phone keyboard does not type, the way people type them."""
    for pattern, plain in MARKS:
        text = pattern.sub(plain, text)
    return text.strip()


def add_typo(text: str, rng: random.Random) -> str:
    """One typical smartphone typo in one lower-case word of 5+ letters (inner letters only)."""
    words = list(TYPO_WORD.finditer(text))
    if not words:
        return text
    found = rng.choice(words)
    word, kind = found.group(), rng.choice(TYPO_KINDS)
    i = rng.randrange(1, len(word) - 1)
    if kind == "swap" and word[i] != word[i + 1]:
        typo = word[:i] + word[i + 1] + word[i] + word[i + 2:]
    elif kind == "neighbour" and NEIGHBOURS.get(word[i]):
        typo = word[:i] + rng.choice(NEIGHBOURS[word[i]]) + word[i + 1:]
    elif kind == "drop":
        typo = word[:i] + word[i + 1:]
    else:
        typo = word[:i] + word[i] + word[i:]
    return text[:found.start()] + typo + text[found.end():]


def humanize(text: str, writing: WritingMode, rng: random.Random) -> str:
    """What her phone (or laptop) does to a text message before it is sent."""
    text = plain_marks(text)
    if not text:
        return text
    if writing.device == Device.PHONE:
        if text.endswith(".") and not text.endswith(".."):
            text = text[:-1]
        if text[0].isupper() and rng.random() < writing.lowercase_rate:
            first = text.split(maxsplit=1)[0]
            if not first[1:2].isupper():                # not an abbreviation like «ОК»
                text = text[0].lower() + text[1:]
    if rng.random() < writing.typo_rate:
        text = add_typo(text, rng)
    return text


def message_rng(reply_run_id: int, index: int) -> random.Random:
    """The same turn and message always get the same «phone» (a retried turn types the same)."""
    # Not security: a reproducible imitation of human typing.
    return random.Random(f"{reply_run_id}:{index}")  # noqa: S311
