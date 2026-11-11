"""Messenger punctuation: what a person actually types on a phone keyboard.

Models love typographic dashes, guillemets and semicolons; people in a chat do not. This pure
function is applied to every reply that can reach a client (the generator's reply and the
guard's rewrite), so the style does not depend on the model obeying the prompt."""
from __future__ import annotations

import re

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
