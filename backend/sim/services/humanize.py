"""Messenger punctuation: what a person actually types on a phone keyboard.

Models love typographic dashes, guillemets and semicolons; people in a chat do not. This pure
function is applied to every reply that can reach a client (the generator's reply and the
guard's rewrite), so the style does not depend on the model obeying the prompt."""
from __future__ import annotations

import random
import re
import zlib

REPLACEMENTS = (
    ("—", "-"),   # em dash
    ("–", "-"),   # en dash
    ("‒", "-"),   # figure dash
    ("«", '"'),   # «
    ("»", '"'),   # »
    ("„", '"'),   # „
    ("“", '"'),   # “
    ("”", '"'),   # ”
    ("…", "..."), # …
    (";", ","),
)
SPACES = re.compile(r"[ \t]{2,}")
# Latin look-alikes a model sometimes slips into a Russian word ("стратегичeскому" with a Latin e):
# a detector flags them at once. Only letters touching Cyrillic letters are replaced.
LATIN_LOOKALIKES = "aceopxyACEHKMOPTXB"
HOMOGLYPHS = str.maketrans(LATIN_LOOKALIKES, "асеорхуАСЕНКМОРТХВ")
MIXED = re.compile(rf"(?<=[а-яёА-ЯЁ])[{LATIN_LOOKALIKES}]+|[{LATIN_LOOKALIKES}]+(?=[а-яёА-ЯЁ])")


def plain_punctuation(text: str) -> str:
    for typographic, typed in REPLACEMENTS:
        text = text.replace(typographic, typed)
    text = MIXED.sub(lambda m: m.group(0).translate(HOMOGLYPHS), text)
    return SPACES.sub(" ", text)


# ---- messenger touches: done by code, so the model never has to "act" sloppy ------------------
# Lowercase Cyrillic words of 5+ letters only: names (capitalised), links, numbers and prices stay
# untouched. Deterministic per text (seeded by its checksum): a retry produces the same message.
TYPO_WORD = re.compile(r"(?<![\w/@.:-])[а-яё]{5,}(?![\w/@.-]*[/@])")


def typo(word: str, rng: random.Random) -> str:
    """One natural slip inside a word: two letters swapped, one dropped or one doubled."""
    i = rng.randrange(1, len(word) - 2)
    kind = rng.randrange(3)
    if kind == 0 and word[i] != word[i + 1]:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if kind == 1:
        return word[:i] + word[i + 1:]
    return word[:i] + word[i] + word[i:]


COMMA = re.compile(r", (?=[а-яё])")
COMMA_SLIP_PERCENT = 40


def slip(text: str, rng: random.Random) -> str:
    """One natural slip: more often a missing comma (what people really skip), otherwise a letter
    swapped, dropped or doubled in one lowercase word."""
    commas = list(COMMA.finditer(text))
    if commas and rng.randrange(100) < COMMA_SLIP_PERCENT:
        m = rng.choice(commas)
        return text[:m.start()] + " " + text[m.end():]
    words = list(TYPO_WORD.finditer(text))
    if not words:
        return text
    m = rng.choice(words)
    return text[:m.start()] + typo(m.group(0), rng) + text[m.end():]


def messenger_touches(text: str, *, typo_percent: int, lowercase_percent: int) -> str:
    """What a person's thumbs do to a reply of 1-3 short messages (separated by a blank line): no
    full stop at the end of a message, sometimes a small first letter (`lowercase_percent` per
    message) and at most ONE slip in the whole reply (`typo_percent` per reply): a slip in every
    message is what a detector notices. Deterministic per text."""
    rng = random.Random(zlib.crc32(text.strip().encode()))  # noqa: S311 - style, not security
    out: list[str] = []
    for part in bubbles(text):
        if part.endswith(".") and not part.endswith(".."):
            part = part[:-1]
        first = part.split(maxsplit=1)[0] if part else ""
        if rng.randrange(100) < lowercase_percent and first[:1].isupper() and first[1:].islower():
            part = part[0].lower() + part[1:]
        out.append(part)
    if rng.randrange(100) < typo_percent:
        k = rng.randrange(len(out))
        out[k] = slip(out[k], rng)
    return "\n\n".join(out)


# ---- several short messages instead of one block ----------------------------------------------
# The model separates messages with a blank line; code sends them one by one with a typing pause.
BUBBLE_GAP = re.compile(r"\n[ \t]*\n")
MAX_BUBBLES = 3
# The pause before each next message of one reply: about a second per 28 characters, never
# shorter than TYPING_MIN_S (the status must be seen) and never longer than TYPING_MAX_S.
TYPING_CHARS_PER_S = 28.0
TYPING_MIN_S, TYPING_MAX_S = 1.0, 6.0


def bubbles(text: str) -> list[str]:
    """A reply split into at most MAX_BUBBLES messages (extra parts stay in the last one)."""
    parts = [p.strip() for p in BUBBLE_GAP.split(text) if p.strip()]
    if len(parts) > MAX_BUBBLES:
        parts = [*parts[:MAX_BUBBLES - 1], "\n".join(parts[MAX_BUBBLES - 1:])]
    return parts or [text.strip()]


def typing_pause_s(text: str) -> float:
    """The pause before a next message of one reply, by its length; «печатает…» is shown for it."""
    return min(TYPING_MAX_S, max(TYPING_MIN_S, len(text) / TYPING_CHARS_PER_S))
