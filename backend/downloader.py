import json
import logging
import os
import queue
import re
import threading
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Callable

import yt_dlp
from yt_dlp.utils import DownloadError


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "ffmpeg"))

DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE = os.path.join(BASE_DIR, "download_history.jsonl")

DEFAULT_RETRIES = 2
MAX_HISTORY_ITEMS = 100

INVALID_WINDOWS_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_WINDOWS_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}

logger = logging.getLogger("kerzox.downloader")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


class KerzoxDownloadError(RuntimeError):
    """Readable exception that Flask can return to the extension."""


@dataclass
class DownloadJob:
    id: str
    url: str
    mode: str
    label: str
    status: str = "queued"
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    downloaded: str = ""
    total: str = ""
    message: str = "Waiting in queue"
    filename: str = ""
    error: str = ""
    attempts: int = 0
    max_retries: int = DEFAULT_RETRIES
    queued_at: str = ""
    started_at: str = ""
    completed_at: str = ""


_download_queue: queue.Queue[str] = queue.Queue()
_jobs: dict[str, DownloadJob] = {}
_jobs_lock = threading.RLock()
_history_lock = threading.RLock()
_worker_lock = threading.Lock()
_worker_started = False
_active_job_id: str | None = None
_history_cache: list[dict[str, Any]] | None = None
_history_last_mtime: float | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_settings() -> dict[str, Any]:
    return {
        "download_folder": DOWNLOAD_DIR,
        "ffmpeg_path": FFMPEG_PATH,
        "backend_url": "http://127.0.0.1:5000",
        "theme": "dark",
        "version": "1.1.0",
    }


def read_settings() -> dict[str, Any]:
    settings = default_settings()

    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as settings_file:
                saved_settings = json.load(settings_file)
            if isinstance(saved_settings, dict):
                settings.update(saved_settings)
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read settings file")

    if not is_valid_download_folder(settings.get("download_folder", "")):
        settings["download_folder"] = DOWNLOAD_DIR

    return settings


def write_settings(changes: dict[str, Any]) -> dict[str, Any]:
    settings = read_settings()

    if "download_folder" in changes:
        folder = str(changes.get("download_folder") or "").strip()
        settings["download_folder"] = folder if is_valid_download_folder(folder) else DOWNLOAD_DIR

    if "theme" in changes:
        theme = str(changes.get("theme") or "dark").strip().lower()
        settings["theme"] = theme if theme in {"dark", "midnight", "contrast"} else "dark"

    with open(SETTINGS_FILE, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file, indent=2, ensure_ascii=False)

    return settings


def reset_download_folder() -> dict[str, Any]:
    return write_settings({"download_folder": DOWNLOAD_DIR})


def is_valid_download_folder(folder: str) -> bool:
    return bool(folder) and os.path.isdir(folder) and os.access(folder, os.W_OK)


def get_download_dir() -> str:
    settings = read_settings()
    folder = settings.get("download_folder", DOWNLOAD_DIR)

    if is_valid_download_folder(folder):
        return folder

    logger.warning("Invalid download folder %s. Falling back to %s", folder, DOWNLOAD_DIR)
    return DOWNLOAD_DIR


def select_download_folder() -> dict[str, Any]:
    import sys
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise KerzoxDownloadError("Graphical environment is not available on this Linux system. Please enter the folder path manually in settings.")

    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        logger.warning("Tkinter is not installed on this system")
        raise KerzoxDownloadError("Graphical folder picker is not available (Tkinter missing). Please enter the folder path manually in settings.")

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected_folder = filedialog.askdirectory(
            title="Select Kerzox Download Folder",
            initialdir=get_download_dir(),
        )
        root.destroy()
    except Exception:
        logger.exception("Folder picker failed")
        raise KerzoxDownloadError("Folder picker failed to open.")

    if not selected_folder:
        raise KerzoxDownloadError("No folder selected")

    return write_settings({"download_folder": selected_folder})


