from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path

import discord

from app.youtube import extract_youtube_track


@dataclass
class PlaybackState:
    kind: str
    title: str
    source: str


class PlaybackService:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.states: dict[int, PlaybackState] = {}
        self._locks: dict[int, asyncio.Lock] = {}

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

    async def _play(
        self,
        guild_id: int,
        channel_id: int,
        source: discord.AudioSource,
        state: PlaybackState,
    ) -> PlaybackState:
        lock = self._locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            voice = await self._voice_client(guild_id, channel_id)
            if voice.is_playing() or voice.is_paused():
                voice.stop()
            self.states[guild_id] = state
            loop = asyncio.get_running_loop()

            def finished(error: Exception | None) -> None:
                loop.call_soon_threadsafe(self.states.pop, guild_id, None)
                if error:
                    print(f"Playback error in guild {guild_id}: {error}")

            voice.play(source, after=finished)
        return state

    async def play_file(self, guild_id: int, channel_id: int, path: Path, title: str) -> PlaybackState:
        if not path.is_file():
            raise ValueError("That audio file no longer exists.")
        state = PlaybackState(kind="sound", title=title, source=path.name)
        source = discord.FFmpegPCMAudio(str(path))
        return await self._play(guild_id, channel_id, source, state)

    async def play_youtube(self, guild_id: int, channel_id: int, url: str) -> PlaybackState:
        track = await extract_youtube_track(url)
        state = PlaybackState(kind="youtube", title=track.title, source=track.webpage_url)
        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        source = discord.FFmpegPCMAudio(track.stream_url, before_options=before_options)
        return await self._play(guild_id, channel_id, source, state)

    def status(self, guild_id: int) -> dict[str, str] | None:
        state = self.states.get(guild_id)
        return asdict(state) if state else None

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
