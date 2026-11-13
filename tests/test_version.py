"""VERSION 自動採番（tools/build_version.py）のテスト。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_version import (
    GitHistoryUnavailable,
    determine_version,
    format_version,
)


class BuildVersionTest(unittest.TestCase):
    def test_zero_commits_is_release(self):
        self.assertEqual(format_version(0), "RELEASE1.0")

    def test_three_commits_is_zero_padded(self):
        self.assertEqual(format_version(3), "RELEASE1.0-03")

    def test_counts_above_nine_keep_two_digit_padding(self):
        self.assertEqual(format_version(12), "RELEASE1.0-12")

    def test_negative_count_is_rejected(self):
        with self.assertRaises(ValueError):
            format_version(-1)

    def test_falls_back_to_existing_version_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            version_file = Path(tmp) / "VERSION"
            version_file.write_text("RELEASE1.0-07\n", encoding="utf-8")
            with patch(
                "tools.build_version.count_commits_since_tag",
                side_effect=subprocess.CalledProcessError(1, "git"),
            ):
                self.assertEqual(
                    determine_version(cwd=Path(tmp), version_file=version_file),
                    "RELEASE1.0-07",
                )

    def test_falls_back_when_repository_is_shallow(self):
        with tempfile.TemporaryDirectory() as tmp:
            version_file = Path(tmp) / "VERSION"
            version_file.write_text("RELEASE1.0-07\n", encoding="utf-8")
            with patch(
                "tools.build_version.count_commits_since_tag",
                side_effect=GitHistoryUnavailable("shallow"),
            ):
                self.assertEqual(
                    determine_version(cwd=Path(tmp), version_file=version_file),
                    "RELEASE1.0-07",
                )

    def test_falls_back_to_dev_without_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            version_file = Path(tmp) / "VERSION"
            with patch(
                "tools.build_version.count_commits_since_tag",
                side_effect=FileNotFoundError("git"),
            ):
                self.assertEqual(
                    determine_version(cwd=Path(tmp), version_file=version_file),
                    "dev",
                )


if __name__ == "__main__":
    unittest.main()
