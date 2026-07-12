from __future__ import annotations

import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from backing_tracks_app import AUDACITY, FFMPEG, FINISHED_ROOT, PROCESSING_ROOT, audacity_command, restore_audacity_pipe, set_audacity_pipe


DEFAULT_INPUT = PROCESSING_ROOT
DEFAULT_OUTPUT = FINISHED_ROOT
STEMS = ("Bass", "Drums", "Guitar", "Vocals")


def natural_key(value: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", value))


def stem_key(path: Path, stem: str) -> str:
    suffix = f"_({stem}).mp3"
    return path.name[: -len(suffix)]


def display_name(key: str) -> str:
    return re.sub(r"^\d+_", "", key)


def album_folders(root: Path) -> list[Path]:
    candidates = [root, *[p for p in root.rglob("*") if p.is_dir()]]
    return sorted((p for p in candidates if any(p.glob("*_(Bass).mp3"))), key=lambda p: str(p).casefold())


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Audacity Backing Tracks")
        self.geometry("820x560")
        self.minsize(700, 460)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.input_var = tk.StringVar(value=str(DEFAULT_INPUT))
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
        self.move_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Choose folders, then click Start.")
        self._build()
        self.after(100, self._poll)

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Audacity Backing Tracks", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Mix bass + drums + vocals and organize finished album folders.").pack(anchor="w", pady=(0, 14))
        self._folder_row(frame, "Processing folder", self.input_var, self._choose_input)
        self._folder_row(frame, "Finished folder", self.output_var, self._choose_output)
        ttk.Checkbutton(frame, text="Move retained stems to Finished and delete temporary Piano/Other after validation", variable=self.move_var).pack(anchor="w", pady=(8, 10))
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 10))
        self.start_button = ttk.Button(controls, text="Start", command=self._start)
        self.start_button.pack(side="left")
        ttk.Button(controls, text="Open Finished Folder", command=self._open_finished).pack(side="left", padx=8)
        self.progress = ttk.Progressbar(controls, mode="determinate")
        self.progress.pack(side="right", fill="x", expand=True, padx=(18, 0))
        ttk.Label(frame, textvariable=self.status_var).pack(anchor="w")
        self.log = tk.Text(frame, height=18, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(6, 0))

    def _folder_row(self, parent, label, variable, command) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=18).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=command).pack(side="left", padx=(8, 0))

    def _choose_input(self) -> None:
        value = filedialog.askdirectory(initialdir=self.input_var.get(), title="Choose processing folder")
        if value:
            self.input_var.set(value)

    def _choose_output(self) -> None:
        value = filedialog.askdirectory(initialdir=self.output_var.get(), title="Choose finished folder")
        if value:
            self.output_var.set(value)

    def _open_finished(self) -> None:
        path = Path(self.output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(path)])

    def _write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        source, output = Path(self.input_var.get()), Path(self.output_var.get())
        if not source.is_dir():
            messagebox.showerror("Invalid folder", "The processing folder does not exist.")
            return
        output.mkdir(parents=True, exist_ok=True)
        folders = album_folders(source)
        if not folders:
            messagebox.showinfo("Nothing found", "No album folders containing Bass stems were found.")
            return
        self.start_button.configure(state="disabled")
        self.progress["value"] = 0
        self._write(f"Found {len(folders)} album folder(s).")
        self.worker = threading.Thread(target=self._run, args=(source, output, folders, self.move_var.get()), daemon=True)
        self.worker.start()

    def _run(self, source: Path, output_root: Path, folders: list[Path], move_files: bool) -> None:
        try:
            albums = []
            total = 0
            for folder in folders:
                maps = {stem: {stem_key(p, stem): p for p in folder.glob(f"*_({stem}).mp3")} for stem in STEMS}
                keys = set(maps["Bass"])
                if not keys or any(set(maps[s]) != keys for s in STEMS):
                    raise RuntimeError(f"Mismatched Bass/Drums/Guitar/Vocals sets: {folder}")
                ordered = sorted(keys, key=natural_key)
                albums.append((folder, maps, ordered))
                total += len(ordered)
            self.messages.put(("maximum", total))

            subprocess.run(["taskkill", "/IM", "Audacity.exe", "/F"], capture_output=True)
            time.sleep(2)
            old = set_audacity_pipe(True)
            process = subprocess.Popen([str(AUDACITY)])
            done = 0
            try:
                time.sleep(8)
                with open(r"\\.\pipe\ToSrvPipe", "w", encoding="utf-8", buffering=1) as to_pipe, open(r"\\.\pipe\FromSrvPipe", "r", encoding="utf-8", buffering=1) as from_pipe:
                    for folder, maps, keys in albums:
                        relative = folder.relative_to(source)
                        album_output = output_root / relative
                        album_output.mkdir(parents=True, exist_ok=True)
                        self.messages.put(("log", f"ALBUM: {relative}"))
                        for key in keys:
                            destination = album_output / f"{display_name(key)} - Backing Track.mp3"
                            if not (destination.exists() and destination.stat().st_size > 0):
                                audacity_command(to_pipe, from_pipe, "SelectAll:")
                                audacity_command(to_pipe, from_pipe, "RemoveTracks:")
                                for stem in ("Bass", "Drums", "Vocals"):
                                    audacity_command(to_pipe, from_pipe, f'Import2: Filename="{maps[stem][key]}"')
                                audacity_command(to_pipe, from_pipe, "SelectAll:")
                                audacity_command(to_pipe, from_pipe, "MixAndRender:")
                                audacity_command(to_pipe, from_pipe, f'Export2: Filename="{destination}" NumChannels=2')
                            check = subprocess.run([str(FFMPEG), "-v", "error", "-i", str(destination), "-f", "null", "NUL"], capture_output=True)
                            if check.returncode:
                                raise RuntimeError(f"MP3 validation failed: {destination}")
                            done += 1
                            self.messages.put(("progress", done))
                            self.messages.put(("log", f"  {done}/{total} {destination.name}"))

                        for stem, subfolder in (("Bass", "bass"), ("Drums", "drums"), ("Guitar", "guitar tracks"), ("Vocals", "vocals")):
                            destination_folder = album_output / "stems" / subfolder
                            destination_folder.mkdir(parents=True, exist_ok=True)
                            for key in keys:
                                source_file = maps[stem][key]
                                target = destination_folder / f"{display_name(key)}_({stem}).mp3"
                                if target.exists():
                                    continue
                                if move_files:
                                    shutil.move(str(source_file), str(target))
                                else:
                                    shutil.copy2(source_file, target)
                        if move_files:
                            for temporary in ("Other", "Piano"):
                                for path in folder.glob(f"*_({temporary}).mp3"):
                                    path.unlink()

                    audacity_command(to_pipe, from_pipe, "SelectAll:")
                    audacity_command(to_pipe, from_pipe, "RemoveTracks:")
            finally:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                restore_audacity_pipe(old)
            self.messages.put(("done", f"Complete: created and validated {done} backing tracks."))
        except Exception as error:
            self.messages.put(("error", str(error)))

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log": self._write(str(value))
                elif kind == "maximum": self.progress["maximum"] = int(value)
                elif kind == "progress": self.progress["value"] = int(value); self.status_var.set(f"Processed {value} track(s)...")
                elif kind == "done":
                    self.status_var.set(str(value)); self._write(str(value)); self.start_button.configure(state="normal"); messagebox.showinfo("Finished", str(value))
                elif kind == "error":
                    self.status_var.set("Stopped with an error."); self._write("ERROR: " + str(value)); self.start_button.configure(state="normal"); messagebox.showerror("Processing stopped", str(value))
        except queue.Empty:
            pass
        self.after(100, self._poll)


if __name__ == "__main__":
    App().mainloop()
