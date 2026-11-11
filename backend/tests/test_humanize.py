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
    ],
)
def test_plain_punctuation(raw: str, typed: str) -> None:
    assert plain_punctuation(raw) == typed
