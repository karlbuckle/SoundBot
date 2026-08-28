from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class UsageStats:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    guild_name TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def record(
        self,
        *,
        kind: str,
        item_id: str,
        title: str,
        actor_id: str,
        actor_name: str,
        source: str,
        guild_id: int,
        guild_name: str,
        channel_id: int,
        channel_name: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO usage_events (
                    occurred_at, kind, item_id, title, actor_id, actor_name,
                    source, guild_id, guild_name, channel_id, channel_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(), kind, item_id, title,
                    actor_id, actor_name, source, str(guild_id), guild_name,
                    str(channel_id), channel_name,
                ),
            )

    def summary(self, limit: int = 10) -> dict[str, object]:
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN kind = 'sound' THEN 1 ELSE 0 END) AS sounds,
                       SUM(CASE WHEN kind = 'youtube' THEN 1 ELSE 0 END) AS youtube,
                       COUNT(DISTINCT actor_id) AS users
                FROM usage_events
                """
            ).fetchone()
            top_sounds = connection.execute(
                """
                SELECT item_id, title, COUNT(*) AS plays
                FROM usage_events WHERE kind = 'sound'
                GROUP BY item_id, title ORDER BY plays DESC, title LIMIT ?
                """,
                (limit,),
            ).fetchall()
            top_users = connection.execute(
                """
                SELECT actor_id, actor_name, COUNT(*) AS plays,
                       COUNT(DISTINCT item_id) AS unique_items
                FROM usage_events GROUP BY actor_id, actor_name
                ORDER BY plays DESC, actor_name LIMIT ?
                """,
                (limit,),
            ).fetchall()
            methods = connection.execute(
                """
                SELECT source, kind, COUNT(*) AS plays
                FROM usage_events GROUP BY source, kind
                ORDER BY plays DESC, source, kind
                """
            ).fetchall()
            recent = connection.execute(
                """
                SELECT occurred_at, kind, title, actor_name, source,
                       guild_name, channel_name
                FROM usage_events ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return {
            "totals": {
                "plays": totals["total"] or 0,
                "sounds": totals["sounds"] or 0,
                "youtube": totals["youtube"] or 0,
                "users": totals["users"] or 0,
            },
            "top_sounds": [dict(row) for row in top_sounds],
            "top_users": [dict(row) for row in top_users],
            "methods": [dict(row) for row in methods],
            "recent": [dict(row) for row in recent],
        }
