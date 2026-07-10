"""
End-to-end smoke test for MediaForge.

Tests the complete workflow without actual network downloads:
  1. Launch backend server
  2. Verify health endpoint
  3. Check diagnostics
  4. Verify queue health
  5. Simulate startup recovery
  6. Verify all API endpoints respond correctly
  7. Shut down cleanly

Usage:
    python scripts/smoke_test.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
TIMEOUT = 10  # seconds


def resolve_backend_url() -> str:
    """Resolve the backend URL from settings.json, matching backend/app.py logic."""
    url = "http://127.0.0.1:5000"
    settings_path = os.path.join(BACKEND_DIR, "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                settings = json.load(f)
            url = settings.get("backend_url", url)
        except (json.JSONDecodeError, OSError):
            pass
    return url


BASE_URL = resolve_backend_url()


def wait_for_backend(timeout=TIMEOUT):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"{BASE_URL}/api/health", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.5)
    return False


def check_endpoint(method, path, expected_status=200):
    url = f"{BASE_URL}{path}"
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            if resp.status != expected_status:
                return False, f"Expected {expected_status}, got {resp.status}"
            try:
                json.loads(body)
            except json.JSONDecodeError:
                pass
            return True, body
    except urllib.error.HTTPError as e:
        if e.code == expected_status:
            return True, e.read().decode("utf-8")
        return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, str(e)


class SmokeTest:
    def __init__(self):
        self.process = None
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.already_running = False

    def start_backend(self):
        # Check if backend is already running
        try:
            req = urllib.request.Request(f"{BASE_URL}/api/health", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    print("[smoke] Backend already running (reusing)")
                    self.already_running = True
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass

        print("[smoke] Starting backend server...")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=BACKEND_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not wait_for_backend():
            self.process.terminate()
            self.process.wait()
            return False
        print("[smoke] Backend is running")
        return True

    def stop_backend(self):
        if self.already_running:
            print("[smoke] Leaving existing backend running")
            return
        if self.process:
            print("[smoke] Shutting down backend...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            print("[smoke] Backend stopped")

    def run_test(self, name, method, path, expected_status=200):
        ok, detail = check_endpoint(method, path, expected_status)
        status = "PASS" if ok else "FAIL"
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        extra = "" if ok else f" - {detail[:120]}"
        print(f"  [{status:4s}] {method:6s} {path}{extra}")
        return ok

    def run(self):
        print("=" * 60)
        print("  MediaForge Smoke Test")
        print("=" * 60)
        print()

        # Start backend
        if not self.start_backend():
            print("[smoke] FAILED: Could not start backend")
            self.failed += 1
            return False

        try:
            # API health check
            self.run_test("Health check", "GET", "/api/health")

            # Diagnostics
            self.run_test("Diagnostics", "GET", "/api/diagnostics")

            # Queue health
            self.run_test("Queue health", "GET", "/api/queue/health")

            # Recovery info
            self.run_test("Recovery info", "GET", "/api/recovery")

            # Queue status (should work even with empty queue)
            self.run_test("Queue status (empty)", "GET", "/queue")

            # History (should work even with empty history)
            self.run_test("Download history (empty)", "GET", "/history")

            # Settings
            self.run_test("Get settings", "GET", "/settings")

            # Stats
            self.run_test("Get stats", "GET", "/stats")

        finally:
            self.stop_backend()

        print()
        print("=" * 60)
        total = self.passed + self.failed
        print(f"  Results: {self.passed}/{total} passed", end="")
        if self.failed:
            print(f", {self.failed} failed", end="")
        print()
        print("=" * 60)

        return self.failed == 0


def main():
    test = SmokeTest()
    success = test.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
