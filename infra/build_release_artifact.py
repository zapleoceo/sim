"""Build a source-pinned SIM release tar from tracked backend files and CI-built UI.

This command runs only inside GitHub Actions after backend/PostgreSQL and frontend
checks succeeded. It never deploys or reads production credentials.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "release-output"
BACKEND_FILES = {
    "backend/.dockerignore",
    "backend/Dockerfile",
    "backend/alembic.ini",
    "backend/pyproject.toml",
    "backend/uv.lock",
}
MAX_FILES = 2000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def allowed_backend(path: str) -> bool:
    return path in BACKEND_FILES or path.startswith(("backend/sim/", "backend/migrations/"))


def safe_file(path: Path, name: str) -> bytes:
    parts = Path(name).parts
    if path.is_symlink() or not path.is_file() or not parts or ".." in parts:
        raise RuntimeError(f"unsafe release path: {name}")
    lowered = [part.lower() for part in parts]
    if any(
        part in {".git", ".ssh", "node_modules", "tests", "broker-project.json"}
        or part.startswith(".env")
        or part.endswith((".pem", ".key", ".p12", ".pfx"))
        for part in lowered
    ):
        raise RuntimeError(f"secret or test path in release: {name}")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES or any(
        marker in data
        for marker in (b"-----BEGIN PRIVATE KEY-----", b"-----BEGIN OPENSSH PRIVATE KEY-----")
    ):
        raise RuntimeError(f"oversized or private-key content: {name}")
    return data


def main() -> None:
    commit = git("rev-parse", "HEAD")
    expected = os.environ.get("EXPECTED_SOURCE_SHA", "")
    if not expected or commit != expected:
        raise RuntimeError("checkout SHA differs from requested CI source SHA")
    if git("status", "--porcelain", "--", "backend"):
        raise RuntimeError("tracked backend source changed after checkout")
    tree = git("rev-parse", "HEAD^{tree}")
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "backend"], cwd=ROOT
    ).decode().split("\0")
    names = sorted(name for name in tracked if name and allowed_backend(name))
    required = BACKEND_FILES - {"backend/.dockerignore"}
    if not required <= set(names):
        raise RuntimeError("required backend build inputs missing")
    browser = ROOT / "frontend/dist/sim/browser"
    if not (browser / "index.html").is_file():
        raise RuntimeError("CI frontend build missing")
    names.extend(
        path.relative_to(ROOT).as_posix()
        for path in sorted(browser.rglob("*"))
        if path.is_file() or path.is_symlink()
    )
    names = sorted(set(names))
    if len(names) > MAX_FILES:
        raise RuntimeError("release file count limit exceeded")

    OUT.mkdir(exist_ok=True)
    archive = OUT / f"sim-release-{commit}.tar"
    entries = []
    total = 0
    with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as tar:
        for name in names:
            if not (
                allowed_backend(name) or name.startswith("frontend/dist/sim/browser/")
            ):
                raise RuntimeError(f"path outside release roots: {name}")
            data = safe_file(ROOT / name, name)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise RuntimeError("release content limit exceeded")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
            entries.append(
                {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            )

    archive_bytes = archive.read_bytes()
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as tar:
        members = tar.getmembers()
        if [member.name for member in members] != names or not all(
            member.isfile() for member in members
        ):
            raise RuntimeError("archive membership verification failed")
        for member, entry in zip(members, entries, strict=True):
            extracted = tar.extractfile(member)
            if extracted is None or hashlib.sha256(extracted.read()).hexdigest() != entry["sha256"]:
                raise RuntimeError(f"archive content verification failed: {member.name}")

    manifest = {
        "source_commit": commit,
        "source_tree": tree,
        "archive": archive.name,
        "archive_sha256": archive_sha,
        "files": len(entries),
        "content_bytes": total,
        "backend_lock_sha256": hashlib.sha256((ROOT / "backend/uv.lock").read_bytes()).hexdigest(),
        "frontend_lock_sha256": hashlib.sha256(
            (ROOT / "frontend/package-lock.json").read_bytes()
        ).hexdigest(),
        "entries": entries,
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"SIM release artifact: commit={commit} tree={tree} files={len(entries)} "
        f"tar_sha256={archive_sha}"
    )


if __name__ == "__main__":
    main()
