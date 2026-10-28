from pathlib import Path

from app.preferences import GuildPreferences, PlaybackDefaults


def test_defaults_until_a_server_saves_its_own(tmp_path: Path) -> None:
    preferences = GuildPreferences(tmp_path / "stats.db")

    assert preferences.get(1) == PlaybackDefaults(volume=1.0, normalize=False)

    preferences.update(1, volume=0.5)
    preferences.update(1, normalize=True)

    assert preferences.get(1) == PlaybackDefaults(volume=0.5, normalize=True)
    assert preferences.get(2) == PlaybackDefaults()


def test_volume_is_clamped(tmp_path: Path) -> None:
    preferences = GuildPreferences(tmp_path / "stats.db")

    assert preferences.update(1, volume=9.0).volume == 3.0
    assert preferences.update(1, volume=-1.0).volume == 0.0
