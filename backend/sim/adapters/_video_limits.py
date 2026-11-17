"""Linux child bootstrap; invoked with Python -I, never imports application settings.

Apply hard limits before exec, so they cover codec probing/decoding, not metadata
claims. This is resource containment, not a filesystem/network sandbox.
"""
import os
import resource
import sys

MEMORY_BYTES = 512 * 1024 * 1024
CPU_SECONDS = 6
CLEAN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1",
             "OPENBLAS_NUM_THREADS": "1"}


def launch(args: list[str]) -> None:
    if not args or args[0] not in ("ffprobe", "ffmpeg"):
        raise ValueError("unsupported video executable")
    for kind, bounds in (
        (resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES)),
        (resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS)),
        (resource.RLIMIT_CORE, (0, 0)),
        (resource.RLIMIT_FSIZE, (0, 0)),
        (resource.RLIMIT_NOFILE, (32, 32)),
    ):
        resource.setrlimit(kind, bounds)
    os.execve(f"/usr/bin/{args[0]}", args, CLEAN_ENV)  # noqa: S606


if __name__ == "__main__":
    launch(sys.argv[1:])
