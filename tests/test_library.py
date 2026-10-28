from pathlib import Path

from app.library import AudioLibrary


def test_scans_multiple_directories_and_ignores_other_files(tmp_path: Path) -> None:
    first = tmp_path / "memes"
    second = tmp_path / "music"
    first.mkdir()
    second.mkdir()
    (first / "airhorn.mp3").touch()
    (first / "notes.txt").touch()
    (second / "intro.ogg").touch()

    sounds = AudioLibrary((first, second)).all()

    assert [(sound.name, sound.collection) for sound in sounds] == [
        ("airhorn", "memes"),
        ("intro", "music"),
    ]


def test_get_only_returns_scanned_audio(tmp_path: Path) -> None:
    audio = tmp_path / "safe.wav"
    audio.touch()
    library = AudioLibrary((tmp_path,))
    sound = library.all()[0]

    assert library.get(sound.id) is not None
    assert library.get("../../etc/passwd") is None


def test_uses_cached_scan_until_refreshed(tmp_path: Path) -> None:
    (tmp_path / "first.mp3").touch()
    library = AudioLibrary((tmp_path,))
    assert [sound.name for sound in library.all()] == ["first"]

    (tmp_path / "second.mp3").touch()

    assert [sound.name for sound in library.all()] == ["first"]
    assert [sound.name for sound in library.all(refresh=True)] == ["first", "second"]


def test_get_finds_files_added_after_the_last_scan(tmp_path: Path) -> None:
    library = AudioLibrary((tmp_path,))
    assert library.all() == []
    (tmp_path / "late.mp3").touch()
    late_id = AudioLibrary((tmp_path,)).all()[0].id

    assert library.get(late_id) is not None


def test_scans_subfolders_with_relative_paths_for_browsing(tmp_path: Path) -> None:
    library_root = tmp_path / "soundboard"
    nested = library_root / "memes" / "classic"
    nested.mkdir(parents=True)
    (nested / "airhorn.mp3").touch()

    sound = AudioLibrary((library_root,)).all()[0]

    assert sound.collection == "soundboard"
    assert sound.relative_path == "memes/classic/airhorn.mp3"
