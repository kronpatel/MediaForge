# Build Guide

This document explains how to build, package, and verify MediaForge from source.

## Prerequisites

- Python 3.10+
- PyInstaller (`pip install pyinstaller`)
- All backend and companion requirements installed (`pip install -r backend/requirements.txt && pip install -r companion/requirements.txt`)
- FFmpeg binaries in `ffmpeg/` (see README)

## Quick Build

Build the companion EXE (one-folder):

```batch
build.bat
```

This runs: version verification → compile check → tests → PyInstaller → artifact verification.

Output: `dist/MediaForge/` (one-folder) or `dist/MediaForge.exe` (one-file, see spec).

## Clean

Remove all build artifacts:

```batch
clean.bat
```

Removes: `dist/`, `build/`, `*.spec`, `__pycache__`, `.pyc`, `release/`, test logs.

## Full Release Pipeline

Run the complete release workflow:

```batch
release.bat
```

Steps:
1. Clean previous artifacts
2. Build companion EXE (via build.bat)
3. Verify resources
4. Package portable ZIP
5. Verify release artifacts
6. Run smoke test

Outputs:
- `dist/MediaForge.exe` — Standalone companion executable
- `release/MediaForge_Portable.zip` — Portable edition ZIP

## Production Build Configuration

| Setting | Value |
|---------|-------|
| **Build Type** | One-file |
| **UPX** | **Disabled** |

**Reason for disabling UPX:**

- Improves compatibility with Microsoft Defender Attack Surface Reduction (ASR) rules on hardened Windows 11 systems. UPX-compressed one-file executables can trigger the "Use advanced protection against ransomware" ASR rule (C1DB55AB) via cloud heuristic scanning.
- UPX provides no meaningful size reduction for MediaForge (difference <0.01 MB between UPX and no-UPX builds).
- Current production builds intentionally ship with UPX disabled.

*Note: On default Windows 11 configurations (no enterprise ASR policies), UPX-enabled builds also launch without issues. Code signing would allow safely re-enabling UPX on enterprise-hardened systems.*

## Manual PyInstaller Build

```batch
pyinstaller MediaForge.spec
```

## Verification Scripts

| Script | Purpose |
|--------|---------|
| `scripts/verify_versions.py` | Check that VERSION matches all source files |
| `scripts/check_resources.py` | Verify icon and resource files exist |
| `scripts/verify_build.py` | Check build artifacts (EXE, ZIP) |
| `scripts/package_portable.py` | Package portable ZIP |
| `scripts/smoke_test.py` | End-to-end API smoke test |

## Version Bumping

To update the project version:

1. Edit `VERSION` (single source of truth)
2. Run `python scripts/verify_versions.py` to see all files that need updating
3. Run `release.bat` to produce release artifacts

## CI Pipeline

The GitHub Actions CI workflow runs:
- Compile checks on all `.py` files
- Full unittest suite (companion + backend)
- Version verification
- Resource checks

See `.github/workflows/tests.yml` for details.

## Runtime Files

The following files and directories are generated automatically at runtime when the application is executed:
- `backend/settings.json` — Backend preferences and paths
- `backend/queue_state.json` — Persistent state of active, queued, and failed downloads
- `backend/download_history.jsonl` — Local log history of finished/canceled downloads
- `companion/settings.json` — Companion dashboard preferences (themes, update schedules)
- `companion/cache/` — Temporary caching files used for update checks and notifications

These files and directories are ignored by Git (defined in `.gitignore`) and should never be committed to the repository. If any of these files are deleted or missing, they are automatically recreated with safe default configurations upon application launch.
