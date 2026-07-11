# GitHub Actions Workflow Hang Investigation Report

This document details the investigation into why the GitHub Actions CI workflow hangs in the "In Progress" state and why the release version remains at v1.2.0.

---

## 1. Summary of Findings

- **Hanging Job:** `test` (runs on `windows-latest`).
- **Hanging Step:** `Run tests` (line 53 of `.github/workflows/tests.yml`).
- **Hanging Command:** `python -m unittest discover -p "test_*.py" -v` in the `companion` directory.

---

## 2. Root Cause Analysis

### Cause A: Headless CustomTkinter Widget Instantiation
The file [companion/test_integration.py](file:///d:/MediaForge/companion/test_integration.py) is named `test_integration.py`, which matches the discovery pattern `test_*.py`. 
- During `unittest discover`, this file is loaded and executed.
- `setUpClass()` in `RealWidgetValidationTest` calls `cls.root = ctk.CTk()`, which attempts to instantiate a real CustomTkinter window.
- In a headless CI runner (without a virtual framebuffer or active desktop session), instantiating real GUI widgets blocks the Tkinter main loop or causes GDI initialization blocks, hanging the runner indefinitely.
- The file itself contains a warning comment stating it must *not* be run as part of the automatic unit test suite, but its filename violates this requirement.

### Cause B: Subprocess stdin Leakage
In [companion/backend_manager.py](file:///d:/MediaForge/companion/backend_manager.py) (`_launch_backend()`), the Flask backend is spawned using `subprocess.Popen([python_exe, "app.py"], ...)`.
- While `stdout` and `stderr` are redirected to `subprocess.DEVNULL`, `stdin` is not redirected.
- The background Python process inherits the parent process's standard input. On GitHub Actions runners, this shared stream state prevents the shell environment from registering process completion, resulting in a hang even after the parent runner exits.

### Cause C: Orphan Flask Processes on Failure
In [companion/test_lifecycle.py](file:///d:/MediaForge/companion/test_lifecycle.py), cycles of backend start/stop are executed.
- If any test assertion fails in the middle of a cycle, the test suite aborts immediately.
- The `tearDownClass()` cleanup logic is bypassed, leaving the Flask backend process running in the background.
- Because a child process is still active and holding open handles, the GitHub Actions step runner waits indefinitely for all spawned subprocesses to release standard handles before concluding the step.

### Cause D: Absence of Workflow Job Timeout
The workflow file [tests.yml](file:///d:/MediaForge/.github/workflows/tests.yml) lacks a `timeout-minutes` property. If a job or step hangs, it runs until the default GitHub Actions timeout (360 minutes / 6 hours), wasting runner minutes and delaying build feedback.

---

## 3. Recommended Fixes

### Fix A: Exclude Integration Tests from Auto-Discovery
Rename `companion/test_integration.py` to `companion/integration_test.py` or `companion/verify_integration.py` so it does not match the `test_*.py` pattern. This ensures it is only run manually as intended, preventing headless GUI initialization blocks in the CI.

### Fix B: Redirect stdin in Popen
Modify [companion/backend_manager.py](file:///d:/MediaForge/companion/backend_manager.py) to explicitly redirect `stdin` to `subprocess.DEVNULL`:
```python
        return subprocess.Popen(
            [python_exe, "app.py"],
            cwd=BACKEND_DIR,
            creationflags=creation_flags,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
```

### Fix C: Add Workflow Cleanup Step and Timeout
1. Configure `timeout-minutes: 10` on the `test` job and `Run tests` step in `.github/workflows/tests.yml`.
2. Add a post-test cleanup step using `always()` conditional to force-kill any leaked python/backend processes:
```yaml
      - name: Cleanup leaked backend processes
        if: always()
        run: |
          taskkill /F /IM python.exe /T 2>nul || exit 0
```

---

## Final Verdict

**READY TO FIX**
