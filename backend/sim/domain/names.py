"""Names as the «возможно, один человек» hint compares them (docs/leads.md §6): one spelling for
Cyrillic and Latin, transliteration variants and the usual short forms. Pure and best-effort:
it only feeds a suggestion the operator checks.

`Дмитрий`, `Dmitriy`, `Dmitry`, `Dmytro` and `Дима` become `dmitri`; `Zaporozhets` and
`Запорожец` become `zaporozec`."""
from __future__ import annotations

import re

CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e", "є": "e",
    "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh",
    "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}
# Latin spellings of one sound -> one letter, longest first.
SOUNDS = (
    ("shch", "s"), ("sch", "s"), ("kh", "h"), ("zh", "z"), ("ts", "c"), ("tz", "c"),
    ("ch", "c"), ("sh", "s"), ("ph", "f"), ("x", "ks"), ("w", "v"), ("q", "k"),
    ("yu", "u"), ("ju", "u"), ("ya", "a"), ("ja", "a"), ("ye", "e"), ("je", "e"),
    ("iy", "i"), ("ij", "i"), ("yi", "i"), ("y", "i"), ("j", "i"),
)
# Short and national forms of a first name -> one key (after `spelling`).
SHORT_FORMS: dict[str, tuple[str, ...]] = {
    "dmitri": ("dima", "dimka", "dimon", "dmitro", "dimitri", "mita"),
    "aleksandr": ("aleksander", "oleksandr", "sasa", "sana", "sanek", "aleks"),
    "aleksei": ("alesa", "lesa", "oleksi"),
    "andrei": ("andri", "andrev"),
    "anastasia": ("nasta",),
    "ekaterina": ("kata", "katerina"),
    "elena": ("lena", "olena", "alena", "alona"),
    "ivan": ("vana", "ivanko"),
    "mihail": ("misa", "mihailo", "maikl", "micael"),
    "nikolai": ("kola", "mikola"),
    "natalia": ("nata", "natasa"),
    "olga": ("ola", "olha"),
    "sergei": ("sera", "sereza", "serhi", "sergi"),
    "tatiana": ("tatana", "tana"),
    "vladimir": ("vova", "voloda", "volodimir"),
    "evgeni": ("zena", "evgen"),
}
SHORT_TO_FULL = {short: full for full, shorts in SHORT_FORMS.items() for short in shorts}
WORD_RE = re.compile(r"[^\W\d_]{2,}")


def spelling(word: str) -> str:
    """One Latin spelling of a word, whatever alphabet and transliteration it came in."""
    latin = "".join(CYRILLIC.get(ch, ch) for ch in word.lower())
    for sound, letter in SOUNDS:
        latin = latin.replace(sound, letter)
    return re.sub(r"(.)\1+", r"\1", latin)


def name_word(word: str) -> str:
    """A name word compared: its spelling, a short form as its full name."""
    spelled = spelling(word)
    return SHORT_TO_FULL.get(spelled, spelled)


def name_words(raw: str | None) -> list[str]:
    return [name_word(w) for w in WORD_RE.findall(raw or "")]
