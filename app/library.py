from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path


SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


@dataclass(frozen=True)
class Sound:
    id: str
    name: str
    collection: str
    relative_path: str
    path: Path
    modified_at: float = 0.0

    def public_dict(self) -> dict[str, str | float]:
        data = asdict(self)
        data.pop("path")
        return data


class AudioLibrary:
    def __init__(self, directories: tuple[Path, ...], max_age_seconds: float = 60.0):
        self.directories = directories
        self.max_age_seconds = max_age_seconds
        self._sounds: dict[str, Sound] = {}
        self._scanned_at: float | None = None

    def scan(self) -> list[Sound]:
        sounds: dict[str, Sound] = {}
        for root in self.directories:
            if not root.is_dir():
                continue
            resolved_root = root.resolve()
            collection = root.name
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(resolved_root):
                    continue
                sound_id = hashlib.sha256(str(resolved).encode()).hexdigest()[:16]
                relative = path.relative_to(root).as_posix()
                sounds[sound_id] = Sound(
                    id=sound_id,
                    name=path.stem,
                    collection=collection,
                    relative_path=relative,
                    path=resolved,
                    modified_at=resolved.stat().st_mtime,
                )
        self._sounds = sounds
        self._scanned_at = time.monotonic()
        return list(sounds.values())

    def all(self, refresh: bool = False) -> list[Sound]:
        """Return the cached library, rescanning when asked or when it is stale."""
        stale = self._scanned_at is None or time.monotonic() - self._scanned_at > self.max_age_seconds
        if refresh or stale:
            return self.scan()
        return list(self._sounds.values())

    def get(self, sound_id: str) -> Sound | None:
        self.all()
        sound = self._sounds.get(sound_id)
        if sound is None or not sound.path.is_file():
            # The file may have been added, moved or deleted since the last scan.
            self.scan()
            sound = self._sounds.get(sound_id)
        return sound

    def search(self, query: str, limit: int = 25) -> list[Sound]:
        query = query.casefold().strip()
        sounds = self.all()
        if not query:
            return sounds[:limit]
        return [
            sound
            for sound in sounds
            if query in sound.name.casefold()
            or query in sound.relative_path.casefold()
            or query in sound.collection.casefold()
        ][:limit]
