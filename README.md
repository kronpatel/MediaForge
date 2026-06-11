# MediaForge

Fast, modern, and powerful media downloader with support for MP3, 1080p, 4K, 8K, playlist downloads, download history management, and a clean browser extension interface.

## Features

- **MP3 Downloads** (High quality 320kbps audio extraction)
- **1080p Video Downloads**
- **4K Video Downloads**
- **8K Video Downloads** with automatic fallback if 8K is not available
- **Playlist Downloads** (Full playlist video/audio extraction)
- **Download History** to track past downloads
- **Clear History** options
- **Modern UI** with dark, midnight, and high contrast themes
- **FFmpeg Integration** for seamless post-processing and merging
- **yt-dlp Powered** for fast, reliable, and up-to-date video extraction

---

## Requirements

Before running MediaForge, ensure you have the following installed:

1. **Python 3.8+**
2. **FFmpeg** (See setup instructions below)
3. **Google Chrome / Chromium-based Browser** (Microsoft Edge, Brave, Opera, etc.)

---

## Directory Structure

For everything to run smoothly, ensure your folder structure looks like this:

```text
MediaForge/
├── backend/
│   ├── app.py
│   ├── downloader.py
│   └── requirements.txt
├── extension/
│   ├── manifest.json
│   ├── content.js
│   └── ...
├── ffmpeg/             <-- Download and place FFmpeg binaries here
│   ├── ffmpeg.exe
│   └── ffprobe.exe
└── MediaForge Backend.bat
```

---

## Installation & Setup

### 1. Backend Setup

Open a terminal at the project root and navigate to the `backend/` directory:

```bash
cd backend
```

Create a virtual environment (optional but recommended):

```bash
python -m venv .venv
```

Activate the virtual environment:
* **Windows**: `.venv\Scripts\activate`
* **macOS/Linux**: `source .venv/bin/activate`

Install the required packages:

```bash
pip install -r requirements.txt
```

### 2. FFmpeg Setup

Since FFmpeg binaries are too large for Git/GitHub, you must obtain them manually:

1. Download the static FFmpeg build for your operating system:
   - Recommended source: [FFmpeg official website](https://ffmpeg.org/download.html) or [Gyan.dev (Windows)](https://www.gyan.dev/ffmpeg/builds/).
2. Extract the archive and copy `ffmpeg.exe` and `ffprobe.exe`.
3. Create a folder named `ffmpeg` at the root of the project (parent folder of `backend`).
4. Paste `ffmpeg.exe` and `ffprobe.exe` directly inside that `ffmpeg/` directory.

*Note: The backend is programmed to dynamically resolve FFmpeg inside `Project Root/ffmpeg` so it works out-of-the-box.*

### 3. Extension Installation

1. Open your Chromium-based browser (e.g., Google Chrome).
2. Navigate to `chrome://extensions/`.
3. Enable **Developer mode** using the toggle switch in the top-right corner.
4. Click **Load unpacked** in the top-left corner.
5. Select the `extension` folder inside the MediaForge project directory.

---

## Usage Instructions

1. **Start the Backend**:
   - Double-click the `MediaForge Backend.bat` file in the project root, OR run:
     ```bash
     cd backend
     python app.py
     ```
   - This starts the local server at `http://127.0.0.1:5000`.

2. **Download Media**:
   - Navigate to any YouTube video, short, or playlist.
   - Click the floating **MediaForge** button below the video title.
   - Choose your preferred download option (MP3, 1080p, 4K, 8K, or playlist).
   - Track progress, queue state, and history directly inside the extension popup UI.

3. **Configure Settings**:
   - Click the **Settings** gear icon in the footer of the extension popup.
   - Set custom download folders or select a theme (Dark, Midnight, High Contrast).

---

## Screenshots

### Main Interface
![Main Interface](assets/home.png)

### Download Panel
![Download Panel](assets/download-panel.png)

### History Tab
![History Tab](assets/history.png)

### Settings
![Settings](assets/settings.png)

---

## Roadmap

Future Features:
* **Open Download Folder**: Directly open the destination downloads folder from the UI.
* **Delete Single History Entry**: Individually clean up download records from history.
* **Download Scheduler**: Plan and schedule downloads for off-peak hours.
* **More Media Sources**: Support for downloading from platforms outside of YouTube.
* **Theme Customization**: Fully custom accent color pickers.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

*Crafted by **KERZOX***
