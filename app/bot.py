from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from app.library import AudioLibrary
from app.playback import PlaybackService
from app.preferences import GuildPreferences, PlaybackDefaults
from app.stats import UsageStats


class SoundboardBot(commands.Bot):
    def __init__(self, library: AudioLibrary, stats: UsageStats, preferences: GuildPreferences):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.library = library
        self.stats = stats
        self.preferences = preferences
        self.playback = PlaybackService(self)

    async def setup_hook(self) -> None:
        self.tree.add_command(SoundboardCommands(self))
        await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Discord bot logged in as {self.user} ({self.user.id if self.user else 'unknown'})")


class SoundboardCommands(app_commands.Group):
    def __init__(self, bot: SoundboardBot):
        super().__init__(name="soundboard", description="Play sounds and YouTube audio")
        self.bot = bot

    @staticmethod
    def _voice_details(interaction: discord.Interaction) -> tuple[int, int]:
        if interaction.guild_id is None or not isinstance(interaction.user, discord.Member):
            raise ValueError("This command can only be used in a Discord server.")
        voice = interaction.user.voice
        if voice is None or voice.channel is None:
            raise ValueError("Join a voice channel first.")
        return interaction.guild_id, voice.channel.id

    def _playback_options(
        self, guild_id: int, volume: int | None, normalize: bool | None
    ) -> dict[str, float | bool]:
        """Command options win; anything left out falls back to the server defaults."""
        defaults = self.bot.preferences.get(guild_id)
        return {
            "volume": defaults.volume if volume is None else volume / 100,
            "normalize": defaults.normalize if normalize is None else normalize,
        }

    @staticmethod
    def _describe_defaults(defaults: PlaybackDefaults) -> str:
        return f"volume **{round(defaults.volume * 100)}%**, normalize **{'on' if defaults.normalize else 'off'}**"

    @app_commands.command(name="play", description="Play a sound from the sound library")
    @app_commands.describe(
        sound="Start typing the sound name",
        volume="Volume percent (0–300). Defaults to this server's setting.",
        normalize="Even out loudness. Defaults to this server's setting.",
    )
    async def play(
        self,
        interaction: discord.Interaction,
        sound: str,
        volume: Optional[app_commands.Range[int, 0, 300]] = None,
        normalize: bool | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            guild_id, channel_id = self._voice_details(interaction)
            item = self.bot.library.get(sound)
            if item is None:
                raise ValueError("Sound not found. Pick a sound from autocomplete.")
            await self.bot.playback.play_file(
                guild_id, channel_id, item.path, item.name,
                **self._playback_options(guild_id, volume, normalize),
            )
            self.bot.stats.record(
                kind="sound",
                item_id=item.id,
                title=item.name,
                actor_id=f"discord:{interaction.user.id}",
                actor_name=str(interaction.user),
                source="Discord command",
                guild_id=guild_id,
                guild_name=interaction.guild.name if interaction.guild else str(guild_id),
                channel_id=channel_id,
                channel_name=interaction.user.voice.channel.name,
            )
            await interaction.followup.send(f"🔊 Playing **{item.name}**")
        except Exception as exc:
            await interaction.followup.send(f"Could not play that sound: {exc}", ephemeral=True)

    @play.autocomplete("sound")
    async def sound_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(
                name=f"{item.name} — {item.collection}"[:100], value=item.id
            )
            for item in self.bot.library.search(current)
        ]

    @app_commands.command(name="youtube", description="Play audio from a YouTube URL")
    @app_commands.describe(
        url="A youtube.com or youtu.be URL",
        volume="Volume percent (0–300). Defaults to this server's setting.",
        normalize="Even out loudness. Defaults to this server's setting.",
    )
    async def youtube(
        self,
        interaction: discord.Interaction,
        url: str,
        volume: Optional[app_commands.Range[int, 0, 300]] = None,
        normalize: bool | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        try:
            guild_id, channel_id = self._voice_details(interaction)
            state = await self.bot.playback.play_youtube(
                guild_id, channel_id, url, **self._playback_options(guild_id, volume, normalize)
            )
            self.bot.stats.record(
                kind="youtube",
                item_id=state.source,
                title=state.title,
                actor_id=f"discord:{interaction.user.id}",
                actor_name=str(interaction.user),
                source="Discord command",
                guild_id=guild_id,
                guild_name=interaction.guild.name if interaction.guild else str(guild_id),
                channel_id=channel_id,
                channel_name=interaction.user.voice.channel.name,
            )
            await interaction.followup.send(f"🎵 Playing **{state.title}**")
        except Exception as exc:
            await interaction.followup.send(f"Could not play that video: {exc}", ephemeral=True)

    @app_commands.command(name="settings", description="Show or change this server's default volume and normalize")
    @app_commands.describe(
        volume="Default volume percent (0–300)",
        normalize="Even out loudness by default",
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        volume: Optional[app_commands.Range[int, 0, 300]] = None,
        normalize: bool | None = None,
    ) -> None:
        if interaction.guild_id is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        if volume is None and normalize is None:
            defaults = self.bot.preferences.get(interaction.guild_id)
            await interaction.response.send_message(
                f"Server defaults: {self._describe_defaults(defaults)}.", ephemeral=True
            )
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need the Manage Server permission to change the defaults.", ephemeral=True
            )
            return
        defaults = self.bot.preferences.update(
            interaction.guild_id,
            volume=None if volume is None else volume / 100,
            normalize=normalize,
        )
        await interaction.response.send_message(f"Server defaults updated: {self._describe_defaults(defaults)}.")

    @app_commands.command(name="stop", description="Stop the current sound")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        stopped = self.bot.playback.stop(interaction.guild_id)
        await interaction.response.send_message("Stopped." if stopped else "Nothing is playing.")

    @app_commands.command(name="leave", description="Disconnect the bot from voice")
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        left = await self.bot.playback.leave(interaction.guild_id)
        await interaction.response.send_message("Disconnected." if left else "I am not in voice.")
