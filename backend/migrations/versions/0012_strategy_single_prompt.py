"""One text per strategy: goal, per-stage hints and rules are appended to `prompt` and cleared.

No content is lost: everything moves into the prompt text the owner edits. The columns stay
(empty) so older code keeps working. Downgrade cannot split the text back and is a no-op."""
from collections.abc import Sequence
import json

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE_LABELS = {
    "first_contact": "Первый контакт",
    "engaged": "Вовлечён",
    "needs_identified": "Потребность ясна",
    "interested": "Заинтересован",
    "handed_to_partner": "Передан партнёру",
}


def as_obj(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def merged_prompt(goal: str, prompt: str, playbook: dict, rules: list) -> str:
    parts = []
    if (goal or "").strip():
        parts.append(f"Цель: {goal.strip()}")
    if (prompt or "").strip():
        parts.append(prompt.strip())
    hints = [(k, v) for k, v in (playbook or {}).items() if str(v or "").strip()]
    if hints:
        order = list(STAGE_LABELS)
        hints.sort(key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99, kv[0]))
        parts.append("По этапам:\n" + "\n".join(f"- {STAGE_LABELS.get(k, k)}: {str(v).strip()}" for k, v in hints))
    rule_lines = [str(r).strip() for r in (rules or []) if str(r).strip()]
    if rule_lines:
        parts.append("Ориентиры:\n" + "\n".join(f"- {r}" for r in rule_lines))
    return "\n\n".join(parts)


def upgrade() -> None:
    conn = op.get_bind()
    # PostgreSQL needs an explicit cast for a text parameter into a JSON column; SQLite stores text.
    pb, rl = ("CAST(:pb AS JSON)", "CAST(:rules AS JSON)") if conn.dialect.name == "postgresql" else (":pb", ":rules")
    rows = conn.execute(sa.text("SELECT id, goal, prompt, stage_playbook, rules FROM strategy")).fetchall()
    for sid, goal, prompt, playbook, rules in rows:
        playbook, rules = as_obj(playbook, {}), as_obj(rules, [])
        if not (goal or "").strip() and not playbook and not rules:
            continue
        conn.execute(
            sa.text(
                f"UPDATE strategy SET prompt = :prompt, goal = '', stage_playbook = {pb}, rules = {rl} "
                "WHERE id = :id"
            ),
            {"prompt": merged_prompt(goal, prompt, playbook, rules), "pb": "{}", "rules": "[]", "id": sid},
        )


def downgrade() -> None:
    """The merged text cannot be split back; the schema is unchanged, so nothing to undo."""
