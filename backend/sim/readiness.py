"""Shared container/deploy readiness gate; nonzero on transport, HTTP or payload failure."""
from __future__ import annotations

import json
from urllib.request import urlopen

READINESS_URL = "http://127.0.0.1:8000/api/ready"


def check_ready() -> bool:
    try:
        with urlopen(READINESS_URL, timeout=4) as response:  # noqa: S310 (fixed loopback URL)
            if response.status != 200:
                return False
            payload = json.load(response)
        return isinstance(payload, dict) and payload.get("ok") is True and payload.get("db") is True
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if check_ready() else 1)
