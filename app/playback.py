from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess
import time

import discord

from app.youtube import extract_youtube_track


@dataclass
class PlaybackState:
    kind: str
    title: str
    source: str
    channel_id: int
    input_source: str
    before_options: str = ""
    volume: float = 1.0
    normalize: bool = False
    duration_seconds: float | None = None
    offset_seconds: float = 0.0
    started_monotonic: float = 0.0
    play_id: int = 0


class PlaybackService:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.states: dict[int, PlaybackState] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._play_ids: dict[int, int] = {}

    async def _voice_client(self, guild_id: int, channel_id: int) -> discord.VoiceClient:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise ValueError("The bot is not connected to that Discord server.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            raise ValueError("Choose a valid Discord voice channel.")

        voice = guild.voice_client
        if voice is None:
            return await channel.connect()
        if voice.channel.id != channel.id:
            await voice.move_to(channel)
        return voice

    @staticmethod
    def _probe_file_duration(path: Path) -> float | None:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                return None
            value = (result.stdout or "").strip()
            duration = float(value)
            return duration if duration > 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _build_source(state: PlaybackState) -> discord.AudioSource:
        before_options = state.before_options.strip()
        if state.offset_seconds > 0:
            before_options = f"{before_options} -ss {state.offset_seconds:.3f}".strip()
        filters = [f"volume={max(0.0, state.volume):.3f}"]
        if state.normalize:
            filters.insert(0, "loudnorm=I=-16:LRA=11:TP=-1.5")
        options = f'-vn -af "{",".join(filters)}"'
        return discord.FFmpegPCMAudio(
            state.input_source, before_options=before_options or None, options=options
        )

    def _current_position(self, state: PlaybackState, *, paused: bool) -> float:
        elapsed = 0.0 if paused else max(0.0, time.monotonic() - state.started_monotonic)
        position = state.offset_seconds + elapsed
        if state.duration_seconds is not None:
            return min(position, state.duration_seconds)
        return position

    async def _play(self, guild_id: int, state: PlaybackState) -> PlaybackState:
        lock = self._locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            voice = await self._voice_client(guild_id, state.channel_id)
            if voice.is_playing() or voice.is_paused():
                voice.stop()
            state.play_id = self._play_ids.get(guild_id, 0) + 1
            self._play_ids[guild_id] = state.play_id
            play_id = state.play_id
            state.started_monotonic = time.monotonic()
            source = self._build_source(state)
            self.states[guild_id] = state
            loop = asyncio.get_running_loop()

            def finished(error: Exception | None) -> None:
                def clear_current_state() -> None:
                    current = self.states.get(guild_id)
                    if current and current.play_id == play_id:
                        self.states.pop(guild_id, None)

                loop.call_soon_threadsafe(clear_current_state)
                if error:
                    print(f"Playback error in guild {guild_id}: {error}")

            voice.play(source, after=finished)
        return state

    async def play_file(
        self,
        guild_id: int,
        channel_id: int,
        path: Path,
        title: str,
        *,
        volume: float = 1.0,
        normalize: bool = False,
    ) -> PlaybackState:
        if not path.is_file():
            raise ValueError("That audio file no longer exists.")
        state = PlaybackState(
            kind="sound",
            title=title,
            source=path.name,
            channel_id=channel_id,
            input_source=str(path),
            duration_seconds=self._probe_file_duration(path),
            volume=volume,
            normalize=normalize,
        )
        return await self._play(guild_id, state)

    async def play_youtube(
        self,
        guild_id: int,
        channel_id: int,
        url: str,
        *,
        volume: float = 1.0,
        normalize: bool = False,
    ) -> PlaybackState:
        track = await extract_youtube_track(url)
        state = PlaybackState(
            kind="youtube",
            title=track.title,
            source=track.webpage_url,
            channel_id=channel_id,
            input_source=track.stream_url,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            duration_seconds=track.duration_seconds,
            volume=volume,
            normalize=normalize,
        )
        return await self._play(guild_id, state)

    async def _restart_with_current_settings(self, guild_id: int, state: PlaybackState) -> PlaybackState:
        return await self._play(guild_id, state)

    async def set_volume(self, guild_id: int, volume: float) -> PlaybackState:
        if volume < 0:
            raise ValueError("Volume must be 0 or higher.")
        state = self.states.get(guild_id)
        if state is None:
            raise ValueError("Nothing is playing.")
        guild = self.bot.get_guild(guild_id)
        voice = guild.voice_client if guild else None
        state.offset_seconds = self._current_position(state, paused=bool(voice and voice.is_paused()))
        state.volume = volume
        return await self._restart_with_current_settings(guild_id, state)

    async def set_normalize(self, guild_id: int, normalize: bool) -> PlaybackState:
        state = self.states.get(guild_id)
        if state is None:
            raise ValueError("Nothing is playing.")
        guild = self.bot.get_guild(guild_id)
        voice = guild.voice_client if guild else None
        state.offset_seconds = self._current_position(state, paused=bool(voice and voice.is_paused()))
        state.normalize = normalize
        return await self._restart_with_current_settings(guild_id, state)

    async def seek(self, guild_id: int, position_seconds: float) -> PlaybackState:
        state = self.states.get(guild_id)
        if state is None:
            raise ValueError("Nothing is playing.")
        if state.duration_seconds is None:
            raise ValueError("Seeking is unavailable for this source.")
        state.offset_seconds = min(max(position_seconds, 0.0), state.duration_seconds)
        return await self._restart_with_current_settings(guild_id, state)

    def status(self, guild_id: int) -> dict[str, str | float | bool | None] | None:
        state = self.states.get(guild_id)
        if not state:
            return None
        guild = self.bot.get_guild(guild_id)
        voice = guild.voice_client if guild else None
        paused = bool(voice and voice.is_paused())
        playing = bool(voice and (voice.is_playing() or paused))
        data = asdict(state)
        data["position_seconds"] = self._current_position(state, paused=paused)
        data["is_playing"] = playing
        data["can_seek"] = state.duration_seconds is not None
        for key in ("input_source", "before_options", "started_monotonic", "play_id", "channel_id", "offset_seconds"):
            data.pop(key, None)
        return data

    def stop(self, guild_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client and (guild.voice_client.is_playing() or guild.voice_client.is_paused()):
            guild.voice_client.stop()
            self.states.pop(guild_id, None)
            return True
        return False

    async def leave(self, guild_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client:
            await guild.voice_client.disconnect(force=True)
            self.states.pop(guild_id, None)
            return True
        return False
