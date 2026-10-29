import os
import time

import pytest

from app.clips import ClipStore, clean_sound_name, clean_subfolder, is_writable


@pytest.fixture
def store(tmp_path) -> ClipStore:
    return ClipStore(tmp_path / "clips")


def test_export_copies_into_the_library_without_overwriting(store: ClipStore, tmp_path) -> None:
    clip = store.save(b"audio", "mp3", "Barrington Lounge general")["name"]
    folder = tmp_path / "sounds" / "clips"

    first = store.export(clip, folder, "Lou's big moment")
    second = store.export(clip, folder, "Lou's big moment")
    default = store.export(clip, folder)

    assert first.name == "Lou's big moment.mp3"
    assert second.name == "Lou's big moment (2).mp3"
    # Without a name, the clip's timestamp prefix is dropped.
    assert default.name == "Barrington Lounge general.mp3"
    assert first.read_bytes() == b"audio"
    assert store.path(clip) is not None  # the clip itself stays


def test_exported_sound_counts_as_newly_added(store: ClipStore, tmp_path) -> None:
    clip = store.save(b"audio", "mp3", "old")["name"]
    old = time.time() - 86400
    os.utime(store.path(clip), (old, old))
    exported = store.export(clip, tmp_path / "sounds")
    assert exported.stat().st_mtime > old + 3600


def test_export_rejects_unknown_clips(store: ClipStore, tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        store.export("missing.mp3", tmp_path)


@pytest.mark.parametrize(
    "value, expected",
    [("clips", "clips"), (" clips / funny ", "clips/funny"), ("", ""), ("a\\b", "a/b")],
)
def test_clean_subfolder_accepts_simple_paths(value: str, expected: str) -> None:
    assert clean_subfolder(value) == expected


@pytest.mark.parametrize("value", ["..", "clips/../..", "a/b/c/d", ".hidden", "bad:name", "x" * 70])
def test_clean_subfolder_rejects_escapes_and_odd_names(value: str) -> None:
    with pytest.raises(ValueError):
        clean_subfolder(value)


def test_clean_sound_name_strips_path_characters() -> None:
    assert clean_sound_name('../../etc/passwd') == "etc passwd"
    assert clean_sound_name('  what: a "moment"?  ') == "what a moment"
    assert clean_sound_name("...") == ""


def test_is_writable_checks_the_nearest_existing_folder(tmp_path) -> None:
    assert is_writable(tmp_path / "not" / "made" / "yet")
    (tmp_path / "file.txt").write_text("x")
    assert not is_writable(tmp_path / "file.txt" / "inside")
