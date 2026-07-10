# Portable Edition Guide

The MediaForge Portable Edition is a self-contained ZIP archive that runs from any folder without installation.

## Contents

The portable ZIP (`release/MediaForge_Portable.zip`) contains:

```
MediaForge_Portable/
├── MediaForge.exe          # Companion application
├── mediaforge_start.bat    # Launcher script
├── ffmpeg/
│   ├── ffmpeg.exe          # FFmpeg binary
│   └── ffprobe.exe         # FFprobe binary
└── portable_settings.json  # Default settings (empty)
```

## Usage

1. Extract the ZIP to any folder
2. Run `mediaforge_start.bat` or double-click `MediaForge.exe`
3. The companion runs from the system tray

To also start the backend server:

```batch
mediaforge_start.bat --backend
```

## Data Storage

Portable mode stores all data locally within its own directory:
- `queue_state.json` — Download queue
- `settings.json` — User preferences
- `logs/companion.log` — Production log file (rotated)
- `downloads/` — Downloaded files (configurable)

No registry keys are written. No system-wide installation is performed.

## Upgrading

To upgrade the portable edition:

1. Download the new portable ZIP
2. Extract to a new folder
3. Copy your existing `settings.json` and `queue_state.json` to the new folder
4. Delete the old folder

## Limitations

- Portable mode does not auto-update (no installer)
- The companion EXE must be rebuilt for each new version
- FFmpeg binaries must be included manually or available on PATH
