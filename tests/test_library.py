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
