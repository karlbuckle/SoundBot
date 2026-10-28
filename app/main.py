from __future__ import annotations

import asyncio
import importlib.metadata
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.bot import SoundboardBot
from app.clips import ClipStore
from app.config import Settings
from app.library import AudioLibrary
from app.preferences import MAX_SPEAKER_GAIN, MAX_VOLUME, GuildPreferences
from app.stats import UsageStats


settings = Settings.from_env()
library = AudioLibrary(settings.audio_directories)
stats = UsageStats(settings.stats_database)
preferences = GuildPreferences(settings.stats_database)
clips = ClipStore(settings.clips_directory)
bot = SoundboardBot(
    library,
    stats,
    preferences,
    clips,
    enable_recording=settings.enable_recording,
    recording_seconds=settings.recording_seconds,
)
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


@app.middleware("http")
async def revalidate_page_files(request: Request, call_next):
    # Without this, browsers may keep using an old app.js after an update while
    # loading the new page, which breaks newer features until a hard refresh.
    # Clip audio needs the same treatment: "Replace original" keeps the URL.
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith(("/static/", "/api/clips/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


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


class DefaultsRequest(BaseModel):
    volume: float | None = Field(default=None, ge=0.0, le=MAX_VOLUME)
    normalize: bool | None = None


class RecordingStartRequest(BaseModel):
    guild_id: int
    channel_id: int


class SpeakerGainRequest(BaseModel):
    guild_id: int
    speaker_key: str = Field(min_length=1, max_length=40)
    gain: float = Field(ge=0.0, le=MAX_SPEAKER_GAIN)


class ClipEditRequest(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    gains: dict[str, float] = Field(default_factory=dict)
    replace: bool = False


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
        "recording_available": bot.recording.available,
        "configured_directories": [str(path) for path in settings.audio_directories],
    }


@app.get("/api/sounds", dependencies=[Depends(require_api_token)])
async def sounds(refresh: bool = False) -> list[dict[str, object]]:
    plays = stats.play_counts()
    items = await asyncio.to_thread(library.all, refresh)
    return [{**sound.public_dict(), "plays": plays.get(sound.id, 0)} for sound in items]


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
        {
            "name": "Voice recording",
            "version": _package_version("discord-ext-voice-recv"),
            "status": "available" if bot.recording.available else "offline",
            "detail": str(settings.clips_directory) if bot.recording.available else bot.recording.unavailable_reason,
        },
    ]
    return {"components": components, "packages": package_versions(), "directories": directories}


def _package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "Not installed"


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
            "defaults": preferences.get(guild.id).public_dict(),
        }
        for guild in bot.guilds
    ]


@app.post("/api/discord/guilds/{guild_id}/defaults", dependencies=[Depends(require_api_token)])
async def update_guild_defaults(guild_id: int, request: DefaultsRequest) -> dict[str, float | bool]:
    if bot.get_guild(guild_id) is None:
        raise HTTPException(status_code=404, detail="The bot is not connected to that Discord server.")
    return preferences.update(guild_id, volume=request.volume, normalize=request.normalize).public_dict()


@app.post("/api/play/sound", dependencies=[Depends(require_api_token)])
async def play_sound(request: SoundPlayRequest) -> dict[str, object]:
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
async def play_youtube(request: YouTubePlayRequest) -> dict[str, object]:
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
    return {"disconnected": await bot.leave_voice(request.guild_id)}


@app.get("/api/recording/{guild_id}", dependencies=[Depends(require_api_token)])
async def recording_status(guild_id: int) -> dict[str, object]:
    return bot.recording.status(guild_id)


@app.post("/api/recording/start", dependencies=[Depends(require_api_token)])
async def start_recording(request: RecordingStartRequest) -> dict[str, object]:
    try:
        return await bot.recording.start(request.guild_id, request.channel_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/recording/stop", dependencies=[Depends(require_api_token)])
async def stop_recording(request: GuildRequest) -> dict[str, object]:
    return await bot.recording.stop(request.guild_id)


@app.post("/api/recording/save", dependencies=[Depends(require_api_token)])
async def save_recording(request: GuildRequest) -> dict[str, object]:
    try:
        return await bot.recording.save_clip(request.guild_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/recording/gain", dependencies=[Depends(require_api_token)])
async def set_speaker_gain(request: SpeakerGainRequest) -> dict[str, object]:
    preferences.set_recording_gain(request.guild_id, request.speaker_key, request.gain)
    return bot.recording.status(request.guild_id)


@app.get("/api/clips", dependencies=[Depends(require_api_token)])
async def list_clips() -> list[dict[str, object]]:
    return clips.list()


@app.get("/api/clips/{name}", dependencies=[Depends(require_api_token)])
async def get_clip(name: str) -> FileResponse:
    path = clips.path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    return FileResponse(path, filename=path.name)


@app.get("/api/clips/{name}/stems", dependencies=[Depends(require_api_token)])
async def clip_stems(name: str) -> dict[str, object]:
    """Per-speaker tracks for the editor. Older clips have none and edit as a single track."""
    if clips.path(name) is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    return clips.manifest(name) or {"speakers": []}


@app.get("/api/clips/{name}/stems/{file}", dependencies=[Depends(require_api_token)])
async def clip_stem(name: str, file: str) -> FileResponse:
    path = clips.stem_path(name, file)
    if path is None:
        raise HTTPException(status_code=404, detail="Track not found.")
    return FileResponse(path)


@app.post("/api/clips/{name}/edit", dependencies=[Depends(require_api_token)])
async def edit_clip(name: str, request: ClipEditRequest) -> dict[str, object]:
    try:
        from app.clip_editor import edit_clip as render_edit
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Clip editing needs the recording image.") from exc
    if clips.path(name) is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    try:
        return await asyncio.to_thread(
            render_edit, clips, name, request.start, request.end, request.gains, replace=request.replace
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/clips/{name}", dependencies=[Depends(require_api_token)])
async def delete_clip(name: str) -> dict[str, bool]:
    if not clips.delete(name):
        raise HTTPException(status_code=404, detail="Clip not found.")
    return {"deleted": True}


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
