import re

from flask import Flask, jsonify, send_file
from flask import request
from flask_cors import CORS

from downloader import KerzoxDownloadError
from downloader import clear_history
from downloader import get_download_status
from downloader import get_queue_status
from downloader import queue_download
from downloader import read_history
from downloader import read_settings
from downloader import reset_download_folder
from downloader import select_download_folder
from downloader import write_settings
from downloader import get_statistics
from downloader import retry_job
from downloader import remove_job
from downloader import cancel_job
from downloader import pause_job
from downloader import resume_job
from downloader import set_job_priority
from downloader import set_job_position

from recovery import recover_queue

from diagnostics import health_check
from diagnostics import diagnostics_report
from diagnostics import export_diagnostics
from diagnostics import run_startup_self_check
from diagnostics import check_queue_health
from diagnostics import recovery_dashboard_data


app = Flask(__name__)
CORS(app, origins=[
    re.compile(r"^http://127\.0\.0\.1:\d+$"),
    re.compile(r"^http://localhost:\d+$"),
    re.compile(r"^chrome-extension://[a-z0-9]{32}$"),
    re.compile(r"^moz-extension://[a-f0-9\-]+$"),
])


# ── Startup Recovery & Self Check ──────────────────────────────────────────────
try:
    recovery_result = recover_queue()
    if recovery_result.jobs_restored > 0:
        app.logger.info("Restored %d job(s) from queue state", recovery_result.jobs_restored)
    if recovery_result.jobs_recovered > 0:
        app.logger.info("Recovered %d interrupted job(s)", recovery_result.jobs_recovered)
    if recovery_result.files_cleaned > 0:
        app.logger.info("Cleaned %d temp file(s)", recovery_result.files_cleaned)
    if not recovery_result.history_consistent:
        app.logger.warning("History had %d duplicate(s) removed", recovery_result.history_duplicates_removed)
except Exception as recovery_err:
    app.logger.error("Recovery error: %s", recovery_err)

try:
    self_check = run_startup_self_check()
    if self_check.warnings > 0:
        for check in self_check.checks:
            if not check["passed"]:
                app.logger.warning("Self-check: %s", check["message"])
except Exception as check_err:
    app.logger.error("Self-check error: %s", check_err)
# ──────────────────────────────────────────────────────────────────────────────


@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "name": "MediaForge Backend",
        "version": "1.2.1",
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify(health_check())


@app.route("/api/diagnostics", methods=["GET"])
def api_diagnostics():
    return jsonify(diagnostics_report())


@app.route("/api/diagnostics/export", methods=["POST"])
def api_diagnostics_export():
    try:
        path = export_diagnostics()
        return jsonify({"success": True, "path": path, "message": "Diagnostics exported"})
    except Exception as error:
        return jsonify({"success": False, "message": f"Export failed: {error}"}), 500


@app.route("/api/recovery", methods=["GET"])
def api_recovery():
    return jsonify(recovery_dashboard_data())


@app.route("/api/queue/health", methods=["GET"])
def api_queue_health():
    result = check_queue_health()
    return jsonify({
        "healthy": result.healthy,
        "issues": result.issues,
        "repaired": result.repaired,
    })


@app.route("/download", methods=["POST"])
def download():
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url")
        mode = data.get("mode")
        retries = int(data.get("retries", 2))

        if not url:
            return jsonify({
                "success": False,
                "message": "URL missing",
            }), 400

        if not mode:
            return jsonify({
                "success": False,
                "message": "Download mode missing",
            }), 400

        job = queue_download(url=url, mode=mode, retries=retries)

        return jsonify({
            "success": True,
            "message": "Download added to queue",
            "job": job,
            "job_id": job["id"],
        }), 202

    except KerzoxDownloadError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400

    except ValueError:
        return jsonify({
            "success": False,
            "message": "Retries must be a number",
        }), 400

    except Exception as error:
        return jsonify({
            "success": False,
            "message": f"Backend error: {error}",
        }), 500


@app.route("/status/<job_id>", methods=["GET"])
def download_status(job_id):
    job = get_download_status(job_id)

    if not job:
        return jsonify({
            "success": False,
            "message": "Download job not found",
        }), 404

    return jsonify({
        "success": True,
        "job": job,
    })


@app.route("/queue", methods=["GET"])
def queue_status():
    return jsonify({
        "success": True,
        "queue": get_queue_status(),
    })


@app.route("/stats", methods=["GET"])
def get_stats():
    return jsonify({
        "success": True,
        "stats": get_statistics(),
    })


@app.route("/history", methods=["GET"])
def download_history():
    return jsonify({
        "success": True,
        "history": read_history(),
    })


