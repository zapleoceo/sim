"""«Возможно, один человек» (docs/leads.md): which clients look like one person, and why. Pure:
the service (sim/services/client_match.py) loads what each client is known by and said; this
module compares. It only suggests: the operator decides and merges by hand.

Signals: the same phone (the profile phone, the number of a WhatsApp identity), the same
username or two usernames with one stem (`zapleosoft` / `zapleo_ceo`), the same full name (the
profile or the passport, two words or more, in any order, any alphabet and spelling:
sim.domain.names), and one client naming the other's @username or phone in a message or in the
passport. A shared first name alone («Дима» / «Dmitriy») is only told alongside another reason.
A pair that can not be merged (both have an identity in one messenger) is never suggested."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from sim.domain.client_merge import shared_messengers
from sim.domain.enums import ChannelKind
from sim.domain.names import name_words

PHONE_TAIL = 9                  # +380 99 481 18 89 and 099 481 18 89 are the same number
HANDLE_MIN, HANDLE_MAX = 3, 32
NAME_MIN_WORDS = 2              # one first name is too common to suggest anything
HANDLE_STEM = 6                 # letters two usernames share: zapleosoft / zapleo_ceo
HANDLE_RE = re.compile(
    rf"(?:@|t\.me/|instagram\.com/)([A-Za-z0-9_.]{{{HANDLE_MIN},{HANDLE_MAX}}})", re.IGNORECASE)
PHONE_RE = re.compile(r"\+?\d[\d\s()\-]{7,}\d")
WHATSAPP_PHONE_SERVERS = frozenset({"", "s.whatsapp.net", "c.us"})


class ReasonKind(StrEnum):
    PHONE = "phone"
    USERNAME = "username"
    NAME = "name"
    MENTION = "mention"


REASON_ORDER = (ReasonKind.PHONE, ReasonKind.USERNAME, ReasonKind.MENTION, ReasonKind.NAME)


@dataclass(frozen=True)
class Reason:
    kind: ReasonKind
    text: str                   # Russian, for the operator


def phone_key(raw: str | None) -> str | None:
    """The comparable tail of a phone number; None when it is too short to be one."""
    digits = re.sub(r"\D", "", raw or "")
    return digits[-PHONE_TAIL:] if len(digits) >= PHONE_TAIL else None


def whatsapp_phone(external_id: str) -> str | None:
    """The phone of a WhatsApp jid (`380994811889@s.whatsapp.net`); a `@lid` id is not one."""
    local, _, server = external_id.partition("@")
    return local if server in WHATSAPP_PHONE_SERVERS and local.isdigit() else None


def handle_key(raw: str | None) -> str | None:
    handle = (raw or "").strip().removeprefix("@").rstrip(".").lower()
    return handle if HANDLE_MIN <= len(handle) <= HANDLE_MAX else None


def name_key(raw: str | None) -> str | None:
    """A full name in any word order, case, alphabet and spelling (`Dmitry Zaporozhets` =
    `Запорожец Дмитрий`); None for one word."""
    words = set(name_words(raw))
    return " ".join(sorted(words)) if len(words) >= NAME_MIN_WORDS else None


def handle_stem(handle: str) -> str | None:
    """The first letters of a username without digits and separators (`zapleo_ceo` -> `zapleo`);
    None when it is too short to mean anything."""
    letters = re.sub(r"[^a-z]", "", handle)
    return letters[:HANDLE_STEM] if len(letters) >= HANDLE_STEM else None


def said_handles(text: str) -> set[str]:
    return {key for match in HANDLE_RE.findall(text) if (key := handle_key(match))}


def said_phones(text: str) -> set[str]:
    return {key for match in PHONE_RE.findall(text) if (key := phone_key(match))}


@dataclass(frozen=True)
class Profile:
    """What one client is known by and what they said about themselves (normalized keys)."""

    client_id: int
    messengers: frozenset[ChannelKind] = frozenset()
    phones: frozenset[str] = frozenset()
    handles: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()
    said_handles: frozenset[str] = frozenset()
    said_phones: frozenset[str] = frozenset()
    words: frozenset[str] = frozenset()     # every name word, compared (`dmitri`)
    shown: str = ""                         # the name the operator sees in the reason


def build_profile(client_id: int, *, messengers: Iterable[ChannelKind | str],
                  phones: Iterable[str | None], handles: Iterable[str | None],
                  names: Iterable[str | None], texts: Iterable[str]) -> Profile:
    said_h: set[str] = set()
    said_p: set[str] = set()
    for text in texts:
        said_h |= said_handles(text)
        said_p |= said_phones(text)
    own_handles = {k for h in handles if (k := handle_key(h))}
    own_phones = {k for p in phones if (k := phone_key(p))}
    shown = [n.strip() for n in names if n and n.strip()]
    return Profile(
        client_id=client_id, messengers=frozenset(ChannelKind(m) for m in messengers),
        phones=frozenset(own_phones), handles=frozenset(own_handles),
        names=frozenset(k for n in shown if (k := name_key(n))),
        said_handles=frozenset(said_h - own_handles), said_phones=frozenset(said_p - own_phones),
        words=frozenset(w for n in shown for w in name_words(n)), shown=shown[0] if shown else "")


def reasons(a: Profile, b: Profile) -> list[Reason]:
    """Why `a` and `b` may be one person, strongest first (empty = no reason)."""
    found = {Reason(ReasonKind.PHONE, f"один телефон …{p}") for p in a.phones & b.phones}
    found |= {Reason(ReasonKind.USERNAME, f"один username @{h}") for h in a.handles & b.handles}
    found |= {Reason(ReasonKind.USERNAME, f"похожие username @{ha} и @{hb}")
              for ha in sorted(a.handles) for hb in sorted(b.handles)
              if ha != hb and (stem := handle_stem(ha)) and stem == handle_stem(hb)}
    if a.names & b.names:
        found.add(Reason(ReasonKind.NAME, f"одно имя: «{a.shown}» и «{b.shown}»"))
    for one, other in ((a, b), (b, a)):
        found |= {Reason(ReasonKind.MENTION, f"в переписке назван @{h}")
                  for h in one.said_handles & other.handles}
        found |= {Reason(ReasonKind.MENTION, f"в переписке назван телефон …{p}")
                  for p in one.said_phones & other.phones}
    if found and not a.names & b.names and a.words & b.words:
        found.add(Reason(ReasonKind.NAME, f"похожие имена: «{a.shown}» и «{b.shown}»"))
    return sorted(found, key=lambda r: (REASON_ORDER.index(r.kind), r.text))


@dataclass(frozen=True)
class Suggestion:
    client_id: int              # the smaller id of the pair
    other_id: int
    reasons: tuple[Reason, ...]


def pair(a: int, b: int) -> tuple[int, int]:
    """A pair of clients in its stored order (`client_distinct`: smaller id first)."""
    return (a, b) if a < b else (b, a)


def _buckets(profiles: Iterable[Profile]) -> dict[str, set[int]]:
    """Clients by every key that can link them: what they are known by and what they said."""
    buckets: dict[str, set[int]] = defaultdict(set)
    for p in profiles:
        for key in p.phones | p.said_phones:
            buckets[f"phone:{key}"].add(p.client_id)
        for key in p.handles | p.said_handles:
            buckets[f"handle:{key}"].add(p.client_id)
        for key in p.handles:
            if stem := handle_stem(key):
                buckets[f"stem:{stem}"].add(p.client_id)
        for key in p.names:
            buckets[f"name:{key}"].add(p.client_id)
    return buckets


def suggest(profiles: Mapping[int, Profile],
            distinct: Iterable[tuple[int, int]] = ()) -> list[Suggestion]:
    """Every pair that may be one person, can be merged and was not marked «разные люди»;
    the pairs with more reasons first."""
    skip = {pair(a, b) for a, b in distinct}
    candidates = {pair(a, b) for ids in _buckets(profiles.values()).values()
                  for a in ids for b in ids if a != b}
    found = []
    for a, b in sorted(candidates - skip):
        first, second = profiles[a], profiles[b]
        if shared_messengers(first.messengers, second.messengers):
            continue
        if why := reasons(first, second):
            found.append(Suggestion(a, b, tuple(why)))
    return sorted(found, key=lambda s: (-len(s.reasons), REASON_ORDER.index(s.reasons[0].kind),
                                        s.client_id, s.other_id))
