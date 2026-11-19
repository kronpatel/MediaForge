"""
url_paste.py – URL extraction helpers for direct paste / drag-and-drop.

Pure functions (no GUI, no backend) so they can be unit-tested without a
display.  Used by the Companion's "Direct Download" panel, the global
Ctrl+V paste handler, and the OLE drag-and-drop target.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# UI label -> backend download mode.  Mirrors extension/content.js
# DOWNLOAD_OPTIONS so the Companion offers the same choices as the extension.
MODE_LABELS: dict[str, str] = {
    "MP3 Audio": "mp3",
    "FLAC Lossless": "flac",
    "MP4 1080p": "1080p",
    "MP4 4K": "4k",
    "MP4 8K": "8k",
    "Playlist MP3": "playlist_mp3",
    "Playlist FLAC Lossless": "playlist_flac",
}

# Trailing characters users often tack onto a copied URL but that are not part
# of the URL itself (sentence punctuation, wrapping brackets/parens).
_TRAILING_PUNCT = ".,;:!?'\"`)]}>"
_WRAP_CHARS = "<>"

_URL_TOKEN_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def _clean_token(token: str) -> str:
    token = token.strip(_WRAP_CHARS).strip()
    while token and token[-1] in _TRAILING_PUNCT:
        token = token[:-1]
    return token


def extract_urls(text: str) -> list[str]:
    """Extract valid http(s) URLs from arbitrary text (clipboard / drop data).

    * ``https://…`` and ``http://…`` tokens are taken as-is.
    * ``www.…`` tokens get ``http://`` prepended.
    * Trailing sentence punctuation and wrapping brackets are stripped.
    * Invalid tokens are dropped; duplicates are removed (order preserved).
    """
    if not text:
        return []

    found: list[str] = []
    seen: set[str] = set()

    for raw in _URL_TOKEN_RE.findall(text):
        url = _clean_token(raw)
        if url.startswith("www."):
            url = "http://" + url

        try:
            parsed = urlparse(url)
        except ValueError:
            continue

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue

        if url in seen:
            continue
        seen.add(url)
        found.append(url)

    return found


def normalize_mode(label: str) -> str:
    """Map a UI mode label to a backend mode, defaulting to 1080p."""
    return MODE_LABELS.get(label, "1080p")
