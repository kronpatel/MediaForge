# MediaForge — Post Release Roadmap

This document outlines the monitoring, maintenance, and future planning roadmap following the successful release of MediaForge v1.2.1.

---

## Phase 1 — Monitoring & Feedback

During the initial post-release phase, we will establish monitoring loops to ensure runtime stability:
- **GitHub Issues:** Monitor incoming bug reports, particularly for platform-specific edge cases on Windows 10/11.
- **GitHub Discussions:** Gather feedback on user experience, installation flows, and feature suggestions.
- **User Feedback & Crash Reports:** Monitor local log files for python tracebacks, connection timeout warnings, and update anomalies.
- **Auto-Updater Telemetry:** Verify update checks and download metrics to confirm that v1.2.0 users successfully migrate to v1.2.1.

*Note: No code modifications will be introduced unless a verified bug or crash blocker is reported.*

---

## Phase 2 — Repository Maintenance

Ongoing tasks to maintain the health of the project:
- **CI Stability:** Periodically check that GitHub Actions workflow runs remain green on all Python versions (3.10–3.13) under `windows-latest`.
- **Link Auditing:** Ensure all links in documentation, code comments, and manifest assets point to active resources.
- **Release Asset Availability:** Periodically verify that binary setup installers (`MediaForge-Setup.exe`) and portable archives (`MediaForge_Portable.zip`) remain downloadable from the release endpoint.
- **Dependency Security Audits:** Watch for security advisories and version warning notifications on core dependencies (Flask, Flask-CORS, requests, customtkinter, Pillow, psutil, and yt-dlp).

---

## Phase 3 — Planning MediaForge v1.3.0

The following features and improvements are collected for consideration in the next development cycle:

### 1. Installation & Integration UX
- **One-click Extension Installer:** Provide a button inside the Companion Settings panel to automatically copy and register the unpacked MV3 Chrome extension to the user's local browser profile directory.
- **Automatic Extension Updates:** Implement version mismatch alerts when the extension version is behind the companion backend, prompting the user to refresh.

### 2. Queue & Download Scheduling
- **Schedule Management Refinement:** Allow users to pause, resume, and edit scheduled download tasks directly from the queue.
- **Improved Priority Queueing:** Reorder active tasks dynamically with finer control over concurrently running jobs.

### 3. UI/UX Polish
- **Status Dashboard:** Display active connection throughput, memory foot-print, and average speed indicators in real-time.
- **Theme Support:** Introduce customizable accent colors and custom background themes (Dark, Midnight, High Contrast).

### 4. Performance & Cross-Platform Goals
- **Download Optimizations:** Refine yt-dlp parameter configurations to optimize segment downloads and merge speeds.
- **Multi-Platform Support:** Investigate requirements for porting the CustomTkinter Companion interface to macOS (Cocoa-based packaging) and Linux (X11/Wayland integrations).

---

## Final Verdict

PROJECT STABLE
READY FOR NEXT DEVELOPMENT CYCLE
