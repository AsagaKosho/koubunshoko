"""書庫ルート等の設定解決。

- 書庫ルート: CLI引数 --archive → 環境変数 KOBUNSHOKO_ARCHIVE → 既定 ~/kobunshoko-archive/
- 監視フォルダ: CLI引数 --watch-dir → 環境変数 KOBUNSHOKO_WATCH_DIR → 既定 なし（無効）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "KOBUNSHOKO_ARCHIVE"
ENV_WATCH_DIR = "KOBUNSHOKO_WATCH_DIR"
DEFAULT_ARCHIVE = "~/kobunshoko-archive"


@dataclass(frozen=True)
class Config:
    """アプリ全体の設定。"""

    archive: Path
    watch_dir: Path | None = None  # 監視フォルダ。None = 自動取り込み無効（既定）

    @property
    def docs_dir(self) -> Path:
        return self.archive / "docs"

    @property
    def db_path(self) -> Path:
        return self.archive / "catalog.db"

    def ensure_dirs(self) -> None:
        """書庫ルートと docs/ を作成する（初回起動時の自動作成）。"""
        self.docs_dir.mkdir(parents=True, exist_ok=True)


def resolve_archive(cli_value: str | None = None) -> Path:
    raw = cli_value or os.environ.get(ENV_VAR) or DEFAULT_ARCHIVE
    return Path(raw).expanduser().resolve()


def resolve_watch_dir(cli_value: str | None = None) -> Path | None:
    raw = cli_value or os.environ.get(ENV_WATCH_DIR)
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def load_config(
    cli_value: str | None = None, watch_dir: str | None = None
) -> Config:
    return Config(
        archive=resolve_archive(cli_value),
        watch_dir=resolve_watch_dir(watch_dir),
    )
