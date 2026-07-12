# BackTracker

BackTracker is a two-step Windows tool for turning songs or albums into guitar-free backing
tracks. First it uses Ultimate Vocal Remover (UVR5) to create individual stem files. Then it uses
Audacity to mix bass, drums, and vocals into finished backing tracks while retaining guitar and
useful stem files.

## Prerequisites

Install all of these before using BackTracker:

1. **Windows 10 or Windows 11.** The UVR automation uses Windows dialogs and cannot run on macOS
   or Linux.
2. **Ultimate Vocal Remover (UVR5).** Download and install it from
   <https://ultimatevocalremover.com/>. Open UVR once and install/select a six-stem model that
   produces Bass, Drums, Guitar, Vocals, Piano, and Other.
3. **Audacity.** Download and install it from <https://www.audacityteam.org/>. In Audacity, open
   **Edit > Preferences > Modules**, set **mod-script-pipe** to **Enabled**, close Audacity, and
   reopen it once. BackTracker temporarily enables this setting when needed, but the module must
   be present in the Audacity installation.

The release executables include the BackTracker Python code. Python and PyInstaller are only
needed when building from source.

## Download and folder setup

Download both release files into the same folder:

- `UVR5 Album Separator.exe`
- `Audacity Backing Tracks.exe`
- `run_uvr_album.ps1`

Keep `run_uvr_album.ps1` beside `UVR5 Album Separator.exe`; the separator needs it to control UVR.
Create folders anywhere you like for original audio, separated stems, and finished tracks. The
apps default to `%USERPROFILE%\BackTracker`, but every folder can be selected in the interface.

If UVR or Audacity is installed somewhere unusual, set its path before starting the apps:

```powershell
$env:UVR_EXE = 'D:\Apps\UVR\UVR.exe'
$env:AUDACITY_EXE = 'D:\Apps\Audacity\Audacity.exe'
```

## Step 1: make the individual split files

1. Close UVR if it is already running.
2. Start `UVR5 Album Separator.exe`.
3. Choose a source album folder, or a parent folder containing several album folders. Supported
   inputs include MP3, WAV, FLAC, M4A, AAC, OGG, WMA, AIFF, and AIF.
4. Choose an empty processing/output folder.
5. Click **Start Separation** and do not use the mouse or keyboard while UVR dialogs are being
   filled automatically.
6. Wait for the completion message. Each song should produce six MP3 files named with
   `(Bass)`, `(Drums)`, `(Guitar)`, `(Vocals)`, `(Piano)`, and `(Other)`.

## Step 2: make the backing tracks

1. Close Audacity if it is already running.
2. Start `Audacity Backing Tracks.exe`.
3. Select the processing folder created in Step 1.
4. Select a separate finished-output folder.
5. Leave **Move retained stems...** checked for the normal workflow. This moves Bass, Drums,
   Guitar, and Vocals to the finished album and deletes temporary Piano and Other files after
   validation.
6. Click **Start**. BackTracker opens Audacity, mixes Bass + Drums + Vocals for every song, and
   exports an MP3 backing track.

Each finished album contains `backing tracks`, `guitar tracks`, and `stems` folders for Bass,
Drums, and Vocals.

## Troubleshooting

- If UVR is not found, set `UVR_EXE` as shown above.
- If Audacity is not found, set `AUDACITY_EXE` as shown above.
- If Audacity reports a scripting-pipe error, confirm `mod-script-pipe` is installed and enabled,
  then restart Audacity once.
- If UVR clicks the wrong controls, use the standard UVR window size and ensure display scaling is
  100%; this release automates the current UVR5 desktop interface by screen position.
- Keep source, processing, and finished folders separate so original audio is never overwritten.

## Building from source

Install Python 3.10+ and PyInstaller, then run:

```powershell
python -m pip install pyinstaller
pyinstaller "UVR5 Album Separator.spec"
pyinstaller "Audacity Backing Tracks.spec"
```

Generated executables and build metadata are excluded from Git because build metadata can contain
machine-specific paths.

## License

Released under the MIT License.