def sanitize_filename(value: str, fallback: str = "Kerzox Download") -> str:
    cleaned = INVALID_WINDOWS_FILENAME_CHARS.sub("_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(". ")

    if not cleaned:
        cleaned = fallback

    if cleaned.upper() in RESERVED_WINDOWS_FILENAMES:
        cleaned = f"{cleaned}_"

    return cleaned


def job_snapshot(job: DownloadJob) -> dict[str, Any]:
    return asdict(job)


def update_job(job_id: str, **changes: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return

        for key, value in changes.items():
            if hasattr(job, key):
                setattr(job, key, value)


def append_history(job: DownloadJob) -> None:
    global _history_cache, _history_last_mtime
    os.makedirs(BASE_DIR, exist_ok=True)
    record = job_snapshot(job)

    with _history_lock:
        with open(HISTORY_FILE, "a", encoding="utf-8") as history_file:
            history_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        if _history_cache is not None:
            _history_cache.append(record)
        if os.path.exists(HISTORY_FILE):
            try:
                _history_last_mtime = os.path.getmtime(HISTORY_FILE)
            except OSError:
                logger.warning("Could not update mtime after write")


def read_history(limit: int = MAX_HISTORY_ITEMS) -> list[dict[str, Any]]:
    global _history_cache, _history_last_mtime
    with _history_lock:
        file_exists = os.path.exists(HISTORY_FILE)
        current_mtime = None
        if file_exists:
            try:
                current_mtime = os.path.getmtime(HISTORY_FILE)
            except OSError:
                logger.warning("Could not check history file mtime")

        if current_mtime != _history_last_mtime:
            _history_cache = None
            _history_last_mtime = current_mtime

        if _history_cache is None:
            _history_cache = []
            if file_exists:
                try:
                    with open(HISTORY_FILE, "r", encoding="utf-8") as history_file:
                        for line in history_file:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                _history_cache.append(json.loads(line))
                            except json.JSONDecodeError:
                                logger.warning("Skipping invalid history line")
                except OSError:
                    logger.exception("Could not read history file")

        result = _history_cache[-limit:]
        result.reverse()
        return result


def clear_history() -> None:
    global _history_cache, _history_last_mtime
    with _history_lock:
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except OSError as error:
                logger.exception("Could not delete history file")
                raise KerzoxDownloadError(f"Could not delete history file: {error}") from error
        _history_cache = []
        _history_last_mtime = None


def progress_hook_for(job_id: str) -> Callable[[dict[str, Any]], None]:
    def progress_hook(status: dict[str, Any]) -> None:
        download_status = status.get("status")
        filename = sanitize_filename(os.path.basename(status.get("filename") or ""))

        if download_status == "downloading":
            downloaded_bytes = status.get("downloaded_bytes") or 0
            total_bytes = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            progress = calculate_progress(downloaded_bytes, total_bytes, status.get("_percent_str", ""))
            speed = format_speed(status.get("speed"))
            eta = format_eta(status.get("eta"))
            downloaded = format_bytes(downloaded_bytes)
            total = format_bytes(total_bytes) if total_bytes else ""

            # Check if there is an existing warning message or if 8K is not available
            message = "Downloading"
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job:
                    if job.mode == "8k":
                        info_dict = status.get("info_dict") or {}
                        height = info_dict.get("height")
                        if not height:
                            req_formats = info_dict.get("requested_formats") or []
                            for f in req_formats:
                                if f.get("vcodec") != "none" and f.get("height"):
                                    height = f.get("height")
                                    break
                        if height and height < 4320:
                            message = "8K not available. Downloading highest available quality."
                        elif "8K not available" in job.message:
                            message = "8K not available. Downloading highest available quality."
                    elif "8K not available" in job.message:
                        message = "8K not available. Downloading highest available quality."

            details = [part for part in (f"{progress:.1f}%", speed, f"ETA {eta}" if eta else "") if part]
            update_job(
                job_id,
                status="downloading",
                progress=progress,
                speed=speed,
                eta=eta,
                downloaded=downloaded,
                total=total,
                filename=filename,
                message=message,
            )
            logger.info("Job %s downloading %s%s", job_id, filename, f" - {' | '.join(details)}" if details else "")

        elif download_status == "finished":
            message = "Download complete. Processing with FFmpeg..."
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job and "8K not available" in job.message:
                    message = "8K not available. Downloading highest available quality. Processing with FFmpeg..."

            update_job(
                job_id,
                progress=100.0,
                filename=filename,
                message=message,
            )
            logger.info("Job %s download complete: %s", job_id, filename)

    return progress_hook


def parse_percent(value: str) -> float:
    try:
        return float(value.replace("%", "").strip())
    except ValueError:
        return 0.0


def calculate_progress(downloaded_bytes: int | float, total_bytes: int | float, percent_text: str) -> float:
    if total_bytes:
        return round(min(100.0, max(0.0, (float(downloaded_bytes) / float(total_bytes)) * 100)), 1)

    return round(parse_percent(percent_text), 1)


def format_bytes(value: int | float | None) -> str:
    if not value:
        return ""

    size = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = units[0]

    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024

    return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"


def format_speed(value: int | float | None) -> str:
    formatted = format_bytes(value)
    return f"{formatted}/s" if formatted else ""


def format_eta(value: int | float | None) -> str:
    if value is None:
        return ""

    seconds = max(0, int(value))
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)

    if hours:
        return f"{hours:02d}:{remaining_minutes:02d}:{remaining_seconds:02d}"

    return f"{remaining_minutes:02d}:{remaining_seconds:02d}"


def build_output_template(playlist: bool = False) -> str:
    download_dir = get_download_dir()

    if playlist:
        return os.path.join(
            download_dir,
            "%(playlist_title).180B",
            "%(playlist_index)03d - %(title).180B [%(id)s].%(ext)s",
        )

    return os.path.join(download_dir, "%(title).180B [%(id)s].%(ext)s")


def base_options(job_id: str | None = None, playlist: bool = False) -> dict[str, Any]:
    os.makedirs(get_download_dir(), exist_ok=True)

    options: dict[str, Any] = {
        "ffmpeg_location": FFMPEG_PATH,
        "ignoreerrors": False,
        "noplaylist": not playlist,
        "outtmpl": build_output_template(playlist=playlist),
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "restrictfilenames": False,
        "trim_file_name": 180,
        "windowsfilenames": True,
    }

    if job_id:
        options["progress_hooks"] = [progress_hook_for(job_id)]

    return options


def mp3_options(job_id: str | None = None, playlist: bool = False) -> dict[str, Any]:
    options = base_options(job_id=job_id, playlist=playlist)
    options.update({
        "format": "bestaudio",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
    })
    return options


def video_options(
    job_id: str | None = None,
    max_height: int | None = None,
    playlist: bool = False,
    format_selector: str | None = None,
) -> dict[str, Any]:
    options = base_options(job_id=job_id, playlist=playlist)
    
    if not format_selector:
        format_selector = "bestvideo+bestaudio"
        if max_height:
            format_selector = f"bestvideo[height<={max_height}]+bestaudio"

    options.update({
        "format": format_selector,
        "merge_output_format": "mp4",
    })
    return options


def check_8k_available(url: str, options: dict[str, Any]) -> bool:
    try:
        opts = options.copy()
        opts["skip_download"] = True
        if "progress_hooks" in opts:
            del opts["progress_hooks"]
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])
            for f in formats:
                height = f.get("height") or 0
                width = f.get("width") or 0
                if height >= 4320 or width >= 7680:
                    return True
    except Exception as e:
        logger.warning("Error checking 8K availability: %s", e)
    return False


