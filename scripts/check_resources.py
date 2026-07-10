"""Verify that all required resource files exist and are non-empty."""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_RESOURCES = [
    ("companion/resources/icon.ico", "Companion application icon"),
    ("companion/resources/icon.png", "Companion application PNG icon"),
    ("companion/resources/tray.ico", "System tray icon"),
    ("extension/icon.png", "Extension icon"),
    ("extension/manifest.json", "Extension manifest"),
    ("VERSION", "Project version file"),
    ("MediaForge Backend.bat", "Backend launcher script"),
]


def check_file(rel_path, description):
    full_path = os.path.join(PROJECT_ROOT, rel_path)
    if not os.path.exists(full_path):
        return f"MISSING: {rel_path} ({description})"
    if os.path.getsize(full_path) == 0:
        return f"EMPTY: {rel_path} ({description})"
    return None


def main():
    errors = []
    print("[check_resources] Verifying required resource files...")
    for rel_path, desc in REQUIRED_RESOURCES:
        err = check_file(rel_path, desc)
        if err:
            errors.append(err)
            print(f"  FAIL  {rel_path}")
        else:
            print(f"  OK    {rel_path}")

    existing_resources = []
    resources_dir = os.path.join(PROJECT_ROOT, "companion", "resources")
    if os.path.isdir(resources_dir):
        existing_resources = os.listdir(resources_dir)

    print(f"\n  Total files in companion/resources/: {len(existing_resources)}")

    if errors:
        print("\nERRORS:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("\nAll resources OK")
    return 0


if __name__ == "__main__":
    main()
