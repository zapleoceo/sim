"""infra/docker-compose.yml: WhatsApp's Evolution API is off by default (compose profile
`whatsapp`, docs/deploy.md «WhatsApp»), so a normal deploy starts nothing new, and nothing in
it may break the deploy when the profile is off."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"


def services() -> dict[str, dict]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_evolution_runs_only_with_the_whatsapp_profile() -> None:
    found = services()
    assert found["evolution"]["profiles"] == ["whatsapp"]
    assert found["evolution-init"]["profiles"] == ["whatsapp"]
    assert found["evolution"]["image"] == "evoapicloud/evolution-api:v2.3.7"
    assert "ports" not in found["evolution"]                # only inside the compose network
    # The deploy smoke expects these, always: none of them sits behind a profile.
    for name in ("postgres", "migrate", "api", "worker", "telegram"):
        assert "profiles" not in found[name], name


def test_no_required_variable_in_the_whatsapp_services() -> None:
    # Compose interpolates every service, enabled or not: `${X:?…}` there would fail the
    # normal deploy of a server without EVOLUTION_API_KEY.
    text = COMPOSE.read_text(encoding="utf-8")
    whatsapp = text[text.index("evolution-init:"):text.index("\nvolumes:")]
    assert not re.search(r"\$\{[A-Z_]+:\?", whatsapp)
