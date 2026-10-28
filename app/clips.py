from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path


CLIP_EXTENSIONS = {".mp3", ".wav"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,150}$")
_SAFE_STEM_FILE = re.compile(r"^[A-Za-z0-9_-]{1,60}\.(mp3|wav)$")
_EDIT_SUFFIX = re.compile(r" \(edit(?: \d+)?\)$")
_MANIFEST = "manifest.json"

# A clip's per-speaker tracks: the manifest, and file name -> encoded audio.
Stems = tuple[dict, dict[str, bytes]]


class ClipStore:
    """Saved recording clips in a single directory.

    Each clip is an audio file. Clips saved with per-speaker tracks ("stems")
    also have a sibling ``<clip name>.stems`` folder holding one audio file per
    speaker and a manifest, so they can be remixed later.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def save(self, data: bytes, extension: str, label: str, stems: Stems | None = None) -> dict[str, object]:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H-%M-%S")
        slug = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 _-]+", "", label)).strip()[:40] or "clip"
        return self._write(self._unused(f"{stamp} {slug}", extension), data, stems)

    def save_edit(
        self, original: str, data: bytes, extension: str, stems: Stems | None = None, *, replace: bool = False
    ) -> dict[str, object]:
        """Save an edited version of a clip, either alongside it or in its place."""
        source = self.path(original)
        if source is None:
            raise FileNotFoundError(original)
        if replace:
            target = source.with_suffix(f".{extension}")
            shutil.rmtree(self._stems_dir(source), ignore_errors=True)
            if target != source:
                source.unlink()
            return self._write(target, data, stems)
        base = _EDIT_SUFFIX.sub("", source.stem)
        return self._write(self._unused(f"{base} (edit)", extension, numbered=f"{base} (edit {{}})"), data, stems)

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
        if not _SAFE_NAME.match(name) or ".." in name or Path(name).suffix.lower() not in CLIP_EXTENSIONS:
            return None
        path = self.directory / name
        return path if path.is_file() else None

    def manifest(self, name: str) -> dict | None:
        path = self.path(name)
        manifest = self._stems_dir(path) / _MANIFEST if path else None
        if manifest is None or not manifest.is_file():
            return None
        return json.loads(manifest.read_text(encoding="utf-8"))

    def stem_path(self, name: str, file: str) -> Path | None:
        path = self.path(name)
        if path is None or not _SAFE_STEM_FILE.match(file):
            return None
        stem = self._stems_dir(path) / file
        return stem if stem.is_file() else None

    def delete(self, name: str) -> bool:
        path = self.path(name)
        if path is None:
            return False
        shutil.rmtree(self._stems_dir(path), ignore_errors=True)
        path.unlink()
        return True

    def _unused(self, base: str, extension: str, numbered: str | None = None) -> Path:
        numbered = numbered or f"{base} ({{}})"
        candidate, counter = base, 2
        while any((self.directory / f"{candidate}{suffix}").exists() for suffix in (*CLIP_EXTENSIONS, ".stems")):
            candidate = numbered.format(counter)
            counter += 1
        return self.directory / f"{candidate}.{extension}"

    def _write(self, path: Path, data: bytes, stems: Stems | None) -> dict[str, object]:
        self.directory.mkdir(parents=True, exist_ok=True)
        if stems is not None:
            manifest, files = stems
            folder = self._stems_dir(path)
            folder.mkdir(exist_ok=True)
            for file, content in files.items():
                if not _SAFE_STEM_FILE.match(file):
                    raise ValueError(f"Unsafe stem file name: {file}")
                (folder / file).write_bytes(content)
            (folder / _MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        path.write_bytes(data)
        return self._describe(path)

    @staticmethod
    def _stems_dir(path: Path) -> Path:
        return path.with_name(f"{path.stem}.stems")

    def _describe(self, path: Path) -> dict[str, object]:
        stat = path.stat()
        return {
            "name": path.name,
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "editable_levels": (self._stems_dir(path) / _MANIFEST).is_file(),
        }
