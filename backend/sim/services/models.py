"""Broker model catalog -> the admin's model picker."""
from __future__ import annotations

from typing import Any

from sim.adapters.llm.broker import is_subscription_model


def catalog_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        model_id = str(row.get("id", ""))
        if not model_id:
            continue
        items.append(
            {
                "id": model_id,
                "name": str(row.get("name") or model_id),
                "subscription": row.get("billing_type") == "subscription"
                or is_subscription_model(model_id),
                "available": bool(row.get("available", True)),
            }
        )
    return sorted(items, key=lambda i: (not i["subscription"], i["id"]))
