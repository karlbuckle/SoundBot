from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import yt_dlp


ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


@dataclass(frozen=True)
class YouTubeTrack:
    title: str
    stream_url: str
    webpage_url: str
    duration_seconds: float | None


def validate_youtube_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_YOUTUBE_HOSTS:
        raise ValueError("Please provide a valid YouTube URL.")
    return url.strip()


def _extract(url: str) -> YouTubeTrack:
    options = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": False,
        "js_runtimes": {"node": {}},
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return YouTubeTrack(
        title=info.get("title") or "YouTube video",
        stream_url=info["url"],
        webpage_url=info.get("webpage_url") or url,
        duration_seconds=float(info["duration"]) if info.get("duration") else None,
    )


async def extract_youtube_track(url: str) -> YouTubeTrack:
    return await asyncio.to_thread(_extract, validate_youtube_url(url))
