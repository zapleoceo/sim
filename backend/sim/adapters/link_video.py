"""LinkVideoPort over yt-dlp: downloads a short clip from TikTok, Instagram or YouTube
(docs/reply.md, «Ролик по ссылке»). Imported lazily: only a link loads yt-dlp. Runs in a
thread (yt-dlp is blocking), through the proxy when one is set (TikTok and Instagram refuse
many server addresses), at most `MAX_CLIP_S` long and `MAX_CLIP_BYTES` big, at most 720p."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from sim.domain.links import MAX_CLIP_BYTES, MAX_CLIP_S
from sim.ports import FetchedVideo, PortError

FORMAT = "best[ext=mp4][height<=720]/best[height<=720]/best"
SOCKET_TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 180


def ydl_options(folder: Path, proxy: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "outtmpl": str(folder / "clip.%(ext)s"), "format": FORMAT, "quiet": True,
        "no_warnings": True, "noplaylist": True, "max_filesize": MAX_CLIP_BYTES,
        "socket_timeout": SOCKET_TIMEOUT_S, "noprogress": True,
    }
    if proxy:
        options["proxy"] = proxy
    return options


def _download(url: str, proxy: str) -> FetchedVideo:  # pragma: no cover - network
    import yt_dlp

    with tempfile.TemporaryDirectory(prefix="sim-link-") as tmp:
        folder = Path(tmp)
        options = ydl_options(folder, proxy)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False) or {}
                duration = float(info.get("duration") or 0)
                if duration > MAX_CLIP_S:
                    raise PortError(f"ролик длинный ({int(duration)} с)")
                ydl.process_ie_result(info, download=True)
        except yt_dlp.utils.DownloadError as exc:
            raise PortError(f"не скачался: {str(exc)[:200]}") from exc
        files = sorted(folder.glob("clip.*"))
        if not files:
            raise PortError("не скачался: нет файла")
        data = files[0].read_bytes()
    ext = files[0].suffix.lower().lstrip(".")
    return FetchedVideo(data=data, mime="video/webm" if ext == "webm" else "video/mp4",
                        title=str(info.get("title") or "")[:200], duration_s=duration or None)


class YtDlpLinkVideo:
    def __init__(self, proxy: str = "") -> None:
        self.proxy = proxy

    async def fetch(self, url: str) -> FetchedVideo:
        try:
            return await asyncio.wait_for(asyncio.to_thread(_download, url, self.proxy),
                                          DOWNLOAD_TIMEOUT_S)
        except TimeoutError as exc:
            raise PortError("не скачался за 3 минуты") from exc
