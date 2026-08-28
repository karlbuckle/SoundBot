from pathlib import Path

from app.stats import UsageStats


def test_records_and_summarises_usage(tmp_path: Path) -> None:
    stats = UsageStats(tmp_path / "stats.db")
    stats.record(
        kind="sound",
        item_id="airhorn-id",
        title="airhorn",
        actor_id="discord:123",
        actor_name="Karl",
        source="Discord command",
        guild_id=1,
        guild_name="Test server",
        channel_id=2,
        channel_name="General",
    )
    stats.record(
        kind="sound",
        item_id="airhorn-id",
        title="airhorn",
        actor_id="web:abc",
        actor_name="Web tester",
        source="Web UI",
        guild_id=1,
        guild_name="Test server",
        channel_id=2,
        channel_name="General",
    )

    report = stats.summary()

    assert report["totals"] == {"plays": 2, "sounds": 2, "youtube": 0, "users": 2}
    assert report["top_sounds"][0]["title"] == "airhorn"
    assert report["top_sounds"][0]["plays"] == 2
    assert {item["source"] for item in report["methods"]} == {"Discord command", "Web UI"}
