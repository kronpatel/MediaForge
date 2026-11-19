"""
test_url_paste.py – Tests for url_paste.extract_urls / MODE_LABELS.

Pure functions; no display or backend required.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from url_paste import extract_urls, MODE_LABELS, normalize_mode


class TestExtractUrls(unittest.TestCase):
    """URL extraction from arbitrary text (clipboard / drop data)."""

    def test_empty_input(self):
        self.assertEqual(extract_urls(""), [])

    def test_single_url(self):
        self.assertEqual(
            extract_urls("https://youtu.be/dQw4w9WgXcQ"),
            ["https://youtu.be/dQw4w9WgXcQ"],
        )

    def test_multiple_urls(self):
        text = "Get them here: https://youtu.be/aaa and https://youtu.be/bbb"
        self.assertEqual(
            extract_urls(text),
            ["https://youtu.be/aaa", "https://youtu.be/bbb"],
        )

    def test_trailing_punctuation_stripped(self):
        self.assertEqual(
            extract_urls("Watch https://youtu.be/xyz."),
            ["https://youtu.be/xyz"],
        )
        self.assertEqual(
            extract_urls("Links: https://youtu.be/xyz, https://youtu.be/abc!"),
            ["https://youtu.be/xyz", "https://youtu.be/abc"],
        )

    def test_wrapped_in_angle_brackets(self):
        self.assertEqual(
            extract_urls("<https://youtu.be/xyz>"),
            ["https://youtu.be/xyz"],
        )

    def test_www_fallback_gets_http_scheme(self):
        self.assertEqual(
            extract_urls("Go to www.youtube.com/watch?v=xyz"),
            ["http://www.youtube.com/watch?v=xyz"],
        )

    def test_invalid_tokens_ignored(self):
        self.assertEqual(extract_urls("ftp://example.com"), [])
        self.assertEqual(extract_urls("not a url at all"), [])
        self.assertEqual(extract_urls("http://"), [])

    def test_duplicates_removed_order_preserved(self):
        text = "https://youtu.be/aaa https://youtu.be/bbb https://youtu.be/aaa"
        self.assertEqual(
            extract_urls(text),
            ["https://youtu.be/aaa", "https://youtu.be/bbb"],
        )


class TestModes(unittest.TestCase):
    def test_mode_labels_match_extension(self):
        self.assertEqual(MODE_LABELS["MP3 Audio"], "mp3")
        self.assertEqual(MODE_LABELS["FLAC Lossless"], "flac")
        self.assertEqual(MODE_LABELS["MP4 1080p"], "1080p")
        self.assertEqual(MODE_LABELS["MP4 4K"], "4k")
        self.assertEqual(MODE_LABELS["MP4 8K"], "8k")
        self.assertEqual(MODE_LABELS["Playlist MP3"], "playlist_mp3")
        self.assertEqual(MODE_LABELS["Playlist FLAC Lossless"], "playlist_flac")

    def test_normalize_mode_defaults_to_1080p(self):
        self.assertEqual(normalize_mode("Weird Label"), "1080p")
        self.assertEqual(normalize_mode("MP4 8K"), "8k")


if __name__ == "__main__":
    unittest.main()
