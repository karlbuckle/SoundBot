"""Saving clips with per-speaker tracks, and trimming or remixing them later."""

from __future__ import annotations

from app.clips import ClipStore, Stems
from app.preferences import MAX_SPEAKER_GAIN
from app.recording import SAMPLE_RATE, Track, decode_audio, encode_clip, mix_tracks, to_int16

MIN_CLIP_SECONDS = 0.25
_STEM_BITRATE = "64k"  # speech-only tracks; the final mix uses the default 128k


def build_stems(tracks: list[Track], gains: dict[str, float]) -> Stems:
    files: dict[str, bytes] = {}
    speakers = []
    for index, track in enumerate(tracks):
        data, extension = encode_clip(to_int16(track.samples), bitrate=_STEM_BITRATE)
        file = f"track{index}.{extension}"
        files[file] = data
        speakers.append({
            "key": track.key,
            "name": track.name,
            "is_soundboard": track.is_soundboard,
            "auto_gain": track.auto_gain,
            "gain": gains.get(track.key, 1.0),
            "file": file,
        })
    duration = max((len(track.samples) for track in tracks), default=0) / SAMPLE_RATE
    manifest = {"version": 1, "sample_rate": SAMPLE_RATE, "duration": duration, "speakers": speakers}
    return manifest, files


def save_tracks(
    store: ClipStore,
    tracks: list[Track],
    gains: dict[str, float],
    *,
    label: str = "",
    edit_of: str | None = None,
    replace: bool = False,
) -> dict[str, object]:
    """Mix and save a clip, keeping each speaker's track so levels can be changed later."""
    data, extension = encode_clip(mix_tracks(tracks, gains))
    stems = build_stems(tracks, gains)
    if edit_of is not None:
        return store.save_edit(edit_of, data, extension, stems, replace=replace)
    return store.save(data, extension, label, stems)


def load_tracks(store: ClipStore, name: str) -> tuple[list[Track], dict[str, float], bool]:
    """A clip's speaker tracks and saved levels. Clips without stems load as one mixed track."""
    path = store.path(name)
    if path is None:
        raise FileNotFoundError(name)
    manifest = store.manifest(name)
    if manifest is None:
        return [Track("mix", "Mix", decode_audio(path))], {}, False
    tracks, gains = [], {}
    for speaker in manifest["speakers"]:
        stem = store.stem_path(name, speaker["file"])
        if stem is None:
            raise ValueError(f"Missing track for {speaker['name']}.")
        tracks.append(Track(speaker["key"], speaker["name"], decode_audio(stem), float(speaker["auto_gain"])))
        gains[speaker["key"]] = float(speaker["gain"])
    return tracks, gains, True


def edit_clip(
    store: ClipStore,
    name: str,
    start: float,
    end: float,
    gains: dict[str, float] | None = None,
    *,
    replace: bool = False,
) -> dict[str, object]:
    """Trim a clip to [start, end] seconds and, if it has stems, apply new per-speaker levels."""
    tracks, saved_gains, has_stems = load_tracks(store, name)
    total = max(len(track.samples) for track in tracks)
    duration = total / SAMPLE_RATE
    if start < 0 or end <= start or start >= duration:
        raise ValueError("Choose a start time before the end time, inside the clip.")
    end = min(end, duration)
    if end - start < MIN_CLIP_SECONDS:
        raise ValueError(f"Clips must be at least {MIN_CLIP_SECONDS:g} seconds long.")

    first, last = int(round(start * SAMPLE_RATE)), min(int(round(end * SAMPLE_RATE)), total)
    trimmed = [Track(track.key, track.name, track.samples[first:last], track.auto_gain) for track in tracks]
    if not has_stems:
        # Already mixed: just cut it, without re-levelling or limiting.
        data, extension = encode_clip(to_int16(trimmed[0].samples))
        return store.save_edit(name, data, extension, replace=replace)
    changes = {key: min(max(value, 0.0), MAX_SPEAKER_GAIN) for key, value in (gains or {}).items() if key in saved_gains}
    levels = {**saved_gains, **changes}
    return save_tracks(store, trimmed, levels, edit_of=name, replace=replace)