def mode_options(mode: str, job_id: str | None = None) -> tuple[str, dict[str, Any]]:
    modes: dict[str, tuple[str, dict[str, Any]]] = {
        "mp3": ("MP3", mp3_options(job_id=job_id, playlist=False)),
        "1080p": ("MP4 1080p", video_options(job_id=job_id, max_height=1080, playlist=False)),
        "4k": ("MP4 4K", video_options(job_id=job_id, max_height=2160, playlist=False)),
        "8k": ("MP4 8K", video_options(job_id=job_id, max_height=None, playlist=False, format_selector="bestvideo[height<=4320]+bestaudio/best[height<=4320]")),
        "playlist_mp3": ("Playlist MP3", mp3_options(job_id=job_id, playlist=True)),
        "playlist_video": ("Playlist Video", video_options(job_id=job_id, max_height=None, playlist=True)),
    }

    if mode not in modes:
        raise KerzoxDownloadError(f"Invalid download mode: {mode}")

    return modes[mode]


def run_download(url: str, ydl_opts: dict[str, Any], label: str) -> str:
    if not url:
        raise KerzoxDownloadError("URL missing")

    try:
        logger.info("Starting %s download: %s", label, url)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        message = f"{label} download completed"
        logger.info(message)
        return message

    except DownloadError as error:
        message = f"{label} download failed: {error}"
        logger.exception(message)
        raise KerzoxDownloadError(message) from error

    except OSError as error:
        message = f"{label} download failed because FFmpeg or file access is not available: {error}"
        logger.exception(message)
        raise KerzoxDownloadError(message) from error

    except Exception as error:
        message = f"{label} download failed: {error}"
        logger.exception(message)
        raise KerzoxDownloadError(message) from error


