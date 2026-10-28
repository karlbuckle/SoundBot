from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


MAX_VOLUME = 3.0
MAX_SPEAKER_GAIN = 4.0


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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recording_channels (
                    guild_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recording_gains (
                    guild_id TEXT NOT NULL,
                    speaker_key TEXT NOT NULL,
                    gain REAL NOT NULL,
                    PRIMARY KEY (guild_id, speaker_key)
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

    def recording_channels(self) -> dict[int, int]:
        """Servers where recording is switched on, and the channel to record."""
        with self._connect() as connection:
            rows = connection.execute("SELECT guild_id, channel_id FROM recording_channels").fetchall()
        return {int(row["guild_id"]): int(row["channel_id"]) for row in rows}

    def set_recording_channel(self, guild_id: int, channel_id: int | None) -> None:
        with self._connect() as connection:
            if channel_id is None:
                connection.execute("DELETE FROM recording_channels WHERE guild_id = ?", (str(guild_id),))
            else:
                connection.execute(
                    """
                    INSERT INTO recording_channels (guild_id, channel_id) VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id
                    """,
                    (str(guild_id), str(channel_id)),
                )

    def recording_gains(self, guild_id: int) -> dict[str, float]:
        """Manual per-speaker gain, keyed by Discord user ID or 'soundboard'."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT speaker_key, gain FROM recording_gains WHERE guild_id = ?", (str(guild_id),)
            ).fetchall()
        return {row["speaker_key"]: row["gain"] for row in rows}

    def set_recording_gain(self, guild_id: int, speaker_key: str, gain: float) -> float:
        gain = min(max(gain, 0.0), MAX_SPEAKER_GAIN)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO recording_gains (guild_id, speaker_key, gain) VALUES (?, ?, ?)
                ON CONFLICT(guild_id, speaker_key) DO UPDATE SET gain = excluded.gain
                """,
                (str(guild_id), speaker_key, gain),
            )
        return gain
