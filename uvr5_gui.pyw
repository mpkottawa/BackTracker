from __future__ import annotations

import queue
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


DEFAULT_OUTPUT = Path(os.environ.get("BACKTRACKER_ROOT", Path.home() / "BackTracker")) / "processing"
HELPER = (Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)).with_name("run_uvr_album.ps1")
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".aiff", ".aif"}


def tracks(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() in AUDIO_EXTENSIONS), key=lambda p: p.name.casefold())


def discover(root: Path) -> list[tuple[Path, list[Path]]]:
    folders = [root, *[p for p in root.rglob("*") if p.is_dir()]]
    return [(folder, found) for folder in folders if (found := tracks(folder))]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("UVR5 Album Separator")
        self.geometry("820x550")
        self.minsize(700, 450)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
        self.status_var = tk.StringVar(value="Choose a folder containing one or more albums.")
        self._build()
        self.after(100, self._poll)

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="UVR5 Album Separator", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Separate albums into Bass, Drums, Guitar, Other, Piano, and Vocals stems.").pack(anchor="w", pady=(0, 14))
        self._folder_row(frame, "Source albums", self.source_var, self._choose_source)
        self._folder_row(frame, "Processing output", self.output_var, self._choose_output)
        ttk.Label(frame, text="UVR5 must already be configured for Demucs htdemucs_6s, All Stems, MP3, and GPU.").pack(anchor="w", pady=(8, 10))
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 10))
        self.start_button = ttk.Button(controls, text="Start Separation", command=self._start)
        self.start_button.pack(side="left")
        ttk.Button(controls, text="Open Processing Folder", command=self._open_output).pack(side="left", padx=8)
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

    def _choose_source(self) -> None:
        value = filedialog.askdirectory(title="Choose an album or parent folder")
        if value:
            self.source_var.set(value)

    def _choose_output(self) -> None:
        value = filedialog.askdirectory(initialdir=self.output_var.get(), title="Choose processing output folder")
        if value:
            self.output_var.set(value)

    def _open_output(self) -> None:
        path = Path(self.output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(path)])

    def _write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        source, output = Path(self.source_var.get()), Path(self.output_var.get())
        if not source.is_dir():
            messagebox.showerror("Invalid source", "Choose a valid source album folder.")
            return
        if not HELPER.is_file():
            messagebox.showerror("Missing helper", f"Required file not found:\n{HELPER}")
            return
        jobs = discover(source)
        if not jobs:
            messagebox.showinfo("Nothing found", "No supported audio files were found.")
            return
        output.mkdir(parents=True, exist_ok=True)
        self.start_button.configure(state="disabled")
        self.progress.configure(maximum=len(jobs), value=0)
        self._write(f"Found {len(jobs)} album folder(s).")
        self.status_var.set("Running. This window is minimized so UVR5 can receive input.")
        self.iconify()
        threading.Thread(target=self._run, args=(source, output, jobs), daemon=True).start()

    def _run(self, source: Path, output: Path, jobs: list[tuple[Path, list[Path]]]) -> None:
        try:
            for index, (folder, audio) in enumerate(jobs, 1):
                relative = Path(folder.name) if folder == source else folder.relative_to(source)
                destination = output / relative
                expected = len(audio) * 6
                completed = [p for p in destination.glob("*.mp3") if p.stat().st_size > 0]
                self.messages.put(("log", f"ALBUM {index}/{len(jobs)}: {relative} ({len(audio)} tracks)"))
                if len(completed) == expected and not list(destination.glob("*.wav")):
                    self.messages.put(("log", f"  SKIP: {expected} completed stems already exist."))
                else:
                    command = [
                        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPER),
                        "-SourceFolder", str(folder), "-OutputFolder", str(destination), "-TrackCount", str(len(audio)),
                    ]
                    result = subprocess.run(command, capture_output=True, text=True)
                    if result.returncode:
                        raise RuntimeError((result.stderr or result.stdout or "UVR5 separation failed").strip())
                    completed = [p for p in destination.glob("*.mp3") if p.stat().st_size > 0]
                    if len(completed) != expected:
                        raise RuntimeError(f"Expected {expected} completed stems in {destination}, found {len(completed)}")
                    self.messages.put(("log", f"  COMPLETE: {len(completed)} stems"))
                self.messages.put(("progress", index))
            self.messages.put(("done", f"Complete: separated {len(jobs)} album folder(s)."))
        except Exception as error:
            self.messages.put(("error", str(error)))

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log": self._write(str(value))
                elif kind == "progress": self.progress["value"] = int(value); self.status_var.set(f"Completed {value} album folder(s)...")
                elif kind == "done":
                    self.deiconify(); self.lift(); self.focus_force(); self.status_var.set(str(value)); self._write(str(value)); self.start_button.configure(state="normal"); messagebox.showinfo("Finished", str(value))
                elif kind == "error":
                    self.deiconify(); self.lift(); self.focus_force(); self.status_var.set("Stopped with an error."); self._write("ERROR: " + str(value)); self.start_button.configure(state="normal"); messagebox.showerror("Separation stopped", str(value))
        except queue.Empty:
            pass
        self.after(100, self._poll)


if __name__ == "__main__":
    App().mainloop()
