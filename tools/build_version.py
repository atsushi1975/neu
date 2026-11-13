#!/usr/bin/env python3
"""ビルド時に VERSION を自動採番する。

git タグ RELEASE1.0 からのコミット数を連番にする:

    0件 -> RELEASE1.0
    1件 -> RELEASE1.0-01
    3件 -> RELEASE1.0-03

使い方:
    python3 tools/build_version.py --write   # VERSION を更新
    python3 tools/build_version.py           # 表示だけ

git 履歴やタグが無いビルド環境では、既存の VERSION をそのまま使う。
VERSION も無ければ "dev"（/health の既存フォールバックと同じ）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
BASE_VERSION = "RELEASE1.0"
BASE_TAG = "RELEASE1.0"


def format_version(commit_count: int) -> str:
    """コミット数から version 文字列を組み立てる。"""
    if commit_count < 0:
        raise ValueError("commit_count must be >= 0")
    if commit_count == 0:
        return BASE_VERSION
    return f"{BASE_VERSION}-{commit_count:02d}"


def count_commits_since_tag(tag: str = BASE_TAG, cwd: Path = ROOT) -> int:
    """タグから HEAD までのコミット数を返す。git が使えなければ例外。"""
    result = subprocess.run(
        ["git", "rev-list", "--count", f"{tag}..HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


def read_version(path: Path = VERSION_FILE) -> str:
    """VERSION ファイルを読む。無ければ空文字。"""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def determine_version(
    cwd: Path = ROOT,
    tag: str = BASE_TAG,
    version_file: Path = VERSION_FILE,
) -> str:
    """git から version を決める。取得失敗時は既存 VERSION、無ければ "dev"。"""
    try:
        return format_version(count_commits_since_tag(tag, cwd))
    except (OSError, subprocess.SubprocessError, ValueError):
        return read_version(version_file) or "dev"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VERSION を git のコミット数から採番する")
    parser.add_argument(
        "--write",
        action="store_true",
        help="採番した version を VERSION ファイルに書き出す",
    )
    args = parser.parse_args(argv)

    version = determine_version()
    if args.write:
        VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
