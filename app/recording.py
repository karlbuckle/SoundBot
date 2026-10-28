"""Rolling per-speaker voice buffer and clip mixing.

Discord delivers each speaker as a separate stream of 20 ms, 48 kHz stereo PCM
frames, and sends nothing while someone is silent. Each speaker gets their own
track of timestamped mono chunks covering the last ``seconds`` of audio. Saving
a clip lays every track onto a shared timeline, evens out each speaker's
loudness, applies any manual per-speaker gain, and mixes them down.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


SAMPLE_RATE = 48_000
SOUNDBOARD_KEY = "soundboard"

# RTP timestamps are 32-bit and count samples at 48 kHz.
_RTP_WRAP = 2**32
_RTP_HALF = 2**31
# Re-anchor a speaker's timeline when packet timing drifts this far from the clock.
_MAX_DRIFT_SECONDS = 0.75

# Per-speaker loudness normalisation.
_TARGET_RMS = 0.1  # about -20 dBFS
_ACTIVE_FRAME_RMS = 0.003  # frames quieter than about -50 dBFS count as silence
_MIN_AUTO_GAIN = 0.25
_MAX_AUTO_GAIN = 6.0


def stereo_to_mono(pcm: bytes) -> bytes:
    """Downmix interleaved 16-bit stereo PCM to mono."""
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 4], dtype="<i2").reshape(-1, 2)
    return samples.mean(axis=1).astype("<i2").tobytes()


@dataclass
class _Chunk:
    start: float
    pcm: bytes  # mono, 16-bit little-endian, 48 kHz

    @property
    def end(self) -> float:
        return self.start + len(self.pcm) / 2 / SAMPLE_RATE


@dataclass
class _Track:
    key: str
    name: str
    chunks: deque[_Chunk] = field(default_factory=deque)
    rtp_anchor: int | None = None
    wall_anchor: float = 0.0

    @property
    def last_end(self) -> float | None:
        return self.chunks[-1].end if self.chunks else None


@dataclass(frozen=True)
class Speaker:
    key: str
    name: str
    seconds: float

    @property
    def is_soundboard(self) -> bool:
        return self.key == SOUNDBOARD_KEY


class RollingRecorder:
    """Thread-safe buffer of the last ``seconds`` of audio, one track per speaker."""

    def __init__(self, seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.seconds = seconds
        self._clock = clock
        self._tracks: dict[str, _Track] = {}
        self._lock = threading.Lock()

    def add_voice(self, key: str, name: str, pcm: bytes, rtp_timestamp: int | None = None) -> None:
        """Add a stereo PCM frame received from a speaker."""
        mono = stereo_to_mono(pcm)
        if not mono:
            return
        duration = len(mono) / 2 / SAMPLE_RATE
        with self._lock:
            now = self._clock()
            track = self._track(key, name)
            start = self._place(track, rtp_timestamp, duration, now)
            track.chunks.append(_Chunk(start, mono))
            self._prune(now)

    def add_soundboard(self, pcm: bytes) -> None:
        """Add a stereo PCM frame the bot itself is playing."""
        self.add_voice(SOUNDBOARD_KEY, "Soundboard", pcm)

    def speakers(self) -> list[Speaker]:
        with self._lock:
            self._prune(self._clock())
            return [
                Speaker(track.key, track.name, sum(chunk.end - chunk.start for chunk in track.chunks))
                for track in self._tracks.values()
            ]

    def clear(self) -> None:
        with self._lock:
            self._tracks.clear()

    def mix(self, gains: dict[str, float] | None = None, normalize: bool = True) -> np.ndarray:
        """Mix the buffered window into mono int16 samples ending now."""
        gains = gains or {}
        length = int(round(self.seconds * SAMPLE_RATE))
        with self._lock:
            now = self._clock()
            self._prune(now)
            window_start = now - self.seconds
            tracks = [(track.key, list(track.chunks)) for track in self._tracks.values()]

        mixed = np.zeros(length, dtype=np.float32)
        for key, chunks in tracks:
            track = _render(chunks, window_start, length)
            # The soundboard is already at the volume it was played at.
            auto = speech_gain(track) if normalize and key != SOUNDBOARD_KEY else 1.0
            mixed += track * auto * max(0.0, gains.get(key, 1.0))

        if np.abs(mixed).max(initial=0.0) > 0.9:
            # Soft-limit instead of hard clipping when voices overlap.
            mixed = np.tanh(mixed)
        return (np.clip(mixed, -1.0, 1.0) * 32767).astype(np.int16)

    def _track(self, key: str, name: str) -> _Track:
        track = self._tracks.get(key)
        if track is None:
            track = self._tracks[key] = _Track(key, name)
        elif name and track.name != name:
            track.name = name
        return track

    @staticmethod
    def _place(track: _Track, rtp_timestamp: int | None, duration: float, now: float) -> float:
        """Work out where a frame that just arrived belongs on the timeline."""
        arrived_start = now - duration
        if rtp_timestamp is not None:
            if track.rtp_anchor is not None:
                # Signed sample offset from the anchor, allowing for 32-bit wrap-around.
                offset = (rtp_timestamp - track.rtp_anchor + _RTP_HALF) % _RTP_WRAP - _RTP_HALF
                start = track.wall_anchor + offset / SAMPLE_RATE
                if abs(start - arrived_start) <= _MAX_DRIFT_SECONDS:
                    return start
            track.rtp_anchor = rtp_timestamp
            track.wall_anchor = arrived_start
            return arrived_start
        # No packet timing (our own playback): keep consecutive frames back to back.
        last_end = track.last_end
        if last_end is not None and abs(last_end - arrived_start) <= _MAX_DRIFT_SECONDS:
            return last_end
        return arrived_start

    def _prune(self, now: float) -> None:
        cutoff = now - self.seconds - 1.0
        for key in list(self._tracks):
            chunks = self._tracks[key].chunks
            while chunks and chunks[0].end < cutoff:
                chunks.popleft()
            if not chunks:
                del self._tracks[key]


def _render(chunks: list[_Chunk], window_start: float, length: int) -> np.ndarray:
    track = np.zeros(length, dtype=np.float32)
    for chunk in chunks:
        samples = np.frombuffer(chunk.pcm, dtype="<i2").astype(np.float32) / 32768.0
        offset = int(round((chunk.start - window_start) * SAMPLE_RATE))
        first, last = max(0, offset), min(length, offset + len(samples))
        if last > first:
            track[first:last] = samples[first - offset : last - offset]
    return track


def speech_gain(track: np.ndarray) -> float:
    """Gain that brings a speaker's active speech to the target loudness."""
    frame = SAMPLE_RATE // 50  # 20 ms
    usable = len(track) - len(track) % frame
    if usable == 0:
        return 1.0
    frame_rms = np.sqrt(np.mean(track[:usable].reshape(-1, frame) ** 2, axis=1))
    active = frame_rms[frame_rms > _ACTIVE_FRAME_RMS]
    if active.size == 0:
        return 1.0
    speech_rms = float(np.sqrt(np.mean(active**2)))
    return float(np.clip(_TARGET_RMS / speech_rms, _MIN_AUTO_GAIN, _MAX_AUTO_GAIN))


def encode_clip(samples: np.ndarray) -> tuple[bytes, str]:
    """Encode mono int16 samples as MP3 when FFmpeg is available, otherwise WAV."""
    wav = io.BytesIO()
    with wave.open(wav, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(samples.astype("<i2").tobytes())
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return wav.getvalue(), "wav"
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"],
        input=wav.getvalue(), capture_output=True, timeout=60, check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return wav.getvalue(), "wav"
    return result.stdout, "mp3"
