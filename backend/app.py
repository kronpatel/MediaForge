from flask import Flask
from flask import jsonify
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


app = Flask(__name__)
CORS(app)


@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "name": "MediaForge Backend",
        "version": "1.0",
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


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        threaded=True,
    )
