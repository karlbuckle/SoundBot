from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path


CLIP_EXTENSIONS = {".mp3", ".wav"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,150}$")
_SAFE_STEM_FILE = re.compile(r"^[A-Za-z0-9_-]{1,60}\.(mp3|wav)$")
_EDIT_SUFFIX = re.compile(r" \(edit(?: \d+)?\)$")
_MANIFEST = "manifest.json"
_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2} ")
_SUBFOLDER_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,60}$")
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def clean_subfolder(value: str) -> str:
    """Validate a library subfolder like "clips" or "clips/funny" (no "..", max 3 levels)."""
    parts = [part.strip() for part in value.replace("\\", "/").split("/") if part.strip()]
    if len(parts) > 3 or not all(_SUBFOLDER_PART.match(part) for part in parts):
        raise ValueError("Use up to three folder names of letters, numbers, spaces, - or _, separated by /.")
    return "/".join(parts)


def clean_sound_name(value: str) -> str:
    """A filesystem-safe sound name; the library uses the file name as the sound's name."""
    return re.sub(r"\s+", " ", _UNSAFE_FILENAME.sub(" ", value)).strip(" .")[:80]


def is_writable(directory: Path) -> bool:
    """Whether files can be created in a folder (or the nearest existing parent it would be made in)."""
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe.is_dir() and os.access(probe, os.W_OK)

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

    def export(self, name: str, directory: Path, sound_name: str = "") -> Path:
        """Copy a clip into a sound library folder, never overwriting an existing sound."""
        source = self.path(name)
        if source is None:
            raise FileNotFoundError(name)
        stem = (
            clean_sound_name(sound_name)
            or clean_sound_name(_TIMESTAMP_PREFIX.sub("", source.stem))
            or "clip"
        )
        directory.mkdir(parents=True, exist_ok=True)
        extension = source.suffix.lower()
        target, counter = directory / f"{stem}{extension}", 2
        while target.exists():
            target = directory / f"{stem} ({counter}){extension}"
            counter += 1
        # copyfile (not copy2) so the sound counts as newly added when sorting.
        shutil.copyfile(source, target)
        return target

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
