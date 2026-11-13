from __future__ import annotations

import pytest

from sim.services.humanize import plain_punctuation


@pytest.mark.parametrize(
    ("raw", "typed"),
    [
        ("да — конечно", "да - конечно"),
        ("2020–2024", "2020-2024"),
        ("«привет»", '"привет"'),
        ("„ок“ и “ок”", '"ок" и "ок"'),
        ("ну…", "ну..."),
        ("раз; два", "раз, два"),
        ("а   б", "а б"),
        ("обычный текст, без изменений!", "обычный текст, без изменений!"),
        ("https://rcipm.com/?ref=ab-12", "https://rcipm.com/?ref=ab-12"),
        ("стратегичeскому", "стратегическому"),       # Latin e inside a Russian word
        ("pаз и ок, OK", "раз и ок, OK"),              # Latin p at the start; Latin words stay
        ("RCIPM про мышление", "RCIPM про мышление"),
    ],
)
def test_plain_punctuation(raw: str, typed: str) -> None:
    assert plain_punctuation(raw) == typed


def test_a_reply_of_several_messages_gets_at_most_one_slip():
    from sim.services.humanize import messenger_touches

    reply = "Ой, привет.\n\nСегодня бегала вдоль реки, погода отличная.\n\nА у вас как дела, всё хорошо?"
    clean = ["ой, привет", "сегодня бегала вдоль реки, погода отличная", "а у вас как дела, всё хорошо?"]
    for n in range(40):  # different texts -> different seeds
        text = reply + " ок" * n
        out = messenger_touches(text, typo_percent=100, lowercase_percent=100).split("\n\n")
        expected = [*clean[:2], clean[2] + " ок" * n]
        assert len(out) == 3
        assert sum(o != e for o, e in zip(out, expected, strict=True)) <= 1
        assert not out[0].endswith(".") and not out[1].endswith(".")


def test_a_slip_is_sometimes_a_missing_comma():
    from sim.services.humanize import messenger_touches

    outs = {messenger_touches(f"слушай, а ты где {n}", typo_percent=100, lowercase_percent=0)
            for n in range(30)}
    assert any(o.startswith("слушай а ты где") for o in outs)


def test_no_slip_at_zero_percent():
    from sim.services.humanize import messenger_touches

    assert messenger_touches("Привет, как ты?\n\nЯ дома.", typo_percent=0, lowercase_percent=0) == \
        "Привет, как ты?\n\nЯ дома"
