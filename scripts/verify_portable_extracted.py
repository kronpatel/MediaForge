import os
import sys
import zipfile
import shutil
import subprocess
import time
import psutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP_PATH = os.path.join(PROJECT_ROOT, "release", "MediaForge_Portable.zip")
EXTRACT_DIR = os.path.join(PROJECT_ROOT, "release", "portable_test_extracted")

EXPECTED_FILES = [
    "MediaForge.exe",
    "VERSION",
    "MediaForge Backend.bat",
    "backend/app.py",
    "backend/downloader.py",
    "ffmpeg/ffmpeg.exe",
    "ffmpeg/ffprobe.exe",
    "mediaforge_start.bat",
    "portable_settings.json"
]

def clean_extract_dir():
    if os.path.exists(EXTRACT_DIR):
        print(f"[Portable Test] Cleaning existing extraction directory {EXTRACT_DIR}...")
        shutil.rmtree(EXTRACT_DIR, ignore_errors=True)
        time.sleep(1.0)

def main():
    print("=" * 70)
    print("[Portable Test] Starting portable release package verification...")
    
    if not os.path.exists(ZIP_PATH):
        print(f"ERROR: {ZIP_PATH} does not exist. Run release.bat first.")
        sys.exit(1)
        
    clean_extract_dir()
    os.makedirs(EXTRACT_DIR, exist_ok=True)
    
    # 1. Extract ZIP
    print(f"[Portable Test] Extracting {ZIP_PATH} to {EXTRACT_DIR}...")
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        zf.extractall(EXTRACT_DIR)
        
    # 2. Check files
    print("[Portable Test] Checking files in extracted directory...")
    missing = []
    for f in EXPECTED_FILES:
        p = os.path.join(EXTRACT_DIR, f)
        if not os.path.exists(p):
            missing.append(f)
            print(f"  FAIL  {f} is missing")
        else:
            print(f"  OK    {f}")
            
    if missing:
        print(f"ERROR: {len(missing)} files missing from the portable package!")
        sys.exit(1)
        
    # 3. Verify launch behavior of the portable build
    print("[Portable Test] Launching portable build to check startup...")
    exe_path = os.path.join(EXTRACT_DIR, "MediaForge.exe")
    
    # Run the portable MediaForge.exe
    proc = subprocess.Popen([exe_path], cwd=EXTRACT_DIR)
    print(f"[Portable Test] Started MediaForge.exe (PID {proc.pid})")
    
    # Let it run for 10 seconds to generate files, configs, and check status
    time.sleep(10.0)
    
    # Check if the process or child process is running
    is_running = False
    for p in psutil.process_iter(attrs=['pid', 'name', 'exe']):
        try:
            if p.info['name'] == 'MediaForge.exe' or (p.info['exe'] and os.path.basename(p.info['exe']) == 'MediaForge.exe'):
                is_running = True
                print(f"[Portable Test] Verified running process: {p.info['name']} (PID={p.info['pid']})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
            
    # Terminate the test processes
    print("[Portable Test] Cleaning up test processes...")
    proc.terminate()
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        
    for p in psutil.process_iter(attrs=['name']):
        try:
            if p.info['name'] == 'MediaForge.exe':
                p.kill()
        except Exception:
            pass
            
    if is_running:
        print("[Portable Test] SUCCESS - Portable package is valid and starts successfully.")
        print("=" * 70)
        sys.exit(0)
    else:
        print("ERROR: MediaForge.exe did not start or exited immediately.")
        print("=" * 70)
        sys.exit(1)

if __name__ == "__main__":
    main()
