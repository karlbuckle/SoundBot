from __future__ import annotations

import hashlib
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

    def public_dict(self) -> dict[str, str]:
        data = asdict(self)
        data.pop("path")
        return data


class AudioLibrary:
    def __init__(self, directories: tuple[Path, ...]):
        self.directories = directories
        self._sounds: dict[str, Sound] = {}

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
                )
        self._sounds = sounds
        return list(sounds.values())

    def all(self) -> list[Sound]:
        return self.scan()

    def get(self, sound_id: str) -> Sound | None:
        self.scan()
        return self._sounds.get(sound_id)

    def search(self, query: str, limit: int = 25) -> list[Sound]:
        query = query.casefold().strip()
        sounds = self.scan()
        if not query:
            return sounds[:limit]
        return [
            sound
            for sound in sounds
            if query in sound.name.casefold()
            or query in sound.relative_path.casefold()
            or query in sound.collection.casefold()
        ][:limit]
