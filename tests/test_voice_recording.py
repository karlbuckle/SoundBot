import asyncio
from types import SimpleNamespace

import discord
import numpy as np
import pytest

from app.clips import ClipStore
from app.playback import TappedVolumeTransformer, stop_audio
from app.preferences import GuildPreferences
from app.recording import SAMPLE_RATE, RollingRecorder
from app.voice_recording import RecordingService, _make_sink

FRAME_BYTES = SAMPLE_RATE // 50 * 4  # 20 ms of 16-bit stereo


class Silence(discord.AudioSource):
    def read(self) -> bytes:
        return b"\x01\x00" * (FRAME_BYTES // 2)


def test_tapped_volume_transformer_hands_frames_to_the_tap() -> None:
    frames = []
    source = TappedVolumeTransformer(Silence(), 0.5, frames.append)
    assert len(source.read()) == FRAME_BYTES
    assert len(frames) == 1

    def broken(_pcm: bytes) -> None:
        raise RuntimeError("boom")

    # A failing tap must never break playback.
    assert len(TappedVolumeTransformer(Silence(), 1.0, broken).read()) == FRAME_BYTES


def test_stop_audio_prefers_stop_playing() -> None:
    calls = []
    recording_client = SimpleNamespace(stop_playing=lambda: calls.append("stop_playing"), stop=lambda: calls.append("stop"))
    stock_client = SimpleNamespace(stop=lambda: calls.append("stop"))
    stop_audio(recording_client)
    stop_audio(stock_client)
    assert calls == ["stop_playing", "stop"]


def test_service_is_inert_when_switched_off(tmp_path) -> None:
    service = RecordingService(
        SimpleNamespace(), GuildPreferences(tmp_path / "db"), ClipStore(tmp_path / "clips"), enabled=False
    )
    assert not service.available
    assert service.voice_client_class is discord.VoiceClient
    service.tap_playback(1, b"\x00" * FRAME_BYTES)  # no-op, no error
    with pytest.raises(ValueError, match="ENABLE_RECORDING"):
        asyncio.run(service.start(1, 2))


def test_sink_feeds_each_speaker_into_the_recorder() -> None:
    voice_recv = pytest.importorskip("discord.ext.voice_recv", reason="recording extension not installed")
    recorder = RollingRecorder(seconds=5)
    sink = _make_sink(voice_recv, recorder)
    assert not sink.wants_opus()

    alice = SimpleNamespace(id=111, name="alice", display_name="Alice")
    pcm = (np.ones(FRAME_BYTES // 2, dtype="<i2") * 1000).tobytes()
    for index in range(10):
        packet = voice_recv.rtp.FakePacket(ssrc=5, sequence=index, timestamp=index * 960)
        sink.write(alice, voice_recv.VoiceData(packet, alice, pcm=pcm))
    sink.write(None, voice_recv.VoiceData(voice_recv.rtp.FakePacket(ssrc=9, sequence=0, timestamp=0), None, pcm=pcm))
    sink.write(alice, voice_recv.VoiceData(voice_recv.rtp.FakePacket(ssrc=5, sequence=11, timestamp=0), alice))  # no PCM

    speakers = {speaker.key: speaker for speaker in recorder.speakers()}
    assert speakers["111"].name == "Alice"
    assert speakers["111"].seconds == pytest.approx(0.2, abs=0.01)
    assert speakers["ssrc-9"].name == "Unknown speaker"


def test_sink_swallows_bad_frames() -> None:
    voice_recv = pytest.importorskip("discord.ext.voice_recv", reason="recording extension not installed")
    sink = _make_sink(voice_recv, recorder=None)  # recorder.add_voice will raise
    alice = SimpleNamespace(id=1, name="a", display_name="A")
    packet = voice_recv.rtp.FakePacket(ssrc=1, sequence=0, timestamp=0)
    sink.write(alice, voice_recv.VoiceData(packet, alice, pcm=b"\x00" * FRAME_BYTES))
