"""The CI change gate: product code needs tests and docs in the same change."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "change_gate", Path(__file__).resolve().parents[1] / "scripts" / "change_gate.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def test_code_with_tests_and_docs_passes():
    r = gate.evaluate(["backend/sim/services/x.py", "backend/tests/test_x.py", "docs/api.md"], "feat: x")
    assert r.ok and r.code == ["backend/sim/services/x.py"]


def test_code_without_tests_or_docs_fails():
    r = gate.evaluate(["frontend/src/features/a/page.tsx"], "feat: a")
    assert not r.ok and r.missing_tests and r.missing_docs


def test_frontend_tests_and_readme_count():
    r = gate.evaluate(
        ["frontend/src/features/a/page.tsx", "frontend/src/features/a/page.test.tsx", "README.md"], "x"
    )
    assert r.ok


def test_test_files_alone_are_not_product_code():
    assert gate.evaluate(["frontend/src/features/a/page.test.tsx", "backend/tests/test_a.py"], "x").ok


def test_non_product_changes_pass():
    assert gate.evaluate([".github/workflows/ci.yml", "infra/deploy.sh"], "ci: x").ok


def test_opt_out_markers():
    changed = ["backend/sim/api/main.py"]
    assert gate.evaluate(changed, "style: rename\n\ntests-not-needed docs-not-needed").ok
    r = gate.evaluate(changed, "style: rename\n\nDocs-Not-Needed")
    assert r.missing_tests and not r.missing_docs
