from __future__ import annotations

import asyncio
import importlib.metadata
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.bot import SoundboardBot
from app.config import Settings
from app.library import AudioLibrary
from app.stats import UsageStats


settings = Settings.from_env()
library = AudioLibrary(settings.audio_directories)
stats = UsageStats(settings.stats_database)
bot = SoundboardBot(library, stats)
static_dir = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task: asyncio.Task[None] | None = None
    if settings.discord_token:
        bot_task = asyncio.create_task(bot.start(settings.discord_token))
    else:
        print("DISCORD_TOKEN is empty; the web UI is running without Discord connectivity.")
    yield
    if not bot.is_closed():
        await bot.close()
    if bot_task:
        await asyncio.gather(bot_task, return_exceptions=True)


app = FastAPI(title="Discord Soundboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def require_api_token(x_api_key: str | None = Header(default=None)) -> None:
    if settings.web_api_token and x_api_key != settings.web_api_token:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


class PlayRequest(BaseModel):
    guild_id: int
    channel_id: int
    client_id: str = Field(default="web-ui", max_length=100)
    requested_by: str = Field(default="Web UI", max_length=80)
    volume: float = Field(default=1.0, ge=0.0, le=3.0)
    normalize: bool = False


class SoundPlayRequest(PlayRequest):
    sound_id: str


class YouTubePlayRequest(PlayRequest):
    url: str


class GuildRequest(BaseModel):
    guild_id: int


class VolumeRequest(BaseModel):
    guild_id: int
    volume: float = Field(ge=0.0, le=3.0)


class NormalizeRequest(BaseModel):
    guild_id: int
    normalize: bool


class SeekRequest(BaseModel):
    guild_id: int
    position_seconds: float = Field(ge=0.0)


def destination_names(guild_id: int, channel_id: int) -> tuple[str, str]:
    guild = bot.get_guild(guild_id)
    if guild is None:
        return str(guild_id), str(channel_id)
    channel = guild.get_channel(channel_id)
    return guild.name, channel.name if channel else str(channel_id)


@lru_cache(maxsize=None)
def command_version(command: str, argument: str) -> dict[str, str]:
    path = shutil.which(command)
    if not path:
        return {"name": command, "version": "Not installed", "status": "unavailable", "detail": "Not found on PATH"}
    try:
        result = subprocess.run(
            [path, argument], capture_output=True, text=True, timeout=3, check=False
        )
        output = (result.stdout or result.stderr).splitlines()[0].strip()
        return {"name": command, "version": output, "status": "available", "detail": path}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"name": command, "version": "Unknown", "status": "error", "detail": str(exc)}


def package_versions() -> list[dict[str, str]]:
    packages = ("discord.py", "fastapi", "uvicorn", "yt-dlp", "httpx", "pytest")
    result = []
    for package in packages:
        try:
            version = importlib.metadata.version(package)
            status = "installed"
        except importlib.metadata.PackageNotFoundError:
            version = "Not installed"
            status = "unavailable"
        result.append({"name": package, "version": version, "status": status})
    return result


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "discord_ready": bot.is_ready(),
        "configured_directories": [str(path) for path in settings.audio_directories],
    }


@app.get("/api/sounds", dependencies=[Depends(require_api_token)])
async def sounds() -> list[dict[str, str]]:
    return [sound.public_dict() for sound in library.all()]


@app.get("/api/system", dependencies=[Depends(require_api_token)])
async def system_status() -> dict[str, object]:
    directories = [
        {"path": str(path), "status": "available" if path.is_dir() else "unavailable"}
        for path in settings.audio_directories
    ]
    components = [
        {"name": "Web API", "version": "FastAPI", "status": "operational", "detail": "Serving requests"},
        {"name": "Python", "version": sys.version.split()[0], "status": "available", "detail": sys.executable},
        {
            "name": "Discord",
            "version": importlib.metadata.version("discord.py"),
            "status": "connected" if bot.is_ready() else "offline",
            "detail": f"{len(bot.guilds)} connected server(s)" if bot.is_ready() else "Bot is not connected",
        },
        command_version("ffmpeg", "-version"),
        command_version("node", "--version"),
        {
            "name": "Usage database",
            "version": "SQLite",
            "status": "available",
            "detail": str(settings.stats_database),
        },
    ]
    return {"components": components, "packages": package_versions(), "directories": directories}


