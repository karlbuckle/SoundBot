import numpy as np
import pytest

from app.clips import ClipStore
from app.recording import SAMPLE_RATE, RollingRecorder, encode_clip, speech_gain

FRAME = SAMPLE_RATE // 50  # 20 ms


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def tone(amplitude: float, frames: int = 1) -> bytes:
    """Stereo 16-bit PCM of a 440 Hz tone, like a decoded Discord frame."""
    t = np.arange(FRAME * frames) / SAMPLE_RATE
    mono = (np.sin(2 * np.pi * 440 * t) * amplitude * 32767).astype("<i2")
    return np.repeat(mono, 2).tobytes()


def feed(recorder: RollingRecorder, clock: FakeClock, key: str, amplitude: float, seconds: float, rtp_start: int = 0) -> None:
    for index in range(int(seconds * 50)):
        clock.now += 0.02
        recorder.add_voice(key, key.title(), tone(amplitude), rtp_timestamp=rtp_start + index * FRAME)


def rms(samples: np.ndarray) -> float:
    active = samples[samples != 0].astype(np.float64) / 32768
    return float(np.sqrt(np.mean(active**2)))


def test_keeps_only_the_last_window() -> None:
    clock = FakeClock()
    recorder = RollingRecorder(seconds=5, clock=clock)
    feed(recorder, clock, "alice", 0.3, seconds=12)

    [speaker] = recorder.speakers()
    assert speaker.name == "Alice"
    assert 5.0 <= speaker.seconds <= 6.1
    assert len(recorder.mix()) == 5 * SAMPLE_RATE


def test_places_speech_at_the_right_time() -> None:
    clock = FakeClock()
    recorder = RollingRecorder(seconds=10, clock=clock)
    feed(recorder, clock, "alice", 0.3, seconds=2)
    clock.now += 5  # five seconds of silence: Discord sends no packets
    feed(recorder, clock, "alice", 0.3, seconds=1, rtp_start=SAMPLE_RATE * 50)

    mixed = recorder.mix(normalize=False)
    second = SAMPLE_RATE
    assert np.abs(mixed[: 2 * second]).max() == 0  # before anyone spoke
    assert np.abs(mixed[2 * second + 1000 : 4 * second - 1000]).max() > 0  # first burst
    assert np.abs(mixed[4 * second + 1000 : 9 * second - 1000]).max() == 0  # silence kept
    assert np.abs(mixed[9 * second + 1000 :]).max() > 0  # second burst


def test_normalizes_quiet_and_loud_speakers_to_similar_levels() -> None:
    clock = FakeClock()
    quiet, loud = RollingRecorder(seconds=4, clock=clock), RollingRecorder(seconds=4, clock=clock)
    feed(quiet, clock, "quiet", 0.03, seconds=3)
    feed(loud, clock, "loud", 0.6, seconds=3)

    quiet_rms, loud_rms = rms(quiet.mix()), rms(loud.mix())
    assert quiet_rms == pytest.approx(loud_rms, rel=0.1)
    assert rms(quiet.mix(normalize=False)) < quiet_rms / 3


def test_manual_gain_applies_per_speaker() -> None:
    clock = FakeClock()
    recorder = RollingRecorder(seconds=4, clock=clock)
    feed(recorder, clock, "alice", 0.2, seconds=3)

    full = rms(recorder.mix(normalize=False))
    half = rms(recorder.mix({"alice": 0.5}, normalize=False))
    assert half == pytest.approx(full / 2, rel=0.05)
    assert np.abs(recorder.mix({"alice": 0.0})).max() == 0


def test_soundboard_frames_play_back_to_back_and_skip_normalization() -> None:
    clock = FakeClock()
    recorder = RollingRecorder(seconds=4, clock=clock)
    for _ in range(100):
        clock.now += 0.02
        recorder.add_soundboard(tone(0.05))

    [speaker] = recorder.speakers()
    assert speaker.is_soundboard
    assert rms(recorder.mix()) == pytest.approx(rms(recorder.mix(normalize=False)), rel=0.01)


def test_rtp_timestamp_wraparound_stays_continuous() -> None:
    clock = FakeClock()
    recorder = RollingRecorder(seconds=4, clock=clock)
    feed(recorder, clock, "alice", 0.3, seconds=2, rtp_start=2**32 - FRAME * 10)

    mixed = recorder.mix(normalize=False)
    # No gap or jump where the 32-bit timestamp wraps back to zero.
    assert (np.abs(mixed[-2 * SAMPLE_RATE + 2000 : -2000]) > 0).mean() > 0.95


def test_speech_gain_ignores_silence() -> None:
    track = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    assert speech_gain(track) == 1.0
    track[:SAMPLE_RATE] = 0.05
    assert speech_gain(track) == pytest.approx(2.0)


def test_encodes_a_playable_clip() -> None:
    data, extension = encode_clip(np.zeros(SAMPLE_RATE, dtype=np.int16))
    assert extension in {"mp3", "wav"}
    assert len(data) > 1000


def test_clip_store_saves_lists_and_refuses_unsafe_names(tmp_path) -> None:
    store = ClipStore(tmp_path / "clips")
    clip = store.save(b"RIFF....", "wav", "Test server / #general")

    assert clip["name"].endswith("Test server general.wav")
    assert [item["name"] for item in store.list()] == [clip["name"]]
    assert store.path(clip["name"]) is not None
    assert store.path("../stats.db") is None
    assert store.path("..\\stats.db") is None
    assert store.delete(clip["name"])
    assert store.list() == []
