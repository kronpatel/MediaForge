# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-11

### Added
- Professional browser extension UI supporting MP3 (320kbps), 1080p, 4K, and 8K video downloads.
- Automatic quality fallback when the requested resolution (such as 8K) is unavailable.
- Full support for downloading entire YouTube Playlists (as MP3s or video formats).
- Interactive, responsive UI popup featuring:
  - Active downloading progress bar, ETA, and speed metrics.
  - Queue management for tracking multiple simultaneous/pending downloads.
  - Persistent download history logs using JSONL storage.
- Settings page with manual and folder-picker configurations, backend settings, and theme customization (Dark, Midnight, and High Contrast).
- Portable Windows startup batch script `MediaForge Backend.bat` using relative path checks.
- Dynamic relative path binding for FFmpeg toolchain binaries inside `Project Root/ffmpeg` instead of local absolute paths.
- Comprehensive licensing (MIT License) and user setups.

### Changed
- Migrated codebase and extension naming from "Kerzox Downloader" to "MediaForge" for public release.
- Upgraded layout elements to prevent shifting, overflows, or container resizing artifacts on Chromium browsers.

### Excluded
- Local large FFmpeg binaries (`ffmpeg/`) and virtual environments (`.venv/`) from source control to maintain a clean repository footprint.
