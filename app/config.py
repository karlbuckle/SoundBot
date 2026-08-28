from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    discord_token: str
    audio_directories: tuple[Path, ...]
    web_api_token: str
    host: str
    port: int
    stats_database: Path

    @classmethod
    def from_env(cls) -> "Settings":
        raw_directories = os.getenv("AUDIO_DIRECTORIES", "/media/sounds")
        directories = tuple(
            Path(item.strip()).expanduser()
            for item in raw_directories.split(",")
            if item.strip()
        )
        return cls(
            discord_token=os.getenv("DISCORD_TOKEN", "").strip(),
            audio_directories=directories,
            web_api_token=os.getenv("WEB_API_TOKEN", "").strip(),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            stats_database=Path(os.getenv("STATS_DATABASE", "data/stats.db")).expanduser(),
        )
