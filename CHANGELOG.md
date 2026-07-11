# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.3] - 2026-07-11

### Fixed
- **Backend Manager**: Fixed an issue where the background python backend did not start automatically on companion launch if the port was free.
- **Maintenance**: Incremented version references across the project to v1.2.3.

## [1.2.0] - 2026-06-27

### Added
- **Auto Updater**: Background updates manager with releases checks, version comparison, download cancellation, integrity checks, and update notifications.
- **Installer Engine**: Robust setup installer execution with automatic recovery, admin privileges elevation, process locks checks, and diagnostic reporting.
- **Scheduler**: Full-featured task scheduler engine supporting job CRUD operations, next-run calculations, event notifications, database persistence, clock jump recovery, and schema migrations.
- **Dashboard Improvements**: Polished widescreen tray options interface, integrated with system tray icon, tabbed pages, and live indicators.
- **Startup Optimization**: Background services launch sequence refinement with optimized resource allocations.
- **Notification Manager**: Priority-based notifications scheduler with quiet hours support, singleton daemon thread, history storage, and toast notifications.
- **Queue UX Improvements**: Re-engineered active downloads layout with smooth transitions, pausing, keyboard hooks, context menus, and scroll position recovery.
- **Engineering Hardening**: Implemented RLock thread synchronization locks on shared states, atomic file saves, and multi-layer crash handling.
- **GitHub Actions CI**: Configured automated continuous integration pipeline verifying compile status and running 161 tests on Windows runners under Python 3.10–3.13.
- **Release Checklist**: Standardized verification guidelines covering tests, manual execution steps, tag rules, and staging deployment.

### Improved
- **Backend Stability:** Robust error handling for the Flask backend download processes.
- **Queue Reliability:** Optimized queue transition flows and state synchronization between extension and backend.
- **Extension Stability:** Enhanced resilience against browser service worker suspensions.
- **Polling Recovery:** Implemented graceful polling reconnection logic during temporary network or backend drops.
- **MutationObserver Lifecycle:** Minimized DOM overhead and prevented memory leaks by correctly managing observers.
- **Notification Compatibility:** Integrated native notifications using compatible image formats.
- **Performance Improvements:** Consolidated redundant panel fetches and minimized DOM layout reflows.
- **Release Hardening:** Cleaned up unused legacy scripts and refined development settings for production.

### Fixed
- **Settings Overwrite Issue:** Prevented default settings from overwriting user-configured options.
- **History Caching Improvements:** Resolved performance issues when reading download logs by implementing an mtime-validated cache.
- **Thread Safety Improvements:** Added thread-safe synchronization locks to prevent concurrent write and read conflicts on shared state.
- **Headless Compatibility:** Fixed crashes on servers without graphical environments by providing a clean text-based configuration fallback.
- **Duplicate UI Injection:** Prevented duplicate MediaForge download buttons from being injected on YouTube SPA page transitions.
- **Timer Cleanup:** Resolved memory and timer leak issues by systematically clearing background intervals on tab navigation.
- **Notification Icon Compatibility:** Resolved rendering issues with extension notifications by migrating the icon asset from SVG to PNG format.

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
