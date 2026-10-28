import numpy as np
import pytest

from app.clip_editor import edit_clip, load_tracks, save_tracks
from app.clips import ClipStore
from app.recording import SAMPLE_RATE, Track, decode_audio, encode_clip, to_int16


def tone(amplitude: float, seconds: float, start: float = 0.0, total: float = 6.0) -> np.ndarray:
    samples = np.zeros(int(total * SAMPLE_RATE), dtype=np.float32)
    first = int(start * SAMPLE_RATE)
    count = int(seconds * SAMPLE_RATE)
    t = np.arange(count) / SAMPLE_RATE
    samples[first : first + count] = np.sin(2 * np.pi * 300 * t) * amplitude
    return samples


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


@pytest.fixture
def store(tmp_path) -> ClipStore:
    return ClipStore(tmp_path / "clips")


@pytest.fixture
def saved(store: ClipStore) -> str:
    tracks = [
        Track("1", "Alice", tone(0.2, 2, start=0), auto_gain=1.0),
        Track("2", "Bob", tone(0.2, 2, start=3), auto_gain=1.0),
    ]
    return save_tracks(store, tracks, {"2": 0.5}, label="Test server general")["name"]


def test_saving_keeps_each_speakers_track(store: ClipStore, saved: str) -> None:
    [clip] = store.list()
    assert clip["name"] == saved and clip["editable_levels"]
    manifest = store.manifest(saved)
    assert [(s["name"], s["gain"]) for s in manifest["speakers"]] == [("Alice", 1.0), ("Bob", 0.5)]
    assert manifest["duration"] == pytest.approx(6.0)

    tracks, gains, has_stems = load_tracks(store, saved)
    assert has_stems and gains == {"1": 1.0, "2": 0.5}
    assert rms(tracks[0].samples[: SAMPLE_RATE]) > 0.1  # Alice talks first
    assert rms(tracks[1].samples[: SAMPLE_RATE]) == 0


def test_trim_saves_a_new_clip_and_keeps_the_original(store: ClipStore, saved: str) -> None:
    edited = edit_clip(store, saved, start=2.5, end=5.5)

    assert edited["name"] == saved.replace(".wav", " (edit).wav")
    assert store.path(saved) is not None
    mix = decode_audio(store.path(edited["name"]))
    assert len(mix) == 3 * SAMPLE_RATE
    # Only Bob's part (3s-5s of the original) remains, starting half a second in.
    assert rms(mix[: SAMPLE_RATE // 4]) == 0
    assert rms(mix[SAMPLE_RATE : 2 * SAMPLE_RATE]) > 0
    # The edit is itself editable, with the same saved levels.
    assert store.manifest(edited["name"])["duration"] == pytest.approx(3.0)

    again = edit_clip(store, edited["name"], start=0, end=2)
    assert again["name"] == saved.replace(".wav", " (edit 2).wav")


def test_changing_a_speakers_level(store: ClipStore, saved: str) -> None:
    quiet = decode_audio(store.path(edit_clip(store, saved, 3, 5)["name"]))
    loud = decode_audio(store.path(edit_clip(store, saved, 3, 5, {"2": 1.0})["name"]))
    muted = decode_audio(store.path(edit_clip(store, saved, 3, 5, {"2": 0, "unknown": 3})["name"]))

    assert rms(loud) == pytest.approx(rms(quiet) * 2, rel=0.02)
    assert rms(muted) == 0
    manifest = store.manifest(store.list()[0]["name"])
    assert [s["key"] for s in manifest["speakers"]] == ["1", "2"]


def test_replace_overwrites_in_place(store: ClipStore, saved: str) -> None:
    result = edit_clip(store, saved, 0, 1, replace=True)
    assert result["name"] == saved
    assert [clip["name"] for clip in store.list()] == [saved]
    assert len(decode_audio(store.path(saved))) == SAMPLE_RATE
    assert store.manifest(saved)["duration"] == pytest.approx(1.0)


def test_clips_without_stems_can_still_be_trimmed(store: ClipStore) -> None:
    data, extension = encode_clip(to_int16(tone(0.3, 4, total=4)))
    old = store.save(data, extension, "old clip")["name"]
    assert store.manifest(old) is None

    edited = edit_clip(store, old, 1, 2, {"1": 4.0})
    assert len(decode_audio(store.path(edited["name"]))) == SAMPLE_RATE
    assert not edited["editable_levels"]


@pytest.mark.parametrize("start, end", [(-1, 2), (3, 2), (2, 2.1), (7, 8)])
def test_rejects_bad_ranges(store: ClipStore, saved: str, start: float, end: float) -> None:
    with pytest.raises(ValueError):
        edit_clip(store, saved, start, end)


def test_deleting_removes_the_tracks_too(store: ClipStore, saved: str) -> None:
    stems = store.directory / saved.replace(".wav", ".stems")
    assert stems.is_dir()
    assert store.delete(saved)
    assert not stems.exists()


def test_refuses_unsafe_track_names(store: ClipStore, saved: str) -> None:
    assert store.stem_path(saved, "track0.wav") is not None
    assert store.stem_path(saved, "../manifest.json") is None
    assert store.stem_path(saved, "manifest.json") is None
    assert store.path("..(edit).wav") is None
