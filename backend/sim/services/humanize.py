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


def plain_punctuation(text: str) -> str:
    for typographic, typed in REPLACEMENTS:
        text = text.replace(typographic, typed)
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


def messenger_touches(text: str, *, typo_percent: int, lowercase_percent: int) -> str:
    """What a person's thumbs do to a message: no full stop at the very end, sometimes a small
    first letter, sometimes one typo. Percentages are per message (owner settings)."""
    text = text.strip()
    if not text:
        return text
    rng = random.Random(zlib.crc32(text.encode()))  # noqa: S311 - style, not security
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    first = text.split(maxsplit=1)[0]
    if rng.randrange(100) < lowercase_percent and first[:1].isupper() and first[1:].islower():
        text = text[0].lower() + text[1:]
    if rng.randrange(100) < typo_percent:
        words = list(TYPO_WORD.finditer(text))
        if words:
            m = rng.choice(words)
            text = text[:m.start()] + typo(m.group(0), rng) + text[m.end():]
    return text
