from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


MAX_VOLUME = 3.0


@dataclass(frozen=True)
class PlaybackDefaults:
    volume: float = 1.0
    normalize: bool = False

    def public_dict(self) -> dict[str, float | bool]:
        return asdict(self)


class GuildPreferences:
    """Per-server playback defaults used by Discord slash commands."""

    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_preferences (
                    guild_id TEXT PRIMARY KEY,
                    volume REAL NOT NULL,
                    normalize INTEGER NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, guild_id: int) -> PlaybackDefaults:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT volume, normalize FROM guild_preferences WHERE guild_id = ?",
                (str(guild_id),),
            ).fetchone()
        if row is None:
            return PlaybackDefaults()
        return PlaybackDefaults(volume=row["volume"], normalize=bool(row["normalize"]))

    def update(
        self, guild_id: int, *, volume: float | None = None, normalize: bool | None = None
    ) -> PlaybackDefaults:
        current = self.get(guild_id)
        updated = PlaybackDefaults(
            volume=current.volume if volume is None else min(max(volume, 0.0), MAX_VOLUME),
            normalize=current.normalize if normalize is None else normalize,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO guild_preferences (guild_id, volume, normalize) VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET volume = excluded.volume, normalize = excluded.normalize
                """,
                (str(guild_id), updated.volume, int(updated.normalize)),
            )
        return updated
