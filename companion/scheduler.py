import os
import sys
import json
import time
import uuid
import datetime
import calendar
import threading
from typing import Callable, Any

# Paths
_COMPANION_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_COMPANION_DIR, "cache")
SCHEDULER_FILE = os.path.join(CACHE_DIR, "scheduler.json")
HISTORY_FILE = os.path.join(CACHE_DIR, "scheduler_history.json")

class SchedulerManager:
    def __init__(self, logger: Any, backend_manager: Any, window: Any = None) -> None:
        self.logger = logger
        self.backend = backend_manager
        self.window = window
        self._lock = threading.RLock()
        self._execution_lock = threading.Lock()
        
        # In-memory registers
        self._schedules: dict[str, dict] = {}
        self._listeners: list[Callable[[str, dict], None]] = []
        self._active_jobs: dict[str, str] = {}  # schedule_uuid -> backend job_id
        self._stats: dict[str, Any] = {
            "total_schedules": 0,
            "enabled_schedules": 0,
            "completed_runs": 0,
            "failed_runs": 0,
            "cancelled_runs": 0,
            "retries": 0,
            "missed_jobs": 0,
            "last_execution": None
        }
        self._startup_ready: bool = False
        self.settings: dict = {}  # populated by deferred_startup()
        
        # Ensure cache directory exists (fast, no I/O beyond mkdir)
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        # Polling/Thread controls
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick_time = time.monotonic()
        self._last_system_time = datetime.datetime.now()

    def deferred_startup(self) -> None:
        """Heavy startup work: runs in a daemon thread, never on the Tkinter thread."""
        try:
            from settings_panel import read_local_settings
            self.settings = read_local_settings(self.logger)
        except Exception:
            self.settings = {}

        self._load_schedules()
        self._run_startup_recovery()
        self._startup_ready = True
        self.logger.info("Scheduler startup complete.")

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._last_tick_time = time.monotonic()
            self._last_system_time = datetime.datetime.now()
            self._thread = threading.Thread(target=self._scheduler_loop, name="SchedulerThread", daemon=True)
            self._thread.start()
            self.logger.info("Download Scheduler engine started.")

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self.logger.info("Download Scheduler engine stopped.")

    # ------------------------------------------------------------------
    # Event Bus (Component 9)
    # ------------------------------------------------------------------
    def register_listener(self, callback: Callable[[str, dict], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def unregister_listener(self, callback: Callable[[str, dict], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify(self, event_name: str, payload: dict) -> None:
        listeners = []
        with self._lock:
            listeners = list(self._listeners)
            
        for cb in listeners:
            try:
                cb(event_name, payload)
            except Exception as e:
                self.logger.debug_log(f"Listener callback error: {e}")

    # ------------------------------------------------------------------
    # Scheduler Loop & Time Jump (Component 10 / 11)
    # ------------------------------------------------------------------
    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            # Check clock jumps (Component 11)
            now_sys = datetime.datetime.now()
            delta = (now_sys - self._last_system_time).total_seconds()
            
            if abs(delta) > 60.0:
                self.logger.info("Scheduler time change detected. Recalculating schedule execution times.")
                self._recalculate_all_schedules()
                self._notify("Time Changed", {})
                
            self._last_system_time = now_sys
            
            # Execute due schedules
            try:
                self._check_and_execute_schedules()
            except Exception as e:
                self.logger.error(f"Error in scheduler check tick: {e}")
                
            self._stop_event.wait(1.0)

    def _recalculate_all_schedules(self) -> None:
        with self._lock:
            for job in self._schedules.values():
                if job.get("enabled") and job.get("state") not in ("Expired", "Running", "Waiting"):
                    # Calculate fresh next run time
                    sched_dt = datetime.datetime.strptime(job["scheduled_time"], "%Y-%m-%d %H:%M:%S")
                    next_run = self.calculate_next_run(sched_dt, job["repeat_type"])
                    if next_run:
                        job["next_run"] = next_run.strftime("%Y-%m-%d %H:%M:%S")
                        job["state"] = "Scheduled"
                    else:
                        job["next_run"] = ""
                        job["state"] = "Expired"
            self._save_schedules()

    def _check_and_execute_schedules(self) -> None:
        now = datetime.datetime.now()
        due_jobs = []
        
        with self._lock:
            # Check for scheduler settings overrides
            from settings_panel import read_local_settings
            self.settings = read_local_settings(self.logger)
            if not self.settings.get("scheduler_enabled", True):
                return
                
            for job_id, job in self._schedules.items():
                if not job.get("enabled"):
                    continue
                if job.get("state") in ("Scheduled", "Waiting"):
                    next_run_str = job.get("next_run")
                    if next_run_str:
                        next_dt = datetime.datetime.strptime(next_run_str, "%Y-%m-%d %H:%M:%S")
                        if next_dt <= now:
                            due_jobs.append(job_id)
                            
        for jid in due_jobs:
            threading.Thread(target=self._execute_job_thread, args=(jid,), daemon=True).start()

    def _execute_job_thread(self, jid: str) -> None:
        with self._execution_lock:
            # Reload job with lock
            with self._lock:
                job = self._schedules.get(jid)
                if not job or not job.get("enabled") or job.get("state") in ("Running", "Completed"):
                    return
                # Update status
                job["state"] = "Running"
                job["last_execution"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._stats["last_execution"] = job["last_execution"]
                self._save_schedules()
            
            self._notify("Schedule Started", job)
            self.logger.info(f"Scheduled Job Started: {job['url']}")
            
            # Post Settings Dynamically if output folder is set
            success = True
            err_msg = ""
            original_folder = None
            
            try:
                # 1. Fetch current settings from backend first to know original folder
                curr_backend_settings = self.backend.get_settings()
                original_folder = curr_backend_settings.get("download_folder")
                
                # 2. Modify folder if specified
                custom_folder = job.get("output_folder", "").strip()
                if custom_folder and os.path.isdir(custom_folder):
                    self.backend.save_settings({"download_folder": custom_folder})
                
                # 3. Post download job
                payload = {
                    "url": job["url"],
                    "mode": job["mode"],
                    "retries": job.get("max_retries", 2)
                }
                
                resp = self.backend._send_request("POST", "/download", json=payload, timeout=5.0)
                if resp and resp.status_code in (200, 202):
                    resp_data = resp.json()
                    backend_job_id = resp_data.get("job_id")
                    
                    with self._lock:
                        self._active_jobs[jid] = backend_job_id
                        job["last_result"] = "success"
                        job["error_message"] = ""
                        self._save_schedules()
                else:
                    success = False
                    err_msg = "Backend rejected the download request"
                    if resp:
                        try:
                            err_msg = resp.json().get("message", err_msg)
                        except Exception:
                            pass
            except Exception as e:
                success = False
                err_msg = f"Network or execution error: {e}"
            
            # Restore original folder settings synchronously
            if original_folder:
                try:
                    self.backend.save_settings({"download_folder": original_folder})
                except Exception:
                    pass
            
            # If request dispatch failed, handle immediately
            if not success:
                self._handle_job_failure(jid, err_msg)

    # ------------------------------------------------------------------
    # Unified Poller State Interception
    # ------------------------------------------------------------------
    def refresh(self, data: dict[str, Any]) -> None:
        """
        Receives polling broadcasts from DashboardController (runs on Tkinter main thread).
        """
        if data.get("offline", True):
            return
            
        queue = data.get("queue", [])
        history = data.get("history", [])
        
        # Check active schedules states
        active_map = {}
        with self._lock:
            active_map = dict(self._active_jobs)
            
        for sched_uuid, b_job_id in active_map.items():
            # Find in active queue or history
            in_queue = next((j for j in queue if j["id"] == b_job_id), None)
            in_history = next((h for h in history if h["id"] == b_job_id), None)
            
            if in_queue:
                # Still running or pending download
                status = in_queue["status"]
                progress = in_queue["progress"]
                self._notify("Schedule Progress", {"uuid": sched_uuid, "status": status, "progress": progress})
                
            elif in_history:
                # Finished downloading!
                status = in_history["status"]
                
                # Cleanup active mapping
                with self._lock:
                    self._active_jobs.pop(sched_uuid, None)
                    
                if status == "completed":
                    self._handle_job_success(sched_uuid, in_history.get("label", ""))
                elif status == "failed":
                    self._handle_job_failure(sched_uuid, "Backend download failed")
                else:
                    self._handle_job_cancelled(sched_uuid)

    def _handle_job_success(self, uuid_str: str, title: str) -> None:
        with self._lock:
            job = self._schedules.get(uuid_str)
            if not job:
                return
            job["state"] = "Completed"
            job["last_result"] = "success"
            job["retry_count"] = 0
            job["error_message"] = ""
            
            # Record execution history (Component 10)
            self._add_history_record(uuid_str, job, "success", title)
            
            # Recalculate next occurrence
            self._update_next_run(job)
            self._stats["completed_runs"] += 1
            self._save_schedules()
            
        self._notify("Schedule Completed", job)
        self.logger.info(f"Scheduled Job Completed Successfully: {job['url']}")

    def _handle_job_failure(self, uuid_str: str, error_msg: str) -> None:
        with self._lock:
            job = self._schedules.get(uuid_str)
            if not job:
                return
                
            retry_enabled = self.settings.get("scheduler_auto_retry", True)
            max_retries = int(self.settings.get("scheduler_max_retries", 3))
            curr_retries = job.get("retry_count", 0)
            
            if retry_enabled and curr_retries < max_retries:
                # Retry schedule after 30 seconds
                job["retry_count"] = curr_retries + 1
                job["state"] = "Waiting"
                retry_time = datetime.datetime.now() + datetime.timedelta(seconds=30)
                job["next_run"] = retry_time.strftime("%Y-%m-%d %H:%M:%S")
                job["last_result"] = "failed"
                job["error_message"] = f"Retry {job['retry_count']}: {error_msg}"
                self._stats["retries"] += 1
                self._save_schedules()
                self._notify("Schedule Retried", job)
                self.logger.warning(f"Scheduled Job Failed. Retrying ({job['retry_count']}/{max_retries}): {job['url']}")
            else:
                # Mark as Failed
                job["state"] = "Failed"
                job["last_result"] = "failed"
                job["error_message"] = error_msg
                self._add_history_record(uuid_str, job, "failed", error_msg=error_msg)
                
                # Recalculate next occurrence (if repeat, we skip this failed occurrence)
                self._update_next_run(job)
                self._stats["failed_runs"] += 1
                self._save_schedules()
                self._notify("Schedule Failed", job)
                self.logger.error(f"Scheduled Job Failed: {job['url']} - {error_msg}")

    def _handle_job_cancelled(self, uuid_str: str) -> None:
        with self._lock:
            job = self._schedules.get(uuid_str)
            if not job:
                return
            job["state"] = "Cancelled"
            job["last_result"] = "cancelled"
            job["error_message"] = "Download cancelled by user"
            self._add_history_record(uuid_str, job, "cancelled", error_msg="Cancelled by user")
            self._update_next_run(job)
            self._stats["cancelled_runs"] += 1
            self._save_schedules()
            
        self._notify("Schedule Cancelled", job)
        self.logger.info(f"Scheduled Job Cancelled: {job['url']}")

    def _update_next_run(self, job: dict) -> None:
        sched_dt = datetime.datetime.strptime(job["scheduled_time"], "%Y-%m-%d %H:%M:%S")
        next_run = self.calculate_next_run(sched_dt, job["repeat_type"], datetime.datetime.now())
        if next_run:
            job["next_run"] = next_run.strftime("%Y-%m-%d %H:%M:%S")
            job["state"] = "Scheduled"
        else:
            job["next_run"] = ""
            job["state"] = "Expired"

    # ------------------------------------------------------------------
    # Time calculations helper
    # ------------------------------------------------------------------
    def calculate_next_run(self, scheduled_dt: datetime.datetime, repeat: str, last_run: datetime.datetime | None = None) -> datetime.datetime | None:
        now = datetime.datetime.now()
        start_dt = scheduled_dt
        if last_run and last_run >= start_dt:
            start_dt = last_run
            
        if repeat == "One Time":
            if scheduled_dt > now:
                return scheduled_dt
            return None
            
        if repeat == "Daily":
            dt = scheduled_dt
            while dt <= now or (last_run and dt <= last_run):
                dt += datetime.timedelta(days=1)
            return dt
            
        if repeat == "Weekly":
            dt = scheduled_dt
            while dt <= now or (last_run and dt <= last_run):
                dt += datetime.timedelta(weeks=1)
            return dt
            
        if repeat == "Monthly":
            dt = scheduled_dt
            while dt <= now or (last_run and dt <= last_run):
                month = dt.month + 1
                year = dt.year
                if month > 12:
                    month = 1
                    year += 1
                max_day = calendar.monthrange(year, month)[1]
                day = min(scheduled_dt.day, max_day)
                dt = dt.replace(year=year, month=month, day=day)
            return dt
            
        return None

    # ------------------------------------------------------------------
    # API Methods
    # ------------------------------------------------------------------
    def add_schedule(self, job_data: dict) -> str:
        with self._lock:
            jid = uuid.uuid4().hex
            job_data["uuid"] = jid
            job_data["enabled"] = job_data.get("enabled", True)
            job_data["retry_count"] = 0
            job_data["last_execution"] = None
            job_data["last_result"] = None
            job_data["error_message"] = None
            
            # Compute next run
            sched_dt = datetime.datetime.strptime(job_data["scheduled_time"], "%Y-%m-%d %H:%M:%S")
            next_run = self.calculate_next_run(sched_dt, job_data["repeat_type"])
            if next_run:
                job_data["next_run"] = next_run.strftime("%Y-%m-%d %H:%M:%S")
                job_data["state"] = "Scheduled"
            else:
                # One-time task in the past
                if sched_dt <= datetime.datetime.now():
                    job_data["next_run"] = ""
                    job_data["state"] = "Expired"
                else:
                    job_data["next_run"] = job_data["scheduled_time"]
                    job_data["state"] = "Scheduled"
            
            self._schedules[jid] = job_data
            self._save_schedules()
            self._notify("Schedule Added", job_data)
            return jid

    def edit_schedule(self, uuid_str: str, updates: dict) -> None:
        with self._lock:
            job = self._schedules.get(uuid_str)
            if not job:
                return
            job.update(updates)
            
            # Recalculate next run on updates
            sched_dt = datetime.datetime.strptime(job["scheduled_time"], "%Y-%m-%d %H:%M:%S")
            next_run = self.calculate_next_run(sched_dt, job["repeat_type"])
            if next_run:
                job["next_run"] = next_run.strftime("%Y-%m-%d %H:%M:%S")
                if job["state"] not in ("Running", "Waiting"):
                    job["state"] = "Scheduled"
            else:
                job["next_run"] = ""
                job["state"] = "Expired"
                
            self._save_schedules()
            self._notify("Schedule Updated", job)

    def delete_schedule(self, uuid_str: str) -> None:
        with self._lock:
            job = self._schedules.pop(uuid_str, None)
            self._active_jobs.pop(uuid_str, None)
            self._save_schedules()
            if job:
                self._notify("Schedule Deleted", job)

    def duplicate_schedule(self, uuid_str: str) -> str:
        with self._lock:
            job = self._schedules.get(uuid_str)
            if not job:
                raise ValueError("Schedule not found")
            dup = dict(job)
            dup.pop("uuid", None)
            dup["url"] = f"{dup['url']}"
            return self.add_schedule(dup)

    def toggle_schedule(self, uuid_str: str) -> None:
        with self._lock:
            job = self._schedules.get(uuid_str)
            if not job:
                return
            job["enabled"] = not job["enabled"]
            if job["enabled"]:
                # Re-calculate next run
                sched_dt = datetime.datetime.strptime(job["scheduled_time"], "%Y-%m-%d %H:%M:%S")
                next_run = self.calculate_next_run(sched_dt, job["repeat_type"])
                if next_run:
                    job["next_run"] = next_run.strftime("%Y-%m-%d %H:%M:%S")
                    job["state"] = "Scheduled"
                else:
                    job["next_run"] = ""
                    job["state"] = "Expired"
                self._notify("Schedule Enabled", job)
            else:
                job["state"] = "Waiting"
                self._notify("Schedule Disabled", job)
            self._save_schedules()

    def run_now(self, uuid_str: str) -> None:
        threading.Thread(target=self._execute_job_thread, args=(uuid_str,), daemon=True).start()

    def get_schedules(self) -> list[dict]:
        with self._lock:
            return [dict(j) for j in self._schedules.values()]

    def get_next_job(self) -> dict | None:
        now = datetime.datetime.now()
        soonest = None
        soonest_dt = None

        with self._lock:
            for job in self._schedules.values():
                if not job.get("enabled") or job.get("state") != "Scheduled":
                    continue
                next_run_str = job.get("next_run")
                if next_run_str:
                    next_dt = datetime.datetime.strptime(next_run_str, "%Y-%m-%d %H:%M:%S")
                    if soonest_dt is None or next_dt < soonest_dt:
                        soonest_dt = next_dt
                        soonest = job

        return dict(soonest) if soonest else None

    def pause_scheduler(self) -> None:
        with self._lock:
            from settings_panel import read_local_settings, write_local_settings
            cfg = read_local_settings(self.logger)
            cfg["scheduler_enabled"] = False
            write_local_settings(cfg)
            self.settings = cfg
        self._notify("Schedule Disabled", {})

    def resume_scheduler(self) -> None:
        with self._lock:
            from settings_panel import read_local_settings, write_local_settings
            cfg = read_local_settings(self.logger)
            cfg["scheduler_enabled"] = True
            write_local_settings(cfg)
            self.settings = cfg
        self._notify("Schedule Enabled", {})

    def get_scheduler_stats(self) -> dict[str, Any]:
        with self._lock:
            self._update_dynamic_stats()
            return dict(self._stats)

    def _update_dynamic_stats(self) -> None:
        with self._lock:
            self._stats["total_schedules"] = len(self._schedules)
            self._stats["enabled_schedules"] = sum(1 for j in self._schedules.values() if j.get("enabled", True))

    # ------------------------------------------------------------------
    # Startup Recovery (Component 9)
    # ------------------------------------------------------------------
    def _run_startup_recovery(self) -> None:
        now = datetime.datetime.now()
        run_missed = self.settings.get("scheduler_run_missed_startup", True)
        
        with self._lock:
            for job in self._schedules.values():
                if not job.get("enabled"):
                    continue
                next_run_str = job.get("next_run")
                if next_run_str:
                    next_dt = datetime.datetime.strptime(next_run_str, "%Y-%m-%d %H:%M:%S")
                    if next_dt <= now:
                        # Missed execution!
                        self._stats["missed_jobs"] += 1
                        if run_missed:
                            self.logger.info(f"Startup check: Running missed schedule: {job['url']}")
                            threading.Thread(target=self._execute_job_thread, args=(job["uuid"],), daemon=True).start()
                        else:
                            self.logger.info(f"Startup check: Skipping missed schedule (recalculating next run): {job['url']}")
                            self._update_next_run(job)
            self._save_schedules()

    # ------------------------------------------------------------------
    # Persistence (Component 8 / 10)
    # ------------------------------------------------------------------
    def _load_schedules(self) -> None:
        with self._lock:
            # Initialize default empty stats
            self._stats = {
                "total_schedules": 0,
                "enabled_schedules": 0,
                "completed_runs": 0,
                "failed_runs": 0,
                "cancelled_runs": 0,
                "retries": 0,
                "missed_jobs": 0,
                "last_execution": None
            }
            if os.path.exists(SCHEDULER_FILE):
                try:
                    with open(SCHEDULER_FILE, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    
                    version = data.get("schema_version", 1)
                    if "schema_version" not in data:
                        # Legacy format (migration from version 0 to 1)
                        self.logger.info("Migrating legacy scheduler schema to version 1.")
                        self._schedules = data.get("schedules", {})
                        self._save_schedules()
                    elif version == 1:
                        self._schedules = data.get("schedules", {})
                        self._stats.update(data.get("stats", {}))
                    elif version < 1:
                        # Older version migration placeholder
                        self.logger.info(f"Migrating older scheduler schema version {version} to 1.")
                        self._schedules = data.get("schedules", {})
                        self._stats.update(data.get("stats", {}))
                        self._save_schedules()
                    else:
                        # Newer version - ignore safely and log warning
                        self.logger.warning(
                            f"Scheduler file schema version ({version}) is newer than supported version (1). "
                            "Ignoring file contents safely to prevent corruption."
                        )
                        self._schedules = {}
                except Exception as e:
                    self.logger.warning(f"Corrupted schedules JSON. Backing up and renewing: {e}")
                    try:
                        os.rename(SCHEDULER_FILE, SCHEDULER_FILE.replace(".json", ".corrupt.json"))
                    except OSError:
                        pass
                    self._schedules = {}
            else:
                self._schedules = {}
            self._update_dynamic_stats()

    def _save_schedules(self) -> None:
        with self._lock:
            self._update_dynamic_stats()
            data = {
                "schema_version": 1,
                "schedules": self._schedules,
                "stats": self._stats
            }
            tmp = SCHEDULER_FILE + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, SCHEDULER_FILE)
            except Exception as e:
                self.logger.error(f"Failed to save schedules: {e}")
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

    def _add_history_record(self, uuid_str: str, job: dict, status: str, title: str = "", error_msg: str = "") -> None:
        # Construct record (Component 10)
        rec = {
            "schedule_uuid": uuid_str,
            "job_id": self._active_jobs.get(uuid_str, ""),
            "title": title or job.get("url"),
            "url": job["url"],
            "mode": job["mode"],
            "scheduled_time": job["scheduled_time"],
            "started_time": job.get("last_execution") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": status,
            "retry_count": job.get("retry_count", 0),
            "error_message": error_msg
        }
        
        # Load and append
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
                    history = json.load(fh)
                    if not isinstance(history, list):
                        history = []
            except Exception:
                pass
                
        history.insert(0, rec)
        history = history[:500]  # trim to 500 records
        
        # Save atomically
        tmp = HISTORY_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(history, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, HISTORY_FILE)
        except Exception as e:
            self.logger.error(f"Failed to save history: {e}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
