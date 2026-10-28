"""Always-on voice recording for the "save the last minute" feature.

Voice receive needs the experimental discord-ext-voice-recv package (which also
swaps discord.py for a fork that can decrypt Discord's end-to-end encrypted
voice). Everything here is inert unless ENABLE_RECORDING is set and that
package imports, so the bot behaves exactly as before without it.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from types import ModuleType
from typing import TYPE_CHECKING

import discord

from app.clips import ClipStore
from app.preferences import GuildPreferences

if TYPE_CHECKING:
    from app.recording import RollingRecorder


log = logging.getLogger(__name__)

WATCHDOG_SECONDS = 15


def _load_voice_recv() -> tuple[ModuleType | None, str]:
    try:
        return importlib.import_module("discord.ext.voice_recv"), ""
    except Exception as exc:  # ImportError, or the fork failing to initialise
        return None, f"discord-ext-voice-recv is not installed ({exc.__class__.__name__}). Rebuild with ENABLE_RECORDING=true."


class RecordingService:
    def __init__(
        self,
        bot: discord.Client,
        preferences: GuildPreferences,
        clips: ClipStore,
        *,
        enabled: bool,
        seconds: float = 60.0,
    ):
        self.bot = bot
        self.preferences = preferences
        self.clips = clips
        self.seconds = seconds
        self._voice_recv: ModuleType | None = None
        self.unavailable_reason = "Recording is switched off. Set ENABLE_RECORDING=true and rebuild to turn it on."
        if enabled:
            self._voice_recv, self.unavailable_reason = _load_voice_recv()
        self._recorders: dict[int, RollingRecorder] = {}
        # Guild ID -> channel ID for servers where recording is switched on. Kept
        # in memory because the soundboard tap reads it on every audio frame.
        self._active: dict[int, int] = self.preferences.recording_channels() if self.available else {}
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._voice_recv is not None

    @property
    def voice_client_class(self) -> type[discord.VoiceClient]:
        return self._voice_recv.VoiceRecvClient if self._voice_recv else discord.VoiceClient

    def is_recording(self, guild_id: int) -> bool:
        return guild_id in self._active

    def _recorder(self, guild_id: int) -> RollingRecorder:
        from app.recording import RollingRecorder

        recorder = self._recorders.get(guild_id)
        if recorder is None:
            recorder = self._recorders[guild_id] = RollingRecorder(self.seconds)
        return recorder

    def tap_playback(self, guild_id: int, pcm: bytes) -> None:
        """Called from the audio player thread with every frame the bot plays."""
        if guild_id in self._active:
            self._recorder(guild_id).add_soundboard(pcm)

    def _ensure_listening(self, guild_id: int, voice: discord.VoiceClient | None) -> bool:
        if self._voice_recv is None or not isinstance(voice, self._voice_recv.VoiceRecvClient):
            return False
        if not voice.is_connected():
            return False
        if not voice.is_listening():
            voice.listen(_make_sink(self._voice_recv, self._recorder(guild_id)))
            log.info("Recording started in guild %s, channel %s", guild_id, voice.channel.id)
        return True

    async def start(self, guild_id: int, channel_id: int) -> dict[str, object]:
        if not self.available:
            raise ValueError(self.unavailable_reason)
        async with self._lock:
            voice = await self.bot.playback.connect(guild_id, channel_id)
            self.preferences.set_recording_channel(guild_id, channel_id)
            self._active[guild_id] = channel_id
            self._ensure_listening(guild_id, voice)
        return self.status(guild_id)

    async def stop(self, guild_id: int) -> dict[str, object]:
        async with self._lock:
            self.preferences.set_recording_channel(guild_id, None)
            self._active.pop(guild_id, None)
            guild = self.bot.get_guild(guild_id)
            voice = guild.voice_client if guild else None
            if voice is not None and hasattr(voice, "stop_listening"):
                voice.stop_listening()
            recorder = self._recorders.pop(guild_id, None)
            if recorder is not None:
                recorder.clear()
        return self.status(guild_id)

    async def watchdog(self) -> None:
        """Keep the bot in its recording channel and listening, across reconnects and restarts."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for guild_id, channel_id in list(self._active.items()):
                try:
                    await self._keep_recording(guild_id, channel_id)
                except Exception:
                    log.exception("Could not resume recording in guild %s", guild_id)
            await asyncio.sleep(WATCHDOG_SECONDS)

    async def _keep_recording(self, guild_id: int, channel_id: int) -> None:
        async with self._lock:
            if self._active.get(guild_id) != channel_id:
                return  # stopped or changed while we waited
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            voice = guild.voice_client
            if voice is not None and voice.is_connected():
                if voice.channel.id != channel_id:
                    # Playing a sound moved the bot; keep recording where it is now.
                    self._active[guild_id] = voice.channel.id
                    self.preferences.set_recording_channel(guild_id, voice.channel.id)
            else:
                voice = await self.bot.playback.connect(guild_id, channel_id)
            self._ensure_listening(guild_id, voice)

    def status(self, guild_id: int) -> dict[str, object]:
        guild = self.bot.get_guild(guild_id)
        voice = guild.voice_client if guild else None
        channel_id = self._active.get(guild_id)
        channel = guild.get_channel(channel_id) if guild and channel_id else None
        recorder = self._recorders.get(guild_id)
        gains = self.preferences.recording_gains(guild_id) if self.available else {}
        return {
            "available": self.available,
            "reason": "" if self.available else self.unavailable_reason,
            "recording": channel_id is not None,
            "listening": bool(voice and getattr(voice, "is_listening", lambda: False)()),
            "channel_id": str(channel_id) if channel_id else None,
            "channel_name": channel.name if channel else None,
            "seconds": self.seconds,
            "speakers": [
                {
                    "key": speaker.key,
                    "name": speaker.name,
                    "seconds": round(speaker.seconds, 1),
                    "gain": gains.get(speaker.key, 1.0),
                    "is_soundboard": speaker.is_soundboard,
                }
                for speaker in (recorder.speakers() if recorder else [])
            ],
        }

    async def save_clip(self, guild_id: int) -> dict[str, object]:
        from app.recording import encode_clip

        if not self.is_recording(guild_id):
            raise ValueError("Recording is not switched on for this server.")
        recorder = self._recorders.get(guild_id)
        if recorder is None or not recorder.speakers():
            raise ValueError("Nothing has been recorded in the last minute.")
        gains = self.preferences.recording_gains(guild_id)
        samples = await asyncio.to_thread(recorder.mix, gains)
        data, extension = await asyncio.to_thread(encode_clip, samples)
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(self._active[guild_id]) if guild else None
        label = f"{guild.name if guild else guild_id} {channel.name if channel else ''}"
        return self.clips.save(data, extension, label)


def _make_sink(voice_recv: ModuleType, recorder: RollingRecorder):
    class RecordingSink(voice_recv.AudioSink):
        """Feeds every speaker's decoded audio into the rolling recorder."""

        def wants_opus(self) -> bool:
            return False

        def write(self, user, data) -> None:
            # Runs on the extension's router thread. An exception here would stop
            # receiving altogether, so never let one escape.
            try:
                if not data.pcm:
                    return
                packet = data.packet
                if user is None:
                    key, name = f"ssrc-{getattr(packet, 'ssrc', 0)}", "Unknown speaker"
                else:
                    key, name = str(user.id), getattr(user, "display_name", None) or user.name
                recorder.add_voice(key, name, data.pcm, getattr(packet, "timestamp", None))
            except Exception:
                log.exception("Dropped a received voice frame")

        def cleanup(self) -> None:
            pass

    return RecordingSink()