@app.get("/api/stats", dependencies=[Depends(require_api_token)])
async def usage_stats() -> dict[str, object]:
    return stats.summary()


@app.get("/api/discord/guilds", dependencies=[Depends(require_api_token)])
async def guilds() -> list[dict[str, object]]:
    return [
        {
            "id": str(guild.id),
            "name": guild.name,
            "channels": [
                {"id": str(channel.id), "name": channel.name}
                for channel in guild.voice_channels
            ],
            "status": bot.playback.status(guild.id),
        }
        for guild in bot.guilds
    ]


@app.post("/api/play/sound", dependencies=[Depends(require_api_token)])
async def play_sound(request: SoundPlayRequest) -> dict[str, str]:
    sound = library.get(request.sound_id)
    if sound is None:
        raise HTTPException(status_code=404, detail="Sound not found.")
    try:
        state = await bot.playback.play_file(
            request.guild_id,
            request.channel_id,
            sound.path,
            sound.name,
            volume=request.volume,
            normalize=request.normalize,
        )
        guild_name, channel_name = destination_names(request.guild_id, request.channel_id)
        stats.record(
            kind="sound",
            item_id=sound.id,
            title=sound.name,
            actor_id=f"web:{request.client_id or 'web-ui'}",
            actor_name=request.requested_by.strip() or "Web UI",
            source="Web UI",
            guild_id=request.guild_id,
            guild_name=guild_name,
            channel_id=request.channel_id,
            channel_name=channel_name,
        )
        return bot.playback.status(request.guild_id) or {
            "kind": state.kind,
            "title": state.title,
            "source": state.source,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/play/youtube", dependencies=[Depends(require_api_token)])
async def play_youtube(request: YouTubePlayRequest) -> dict[str, str]:
    try:
        state = await bot.playback.play_youtube(
            request.guild_id,
            request.channel_id,
            request.url,
            volume=request.volume,
            normalize=request.normalize,
        )
        guild_name, channel_name = destination_names(request.guild_id, request.channel_id)
        stats.record(
            kind="youtube",
            item_id=state.source,
            title=state.title,
            actor_id=f"web:{request.client_id or 'web-ui'}",
            actor_name=request.requested_by.strip() or "Web UI",
            source="Web UI",
            guild_id=request.guild_id,
            guild_name=guild_name,
            channel_id=request.channel_id,
            channel_name=channel_name,
        )
        return bot.playback.status(request.guild_id) or {
            "kind": state.kind,
            "title": state.title,
            "source": state.source,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/play/stop", dependencies=[Depends(require_api_token)])
async def stop(request: GuildRequest) -> dict[str, bool]:
    return {"stopped": bot.playback.stop(request.guild_id)}


@app.post("/api/play/leave", dependencies=[Depends(require_api_token)])
async def leave(request: GuildRequest) -> dict[str, bool]:
    return {"disconnected": await bot.playback.leave(request.guild_id)}


@app.get("/api/play/status/{guild_id}", dependencies=[Depends(require_api_token)])
async def playback_status(guild_id: int) -> dict[str, object] | None:
    return bot.playback.status(guild_id)


@app.post("/api/play/volume", dependencies=[Depends(require_api_token)])
async def update_volume(request: VolumeRequest) -> dict[str, object]:
    try:
        state = await bot.playback.set_volume(request.guild_id, request.volume)
        return bot.playback.status(request.guild_id) or {
            "kind": state.kind,
            "title": state.title,
            "source": state.source,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/play/normalize", dependencies=[Depends(require_api_token)])
async def update_normalize(request: NormalizeRequest) -> dict[str, object]:
    try:
        state = await bot.playback.set_normalize(request.guild_id, request.normalize)
        return bot.playback.status(request.guild_id) or {
            "kind": state.kind,
            "title": state.title,
            "source": state.source,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/play/seek", dependencies=[Depends(require_api_token)])
async def update_seek(request: SeekRequest) -> dict[str, object]:
    try:
        state = await bot.playback.seek(request.guild_id, request.position_seconds)
        return bot.playback.status(request.guild_id) or {
            "kind": state.kind,
            "title": state.title,
            "source": state.source,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
