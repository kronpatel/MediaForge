"""Verify that the VERSION file matches all version references across the project."""

import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_version_file():
    path = os.path.join(PROJECT_ROOT, "VERSION")
    with open(path, "r") as f:
        return f.read().strip()


def check_backend_app(lines):
    for line in lines:
        m = re.search(r'"version":\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return None


def check_backend_downloader(lines):
    for line in lines:
        m = re.search(r'"version":\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return None


def check_companion_updater(lines):
    for line in lines:
        m = re.search(r'COMPANION_VERSION\s*=\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return None


def check_extension_manifest():
    path = os.path.join(PROJECT_ROOT, "extension", "manifest.json")
    with open(path, "r") as f:
        content = f.read()
    m = re.search(r'"version":\s*"([^"]+)"', content)
    return m.group(1) if m else None


def check_changelog(lines):
    for line in lines:
        m = re.search(r'^## \[([\d.]+)\]', line)
        if m:
            return m.group(1)
    return None


def read_file_lines(relative_path):
    path = os.path.join(PROJECT_ROOT, relative_path)
    with open(path, "r") as f:
        return f.readlines()


def main():
    expected = read_version_file()
    errors = []

    mappings = {
        "VERSION": expected,
        "backend/app.py": None,
        "backend/downloader.py": None,
        "backend/diagnostics.py": None,
        "companion/updater.py": None,
        "extension/manifest.json": None,
        "CHANGELOG.md": None,
    }

    # backend/app.py
    lines = read_file_lines("backend/app.py")
    ver = check_backend_app(lines)
    if ver != expected:
        errors.append(f"backend/app.py version {ver!r} != VERSION {expected!r}")
    mappings["backend/app.py"] = ver

    # backend/downloader.py
    lines = read_file_lines("backend/downloader.py")
    ver = check_backend_downloader(lines)
    if ver != expected:
        errors.append(f"backend/downloader.py version {ver!r} != VERSION {expected!r}")
    mappings["backend/downloader.py"] = ver

    # backend/diagnostics.py
    lines = read_file_lines("backend/diagnostics.py")
    ver = check_backend_app(lines)
    if ver != expected:
        errors.append(f"backend/diagnostics.py version {ver!r} != VERSION {expected!r}")
    mappings["backend/diagnostics.py"] = ver

    # companion/updater.py
    lines = read_file_lines("companion/updater.py")
    ver = check_companion_updater(lines)
    if ver != expected:
        errors.append(f"companion/updater.py COMPANION_VERSION {ver!r} != VERSION {expected!r}")
    mappings["companion/updater.py"] = ver

    # extension/manifest.json
    ver = check_extension_manifest()
    if ver != expected:
        errors.append(f"extension/manifest.json version {ver!r} != VERSION {expected!r}")
    mappings["extension/manifest.json"] = ver

    # CHANGELOG.md
    lines = read_file_lines("CHANGELOG.md")
    ver = check_changelog(lines)
    if ver != expected:
        errors.append(f"CHANGELOG.md version {ver!r} != VERSION {expected!r}")
    mappings["CHANGELOG.md"] = ver

    print(f"[verify_versions] VERSION = {expected}")
    for filepath, ver in mappings.items():
        status = "OK" if ver == expected else "MISMATCH"
        print(f"  {status:10s} {filepath}: {ver}")

    if errors:
        print("\nERRORS:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print(f"\nAll versions match VERSION = {expected}")
    return 0


if __name__ == "__main__":
    main()
