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


def check_extension_background_js(lines):
    for line in lines:
        m = re.search(r'MediaForge v([\d.]+) installed', line)
        if m:
            return m.group(1)
    return None


def check_extension_content_js(lines):
    for line in lines:
        m = re.search(r'const VERSION\s*=\s*"([\d.]+)"', line)
        if m:
            return m.group(1)
    return None


def check_extension_settings_js(lines):
    for line in lines:
        m = re.search(r'\|\|\s*"([\d.]+)"', line)
        if m:
            return m.group(1)
    return None


def check_extension_settings_html(lines):
    for line in lines:
        m = re.search(r'MediaForge v([\d.]+)', line)
        if m:
            return m.group(1)
    return None


def check_installer_iss(lines):
    for line in lines:
        m = re.search(r'#define MyAppVersion\s+"([\d.]+)"', line)
        if m:
            return m.group(1)
    return None


def check_companion_settings_panel(lines):
    for line in lines:
        m = re.search(r'Current: v([\d.]+)', line)
        if m:
            return m.group(1)
    return None


def main():
    expected = read_version_file()
    errors = []

    mappings = {
        "VERSION": expected,
        "backend/app.py": None,
        "backend/downloader.py": None,
        "backend/diagnostics.py": None,
        "companion/updater.py": None,
        "companion/settings_panel.py": None,
        "extension/manifest.json": None,
        "extension/background.js": None,
        "extension/content.js": None,
        "extension/settings.js": None,
        "extension/settings.html": None,
        "installer.iss": None,
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

    # companion/settings_panel.py
    lines = read_file_lines("companion/settings_panel.py")
    ver = check_companion_settings_panel(lines)
    if ver != expected:
        errors.append(f"companion/settings_panel.py version {ver!r} != VERSION {expected!r}")
    mappings["companion/settings_panel.py"] = ver

    # extension/manifest.json
    ver = check_extension_manifest()
    if ver != expected:
        errors.append(f"extension/manifest.json version {ver!r} != VERSION {expected!r}")
    mappings["extension/manifest.json"] = ver

    # extension/background.js
    lines = read_file_lines("extension/background.js")
    ver = check_extension_background_js(lines)
    if ver != expected:
        errors.append(f"extension/background.js version {ver!r} != VERSION {expected!r}")
    mappings["extension/background.js"] = ver

    # extension/content.js
    lines = read_file_lines("extension/content.js")
    ver = check_extension_content_js(lines)
    if ver != expected:
        errors.append(f"extension/content.js version {ver!r} != VERSION {expected!r}")
    mappings["extension/content.js"] = ver

    # extension/settings.js
    lines = read_file_lines("extension/settings.js")
    ver = check_extension_settings_js(lines)
    if ver != expected:
        errors.append(f"extension/settings.js fallback version {ver!r} != VERSION {expected!r}")
    mappings["extension/settings.js"] = ver

    # extension/settings.html
    lines = read_file_lines("extension/settings.html")
    ver = check_extension_settings_html(lines)
    if ver != expected:
        errors.append(f"extension/settings.html version {ver!r} != VERSION {expected!r}")
    mappings["extension/settings.html"] = ver

    # installer.iss
    lines = read_file_lines("installer.iss")
    ver = check_installer_iss(lines)
    if ver != expected:
        errors.append(f"installer.iss version {ver!r} != VERSION {expected!r}")
    mappings["installer.iss"] = ver

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