@app.route("/history/clear", methods=["POST"])
def clear_download_history():
    try:
        clear_history()
        return jsonify({
            "success": True,
            "message": "History cleared successfully",
        })
    except Exception as error:
        return jsonify({
            "success": False,
            "message": f"Could not clear history: {error}",
        }), 500


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "GET":
        return jsonify({
            "success": True,
            "settings": read_settings(),
        })

    try:
        data = request.get_json(silent=True) or {}
        updated_settings = write_settings(data)

        return jsonify({
            "success": True,
            "message": "Settings saved",
            "settings": updated_settings,
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "message": f"Could not save settings: {error}",
        }), 400


@app.route("/settings/reset-folder", methods=["POST"])
def reset_folder():
    return jsonify({
        "success": True,
        "message": "Download folder reset",
        "settings": reset_download_folder(),
    })


@app.route("/settings/select-folder", methods=["POST"])
def select_folder():
    try:
        return jsonify({
            "success": True,
            "message": "Download folder selected",
            "settings": select_download_folder(),
        })

    except KerzoxDownloadError as error:
        return jsonify({
            "success": False,
            "message": str(error),
        }), 400


@app.route("/queue/retry", methods=["POST"])
def queue_retry():
    try:
        data = request.get_json(silent=True) or {}
        job_id = data.get("job_id")
        if not job_id:
            return jsonify({"success": False, "message": "job_id missing"}), 400
        job = retry_job(job_id)
        return jsonify({"success": True, "job": job, "job_id": job["id"]}), 200
    except KerzoxDownloadError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({"success": False, "message": f"Backend error: {error}"}), 500


@app.route("/queue/remove", methods=["POST"])
def queue_remove():
    try:
        data = request.get_json(silent=True) or {}
        job_id = data.get("job_id")
        if not job_id:
            return jsonify({"success": False, "message": "job_id missing"}), 400
        remove_job(job_id)
        return jsonify({"success": True, "message": "Job removed"}), 200
    except KerzoxDownloadError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({"success": False, "message": f"Backend error: {error}"}), 500


@app.route("/queue/cancel", methods=["POST"])
def queue_cancel():
    try:
        data = request.get_json(silent=True) or {}
        job_id = data.get("job_id")
        if not job_id:
            return jsonify({"success": False, "message": "job_id missing"}), 400
        job = cancel_job(job_id)
        return jsonify({"success": True, "job": job, "job_id": job["id"]}), 200
    except KerzoxDownloadError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({"success": False, "message": f"Backend error: {error}"}), 500


@app.route("/queue/pause", methods=["POST"])
def queue_pause():
    try:
        data = request.get_json(silent=True) or {}
        job_id = data.get("job_id")
        if not job_id:
            return jsonify({"success": False, "message": "job_id missing"}), 400
        pause_job(job_id)
        return jsonify({"success": True, "message": "Job paused"}), 200
    except KerzoxDownloadError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({"success": False, "message": f"Backend error: {error}"}), 500


@app.route("/queue/resume", methods=["POST"])
def queue_resume():
    try:
        data = request.get_json(silent=True) or {}
        job_id = data.get("job_id")
        if not job_id:
            return jsonify({"success": False, "message": "job_id missing"}), 400
        resume_job(job_id)
        return jsonify({"success": True, "message": "Job resumed"}), 200
    except KerzoxDownloadError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({"success": False, "message": f"Backend error: {error}"}), 500


@app.route("/queue/priority", methods=["POST"])
def queue_priority():
    try:
        data = request.get_json(silent=True) or {}
        job_id = data.get("job_id")
        priority = data.get("priority", "normal")
        if not job_id:
            return jsonify({"success": False, "message": "job_id missing"}), 400
        if priority not in ("high", "normal", "low"):
            return jsonify({"success": False, "message": "Invalid priority"}), 400
        set_job_priority(job_id, priority)
        return jsonify({"success": True, "message": "Priority updated"}), 200
    except KerzoxDownloadError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({"success": False, "message": f"Backend error: {error}"}), 500


@app.route("/queue/reorder", methods=["POST"])
def queue_reorder():
    try:
        data = request.get_json(silent=True) or {}
        job_id = data.get("job_id")
        new_index = data.get("new_index")
        if not job_id or new_index is None:
            return jsonify({"success": False, "message": "job_id and new_index required"}), 400
        set_job_position(job_id, int(new_index))
        return jsonify({"success": True, "message": "Job reordered"}), 200
    except KerzoxDownloadError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception as error:
        return jsonify({"success": False, "message": f"Backend error: {error}"}), 500


if __name__ == "__main__":
    from urllib.parse import urlparse
    settings = read_settings()
    url = settings.get("backend_url", "http://127.0.0.1:5000")
    host = "127.0.0.1"
    port = 5000
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            host = parsed.hostname
        if parsed.port:
            port = parsed.port
    except Exception:
        pass

    app.run(
        host=host,
        port=port,
        debug=True,
        threaded=True,
        use_reloader=False,
    )
