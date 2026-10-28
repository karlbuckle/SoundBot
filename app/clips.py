from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path


CLIP_EXTENSIONS = {".mp3", ".wav"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,150}$")


class ClipStore:
    """Saved recording clips in a single directory."""

    def __init__(self, directory: Path):
        self.directory = directory

    def save(self, data: bytes, extension: str, label: str) -> dict[str, object]:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H-%M-%S")
        slug = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 _-]+", "", label)).strip()[:40] or "clip"
        path = self.directory / f"{stamp} {slug}.{extension}"
        counter = 2
        while path.exists():
            path = self.directory / f"{stamp} {slug} ({counter}).{extension}"
            counter += 1
        path.write_bytes(data)
        return self._describe(path)

    def list(self) -> list[dict[str, object]]:
        if not self.directory.is_dir():
            return []
        clips = [
            path for path in self.directory.iterdir()
            if path.is_file() and path.suffix.lower() in CLIP_EXTENSIONS
        ]
        clips.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return [self._describe(path) for path in clips]

    def path(self, name: str) -> Path | None:
        """Resolve a clip name, refusing anything outside the clips directory."""
        if not _SAFE_NAME.match(name) or Path(name).suffix.lower() not in CLIP_EXTENSIONS:
            return None
        path = self.directory / name
        return path if path.is_file() else None

    def delete(self, name: str) -> bool:
        path = self.path(name)
        if path is None:
            return False
        path.unlink()
        return True

    @staticmethod
    def _describe(path: Path) -> dict[str, object]:
        stat = path.stat()
        return {
            "name": path.name,
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }
