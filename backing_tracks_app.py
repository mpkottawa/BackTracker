from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(os.environ.get("BACKTRACKER_ROOT", Path.home() / "BackTracker"))
SOURCE_ROOT = ROOT / "source albums"
FINISHED_ROOT = ROOT / "finished"
PROCESSING_ROOT = ROOT / "processing"
STATE_FILE = ROOT / ".backing-tracks-state.json"
AUDACITY = Path(os.environ.get("AUDACITY_EXE", r"C:\Program Files\Audacity\Audacity.exe"))
UVR = Path(os.environ.get(
    "UVR_EXE",
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "Programs" / "Ultimate Vocal Remover" / "UVR.exe",
))
UVR_HELPER = Path(__file__).with_name("run_uvr_album.ps1")
AUDACITY_CONFIG = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "audacity" / "audacity.cfg"
FFMPEG = UVR.parent / "ffmpeg.exe"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".aiff", ".aif"}


@dataclass(frozen=True)
class Album:
    folder: Path
    artist: str
    name: str
    tracks: tuple[Path, ...]

    @property
    def key(self) -> str:
        return str(self.folder.resolve()).casefold()

    @property
    def output_folder(self) -> Path:
        return FINISHED_ROOT / self.name


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"version": 1, "albums": {}}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def fingerprint(tracks: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for track in tracks:
        stat = track.stat()
        digest.update(track.name.casefold().encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
    return digest.hexdigest()


def discover_albums() -> list[Album]:
    if not SOURCE_ROOT.exists():
        return []
    albums: list[Album] = []
    for folder in sorted((p for p in SOURCE_ROOT.rglob("*") if p.is_dir()), key=lambda p: str(p).casefold()):
        tracks = tuple(sorted(
            (p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() in AUDIO_EXTENSIONS),
            key=lambda p: p.name.casefold(),
        ))
        if not tracks:
            continue
        relative = folder.relative_to(SOURCE_ROOT)
        artist = relative.parts[-2] if len(relative.parts) > 1 else "Unknown Artist"
        albums.append(Album(folder, artist, folder.name, tracks))
    return albums


def final_files(album: Album) -> dict[str, list[Path]]:
    root = album.output_folder
    return {
        "backing": sorted((root / "backing tracks").glob("*.mp3")),
        "guitar": sorted((root / "guitar tracks").glob("*.mp3")),
        "bass": sorted((root / "stems" / "bass").glob("*.mp3")),
        "drums": sorted((root / "stems" / "drums").glob("*.mp3")),
        "vocals": sorted((root / "stems" / "vocals").glob("*.mp3")),
    }


def output_is_complete(album: Album) -> bool:
    expected = len(album.tracks)
    groups = final_files(album)
    return all(len(files) == expected and all(p.stat().st_size > 0 for p in files) for files in groups.values())


def reconcile(state: dict, albums: list[Album]) -> None:
    records = state.setdefault("albums", {})
    changed = False
    for album in albums:
        record = records.get(album.key, {})
        if output_is_complete(album):
            new_record = {
                "status": "complete",
                "source": str(album.folder),
                "output": str(album.output_folder),
                "track_count": len(album.tracks),
                "fingerprint": fingerprint(album.tracks),
                "completed_at": record.get("completed_at", utc_now()),
            }
            if record != new_record:
                records[album.key] = new_record
                changed = True
    if changed:
        atomic_json(STATE_FILE, state)


def pending_albums(state: dict, albums: list[Album]) -> list[Album]:
    pending: list[Album] = []
    for album in albums:
        record = state.get("albums", {}).get(album.key, {})
        if record.get("status") == "complete" or output_is_complete(album):
            continue
        pending.append(album)
    return pending


def print_scan(albums: list[Album], pending: list[Album]) -> None:
    pending_keys = {album.key for album in pending}
    print(f"Found {len(albums)} album(s) in {SOURCE_ROOT}")
    for album in albums:
        status = "PENDING" if album.key in pending_keys else "SKIP (already complete)"
        print(f"[{status}] {album.artist} / {album.name} - {len(album.tracks)} tracks")


def set_audacity_pipe(enabled: bool) -> str:
    text = AUDACITY_CONFIG.read_text(encoding="utf-8")
    match = re.search(r"(?m)^mod-script-pipe=(\d+)$", text)
    if not match:
        raise RuntimeError("Audacity scripting-module preference was not found")
    old = match.group(1)
    value = "1" if enabled else old
    if enabled and old != "1":
        text = text[:match.start(1)] + value + text[match.end(1):]
        AUDACITY_CONFIG.write_text(text, encoding="utf-8")
    return old


def restore_audacity_pipe(old: str) -> None:
    text = AUDACITY_CONFIG.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^(mod-script-pipe=)\d+$", rf"\g<1>{old}", text, count=1)
    AUDACITY_CONFIG.write_text(text, encoding="utf-8")


def audacity_command(to_pipe, from_pipe, text: str) -> None:
    to_pipe.write(text + "\r\n\0")
    to_pipe.flush()
    lines: list[str] = []
    while True:
        line = from_pipe.readline()
        if not line:
            raise RuntimeError(f"Audacity closed its pipe after {text}")
        if line == "\n" and lines:
            break
        lines.append(line.rstrip())
    response = "\n".join(lines)
    if "BatchCommand finished: OK" not in response:
        raise RuntimeError(f"Audacity failed: {text}\n{response}")


def stem_groups(stems: Path) -> dict[str, list[Path]]:
    return {name: sorted(stems.glob(f"*_({name}).mp3")) for name in ("Bass", "Drums", "Vocals", "Guitar", "Other", "Piano")}


def mix_with_audacity(album: Album, stems: Path) -> None:
    groups = stem_groups(stems)
    count = len(album.tracks)
    if any(len(files) != count for files in groups.values()):
        raise RuntimeError("UVR stem counts are inconsistent")
    subprocess.run(["taskkill", "/IM", "Audacity.exe", "/F"], capture_output=True)
    time.sleep(2)
    old = set_audacity_pipe(True)
    process = subprocess.Popen([str(AUDACITY)])
    try:
        time.sleep(8)
        output = album.output_folder / "backing tracks"
        output.mkdir(parents=True, exist_ok=True)
        by_key = {re.sub(r"_\(Bass\)\.mp3$", "", p.name): p for p in groups["Bass"]}
        with open(r"\\.\pipe\ToSrvPipe", "w", encoding="utf-8", buffering=1) as to_pipe, open(
            r"\\.\pipe\FromSrvPipe", "r", encoding="utf-8", buffering=1
        ) as from_pipe:
            for number, key in enumerate(sorted(by_key), 1):
                audacity_command(to_pipe, from_pipe, "SelectAll:")
                audacity_command(to_pipe, from_pipe, "RemoveTracks:")
                for name in ("Bass", "Drums", "Vocals"):
                    audacity_command(to_pipe, from_pipe, f'Import2: Filename="{stems / (key + "_(" + name + ").mp3")}"')
                audacity_command(to_pipe, from_pipe, "SelectAll:")
                audacity_command(to_pipe, from_pipe, "MixAndRender:")
                title = re.sub(r"^\d+_", "", key)
                destination = output / f"{title} - Backing Track.mp3"
                audacity_command(to_pipe, from_pipe, f'Export2: Filename="{destination}" NumChannels=2')
                print(f"  Audacity {number}/{count}: {destination.name}", flush=True)
    finally:
        process.terminate()
        try: process.wait(5)
        except subprocess.TimeoutExpired: process.kill()
        restore_audacity_pipe(old)


def organize_and_validate(album: Album, stems: Path) -> None:
    groups = stem_groups(stems)
    destinations = {"Bass": album.output_folder / "stems" / "bass", "Drums": album.output_folder / "stems" / "drums", "Vocals": album.output_folder / "stems" / "vocals", "Guitar": album.output_folder / "guitar tracks"}
    for name, destination in destinations.items():
        destination.mkdir(parents=True, exist_ok=True)
        for source in groups[name]:
            source.replace(destination / re.sub(r"^\d+_", "", source.name))
    for name in ("Other", "Piano"):
        for source in groups[name]: source.unlink()
    retained = [p for files in final_files(album).values() for p in files]
    if len(retained) != len(album.tracks) * 5:
        raise RuntimeError(f"Expected {len(album.tracks) * 5} retained files, found {len(retained)}")
    for number, path in enumerate(retained, 1):
        result = subprocess.run([str(FFMPEG), "-v", "error", "-i", str(path), "-f", "null", "NUL"], capture_output=True)
        if result.returncode:
            raise RuntimeError(f"Validation failed: {path}")


def process_album(album: Album, state: dict) -> None:
    stems = PROCESSING_ROOT / album.name / "stems"
    stems.mkdir(parents=True, exist_ok=True)
    record = state.setdefault("albums", {}).setdefault(album.key, {})
    record.update({"status": "processing", "source": str(album.folder), "started_at": utc_now()})
    atomic_json(STATE_FILE, state)
    print(f"Processing {album.artist} / {album.name} ({len(album.tracks)} tracks)", flush=True)
    existing = [p for p in stems.glob("*.mp3") if p.stat().st_size > 0]
    if len(existing) == len(album.tracks) * 6 and not list(stems.glob("*.wav")):
        print(f"  Reusing {len(existing)} completed UVR stems", flush=True)
    else:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(UVR_HELPER), "-SourceFolder", str(album.folder), "-OutputFolder", str(stems), "-TrackCount", str(len(album.tracks))], check=True)
    mix_with_audacity(album, stems)
    organize_and_validate(album, stems)
    record.update({"status": "complete", "output": str(album.output_folder), "track_count": len(album.tracks), "fingerprint": fingerprint(album.tracks), "completed_at": utc_now()})
    atomic_json(STATE_FILE, state)
    print(f"COMPLETE: {album.name}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and queue source albums without repeating completed work.")
    parser.add_argument("--scan", action="store_true", help="Scan once and print album status (default).")
    parser.add_argument("--watch", action="store_true", help="Continuously watch for newly added albums.")
    parser.add_argument("--process", action="store_true", help="Process every pending album, then exit.")
    parser.add_argument("--process-source", action="store_true", help="Process everything in source albums; requires an empty finished folder.")
    parser.add_argument("--interval", type=int, default=60, help="Watch interval in seconds.")
    args = parser.parse_args()

    for required in (ROOT, SOURCE_ROOT):
        required.mkdir(parents=True, exist_ok=True)
    state = load_state()

    while True:
        albums = discover_albums()
        reconcile(state, albums)
        state = load_state()
        pending = pending_albums(state, albums)
        print_scan(albums, pending)
        if args.process_source:
            finished_items = list(FINISHED_ROOT.iterdir()) if FINISHED_ROOT.exists() else []
            if finished_items:
                print(f"STOPPED: {FINISHED_ROOT} must be empty before starting a new batch.", file=sys.stderr)
                print("Move the completed output folders elsewhere, then run the launcher again.", file=sys.stderr)
                return 2
            if not albums:
                print(f"Nothing to process in {SOURCE_ROOT}")
                return 0
            pending = albums
        if args.process or args.process_source:
            for album in pending:
                try:
                    process_album(album, state)
                except Exception as error:
                    record = state.setdefault("albums", {}).setdefault(album.key, {})
                    record.update({"status": "error", "source": str(album.folder), "error": str(error), "failed_at": utc_now()})
                    atomic_json(STATE_FILE, state)
                    print(f"ERROR: {album.name}: {error}", file=sys.stderr, flush=True)
            return 0 if not any(state.get("albums", {}).get(a.key, {}).get("status") == "error" for a in pending) else 1
        if not args.watch:
            return 0
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