def execute_job(job: DownloadJob) -> None:
    global _active_job_id

    with _jobs_lock:
        _active_job_id = job.id
    
    initial_message = f"Starting {job.label} download"
    if job.mode == "8k":
        try:
            _, options = mode_options(job.mode, job_id=job.id)
            if not check_8k_available(job.url, options):
                initial_message = "8K not available. Downloading highest available quality."
        except Exception as e:
            logger.warning("Error performing initial 8K check: %s", e)

    update_job(
        job.id,
        status="running",
        started_at=now_iso(),
        message=initial_message,
    )

    last_error = ""
    for attempt in range(1, job.max_retries + 2):
        update_job(job.id, attempts=attempt)

        try:
            label, options = mode_options(job.mode, job_id=job.id)
            run_download(job.url, options, label)
            
            final_message = f"{job.label} download completed"
            with _jobs_lock:
                job_obj = _jobs.get(job.id)
                if job_obj and "8K not available" in job_obj.message:
                    final_message = "8K not available. Downloading highest available quality. (Completed)"

            update_job(
                job.id,
                status="completed",
                progress=100.0,
                speed="",
                eta="",
                completed_at=now_iso(),
                message=final_message,
                error="",
            )
            break

        except KerzoxDownloadError as error:
            last_error = str(error)
            attempts_left = job.max_retries + 1 - attempt
            logger.exception("Job %s attempt %s failed", job.id, attempt)

            if attempts_left <= 0:
                update_job(
                    job.id,
                    status="failed",
                    completed_at=now_iso(),
                    message="Download failed",
                    error=last_error,
                )
                break

            update_job(
                job.id,
                status="retrying",
                message=f"Retrying after error. Attempts left: {attempts_left}",
                error=last_error,
            )

    with _jobs_lock:
        finished_job = _jobs[job.id]
        append_history(finished_job)
        _active_job_id = None


def download_worker() -> None:
    while True:
        job_id = _download_queue.get()

        try:
            with _jobs_lock:
                job = _jobs.get(job_id)

            if job:
                execute_job(job)

        except Exception:
            logger.exception("Unexpected queue worker failure")

        finally:
            _download_queue.task_done()


def ensure_worker_started() -> None:
    global _worker_started

    with _worker_lock:
        if _worker_started:
            return

        worker = threading.Thread(target=download_worker, name="KerzoxDownloadWorker", daemon=True)
        worker.start()
        _worker_started = True
        logger.info("Kerzox download worker started")


def queue_download(url: str, mode: str, retries: int = DEFAULT_RETRIES) -> dict[str, Any]:
    if not url:
        raise KerzoxDownloadError("URL missing")

    label, _ = mode_options(mode)
    ensure_worker_started()

    job = DownloadJob(
        id=uuid.uuid4().hex,
        url=url,
        mode=mode,
        label=label,
        max_retries=max(0, retries),
        queued_at=now_iso(),
    )

    with _jobs_lock:
        _jobs[job.id] = job
        _download_queue.put(job.id)
        position = _download_queue.qsize()

    logger.info("Queued job %s mode=%s position=%s", job.id, mode, position)

    snapshot = job_snapshot(job)
    snapshot["queue_position"] = position
    return snapshot


def get_download_status(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return None

        return job_snapshot(job)


def get_queue_status() -> dict[str, Any]:
    with _jobs_lock:
        queued_jobs = [
            job_snapshot(job)
            for job in _jobs.values()
            if job.status == "queued"
        ]
        failed_jobs = [
            job_snapshot(job)
            for job in _jobs.values()
            if job.status == "failed"
        ]
        active_job = job_snapshot(_jobs[_active_job_id]) if _active_job_id and _active_job_id in _jobs else None

    return {
        "active": active_job,
        "queued": queued_jobs,
        "failed": failed_jobs,
        "queued_count": len(queued_jobs),
        "failed_count": len(failed_jobs),
        "history": read_history(),
    }

