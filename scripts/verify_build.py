"""Verify build artifacts produced by PyInstaller and release pipeline."""

import argparse
import os
import sys
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_ARTIFACTS = [
    "dist/MediaForge.exe",
    "dist/MediaForgeBackend.exe",
    "dist/MediaForgeService.exe",
]


def check_exe(rel_path, base_dir=PROJECT_ROOT):
    full_path = os.path.join(base_dir, rel_path)
    if not os.path.exists(full_path):
        return f"MISSING: {rel_path}"
    if os.path.getsize(full_path) == 0:
        return f"EMPTY: {rel_path}"
    return None


def check_portable_zip(rel_path, base_dir=PROJECT_ROOT):
    full_path = os.path.join(base_dir, rel_path)
    if not os.path.exists(full_path):
        return f"MISSING: {rel_path}"
    if os.path.getsize(full_path) == 0:
        return f"EMPTY: {rel_path}"
    try:
        with zipfile.ZipFile(full_path, "r") as zf:
            names = zf.namelist()
            if not names:
                return f"EMPTY_ZIP: {rel_path} (no files inside)"
    except zipfile.BadZipFile:
        return f"CORRUPT: {rel_path} (not a valid ZIP)"
    return None


def verify_artifacts(base_dir=PROJECT_ROOT) -> list[str]:
    """Verify all required build EXEs exist and are non-empty."""
    errors = []
    for exe_path in REQUIRED_ARTIFACTS:
        err = check_exe(exe_path, base_dir=base_dir)
        if err:
            errors.append(err)
            print(f"  FAIL  {exe_path}")
        else:
            exe_full = os.path.join(base_dir, exe_path)
            size = os.path.getsize(exe_full)
            size_mb = size / (1024 * 1024)
            print(f"  OK    {exe_path} ({size_mb:.1f} MB)")
    return errors


def main():
    parser = argparse.ArgumentParser(description="Verify MediaForge build artifacts")
    parser.add_argument("--release", action="store_true", help="Also verify release artifacts")
    args = parser.parse_args()

    print("[verify_build] Verifying build artifacts...")

    errors = verify_artifacts()

    if args.release:
        print()
        zip_path = "release/MediaForge_Portable.zip"
        err = check_portable_zip(zip_path)
        if err:
            errors.append(err)
            print(f"  FAIL  {zip_path}")
        else:
            zip_full = os.path.join(PROJECT_ROOT, zip_path)
            zip_size = os.path.getsize(zip_full)
            zip_size_mb = zip_size / (1024 * 1024)
            print(f"  OK    {zip_path} ({zip_size_mb:.1f} MB)")

        setup_path = "release/MediaForge-Setup.exe"
        err = check_exe(setup_path)
        if err:
            errors.append(err)
            print(f"  FAIL  {setup_path}")
        else:
            setup_full = os.path.join(PROJECT_ROOT, setup_path)
            setup_size = os.path.getsize(setup_full)
            setup_size_mb = setup_size / (1024 * 1024)
            print(f"  OK    {setup_path} ({setup_size_mb:.1f} MB)")

    if errors:
        print("\nERRORS:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("\nAll build artifacts verified OK")
    return 0


if __name__ == "__main__":
    main()
