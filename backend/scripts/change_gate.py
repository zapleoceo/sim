#!/usr/bin/python3
"""CI gate: a change to product code must come with tests and docs.

Usage (CI): python scripts/change_gate.py <base_sha> <head_sha>   (run from backend/)

Rules, for the files changed between base and head:
- product code changed (backend/sim/**, frontend/src/** except tests) -> a test file must change
  (backend/tests/**, frontend/src/**/*.test.ts(x), frontend/src/test/**);
- product code changed -> docs must change (docs/** or README.md).
Opt-out for a purely cosmetic change: "tests-not-needed" / "docs-not-needed" in a commit message
of the range; the reviewer sees it in the PR."""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

CODE = re.compile(r"^(backend/sim/|frontend/src/)")
TEST = re.compile(r"^(backend/tests/|frontend/src/test/)|\.test\.(ts|tsx)$")
DOCS = re.compile(r"^(docs/|README\.md$)")
TESTS_OPT_OUT = "tests-not-needed"
DOCS_OPT_OUT = "docs-not-needed"


@dataclass(frozen=True)
class GateResult:
    code: list[str]
    missing_tests: bool
    missing_docs: bool

    @property
    def ok(self) -> bool:
        return not (self.missing_tests or self.missing_docs)


def evaluate(changed: list[str], messages: str) -> GateResult:
    code = [f for f in changed if CODE.match(f) and not TEST.search(f)]
    tests = [f for f in changed if TEST.search(f)]
    docs = [f for f in changed if DOCS.match(f)]
    text = messages.lower()
    return GateResult(
        code=code,
        missing_tests=bool(code) and not tests and TESTS_OPT_OUT not in text,
        missing_docs=bool(code) and not docs and DOCS_OPT_OUT not in text,
    )


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def main(base: str, head: str) -> int:
    changed = [line for line in git("diff", "--name-only", base, head).splitlines() if line]
    result = evaluate(changed, git("log", "--format=%B", f"{base}..{head}"))
    if result.missing_tests:
        print(f"::error::Product code changed without tests: {', '.join(result.code[:10])}")
        print(f"Add or update tests, or put '{TESTS_OPT_OUT}' in a commit message for a cosmetic change.")
    if result.missing_docs:
        print(f"::error::Product code changed without docs: {', '.join(result.code[:10])}")
        print(f"Update docs/ or README.md, or put '{DOCS_OPT_OUT}' in a commit message.")
    if result.ok:
        print(f"change gate passed ({len(result.code)} code files)")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
