# Contributing to MediaForge

Thank you for your interest in contributing to MediaForge! Contributions from the community help make this project better for everyone.

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Branch Strategy

We use a standard branching strategy to keep our production release stable:

*   **`main`**: The stable branch containing the latest production release code. Direct pushes to `main` are restricted.
*   **Feature Branches (`feature/` or `fix/`)**: All active development, features, and bug fixes must be developed in dedicated branches branched off `main`.
    *   Example naming: `feature/auto-scheduler`, `fix/tray-menu-leak`.
*   **Pull Requests**: All feature branches must be merged into `main` via a Pull Request (PR).

---

## Project Structure

A high-level overview of the MediaForge repository layout:

```text
MediaForge/
├── .github/
│   └── workflows/         # GitHub Actions CI configurations
├── assets/                # Documentation screenshots and visual assets
├── backend/               # Flask backend application logic
│   ├── app.py             # Flask application entry point and routes
│   ├── downloader.py      # Core download logic (yt-dlp, FFmpeg integration)
│   └── requirements.txt   # Backend dependency list
├── companion/             # Companion app source modules
│   ├── main.py            # Companion tray and startup entry point
│   ├── updater.py         # Update checker and manager
│   ├── installer.py       # Update installer launcher
│   ├── scheduler.py       # Scheduled downloads service
│   ├── tray.py            # System tray icon and event loop
│   ├── ui.py              # Main dashboard and tab navigator UI
│   ├── queue_panel.py     # Active downloads queue panel
│   ├── settings_panel.py  # User preferences configuration panel
│   ├── test_*.py          # Comprehensive suite of companion unit tests
│   └── requirements.txt   # Companion desktop dependencies
├── extension/             # Chromium browser extension files
│   ├── manifest.json      # Extension configuration and permissions
│   ├── background.js      # Service worker for system-level integrations
│   ├── content.js         # Page injection script for YouTube DOM integration
│   ├── settings.html      # Widescreen extension settings page
│   └── settings.js        # Extension settings page logic
└── ffmpeg/                # Local folder for platform-specific FFmpeg binaries
```

---

## Development Setup

To set up the development environment locally:

### 1. Prerequisites
- **Python 3.10+** installed.
- **Google Chrome** or any Chromium-based browser (Edge, Brave, Opera, etc.).

### 2. Backend & Companion Setup
1. Clone the repository:
   ```bash
   git clone https://github.com/kronpatel/MediaForge.git
   cd MediaForge
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```
3. Install backend dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```
4. Install companion application dependencies:
   ```bash
   pip install -r companion/requirements.txt
   ```
5. Place FFmpeg binaries (`ffmpeg.exe` and `ffprobe.exe` for Windows) inside the `ffmpeg/` directory at the project root.

### 3. Extension Setup
1. Open Chrome and navigate to `chrome://extensions/`.
2. Turn on **Developer mode** in the top-right corner.
3. Click **Load unpacked** and select the `extension/` directory.

---

## Running Tests

MediaForge uses Python's standard `unittest` framework to verify companion features:

1. Activate your virtual environment.
2. Navigate to the `companion/` directory (or project root).
3. Run the automated test suite:
   ```bash
   # Running from project root:
   python -m unittest discover -s companion -p "test_*.py" -v
   ```
4. Ensure all companion and backend unit tests pass and compile checks complete before opening a pull request.

---

## Coding Style

Please follow these programming style guidelines:

*   **Python**: Follow PEP 8 guidelines. Use clear variable and function names. Use type hints where appropriate (e.g., `def process_job(job_id: str) -> bool`).
*   **JavaScript**: Write modern, clean ES6+ code. Avoid globals; use scoping and modular patterns.
*   **HTML/CSS**: Keep HTML semantic. Use CSS custom properties for styling, following the project's layout system.

---

## Issue Reporting Guidelines

If you encounter an issue or have a feature request:

### Bug Reports
Before submitting a bug report:
1. Search the existing issues to ensure it hasn't already been reported.
2. Ensure you are on the latest release of MediaForge.
3. If reporting, open a new issue and include:
   * **Title**: A clear and descriptive title (e.g., "Queue panel scroll jumps on active list update").
   * **Steps to Reproduce**: Detailed list of steps to trigger the bug.
   * **Expected vs. Actual Behavior**: Explain what should have happened vs. what actually happened.
   * **Environment Details**: Python version, browser, operating system version.
   * **Logs**: Include traceback errors from your terminal or console logs.

### Feature Requests
To suggest an enhancement:
1. Explain the **use case** and why it would benefit users.
2. Provide a brief description of the **proposed user flow** or design.

---

## Pull Request Rules

When submitting code changes:

1. **Keep it focused**: Do not combine multiple unrelated bug fixes or features in a single PR.
2. **Sync with main**: Ensure your branch is updated with the latest changes from `main` before submitting.
3. **Verify tests pass**: Run the unit test suite and confirm all companion and backend tests pass.
4. **Compile check**: Verify that all Python files compile cleanly.
5. **No build artifacts**: Do not check in build files, local caches, logs, or temporary setting files.
6. **Update docs**: Update the `README.md` or other documentation if your changes introduce new parameters, requirements, or features.

---

## Commit Message Format

We require commit messages to be descriptive and structured. Follow this prefix-based standard:

*   `feat: <description>` — A new feature (e.g., `feat: add scheduling support to dashboard`)
*   `fix: <description>` — A bug fix (e.g., `fix: resolve settings panel duplicate borders`)
*   `docs: <description>` — Documentation-only changes (e.g., `docs: add development guidelines to CONTRIBUTING.md`)
*   `style: <description>` — Code style changes (formatting, missing semicolons, no functional change)
*   `refactor: <description>` — A code change that neither fixes a bug nor adds a feature
*   `test: <description>` — Adding missing tests or correcting existing tests
*   `chore: <description>` — Updating build tasks, dependencies, git settings, etc.

Example:
```text
feat: integrate local auto-updater inside tray icon check flow
```
