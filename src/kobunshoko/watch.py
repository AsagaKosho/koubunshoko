"""監視フォルダ自動取り込み（FR-08）。

指定フォルダ（例: ~/Downloads）に公文書ZIPが置かれたら自動で取り込む。

- 既定は無効。CLI引数 --watch-dir または環境変数 KOBUNSHOKO_WATCH_DIR で有効化
- サーバープロセス内のバックグラウンドスレッドとして常駐する
  （watchdogのObserverスレッド＋取り込みワーカースレッドの2本。
    取り込みはワーカー1本に直列化し、DB書き込みの競合を避ける）
- 公文書らしくないZIP（名前にも中身にも到達番号・鑑XMLの痕跡がないもの）は無視する
- 取り込み済みの到達番号は通常の重複チェックでスキップされる（上書きしない）
- 監視フォルダ内のファイルは読み取りのみで、削除・移動・変更は行わない
- ダウンロード途中のファイルを掴まないよう、サイズが安定して
  ZIPとして読めるようになるまで待ってから取り込む
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import zipfile
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from . import db, ingest
from .config import Config

logger = logging.getLogger(__name__)

_STOP = object()  # ワーカー停止用の番兵


class _ZipCreatedHandler(FileSystemEventHandler):
    """新規作成・リネーム到着（ブラウザの .crdownload → .zip 等）を拾う。"""

    def __init__(self, service: "WatchService") -> None:
        self._service = service

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._service.enqueue(Path(str(event.src_path)))

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._service.enqueue(Path(str(event.dest_path)))


class WatchService:
    """監視フォルダの常駐サービス。start()/stop() でサーバーの起動・終了に追従する。"""

    def __init__(
        self,
        config: Config,
        *,
        use_polling: bool = False,
        poll_interval: float = 0.2,
        settle_delay: float = 0.5,
        settle_retries: int = 120,
    ) -> None:
        if config.watch_dir is None:
            raise ValueError("watch_dir が設定されていません")
        self.config = config
        self.watch_dir = config.watch_dir
        self.use_polling = use_polling
        self.poll_interval = poll_interval
        self.settle_delay = settle_delay
        self.settle_retries = settle_retries
        self.results: list[ingest.IngestResult] = []  # 自動取り込みの履歴（新しい順ではなく到着順）
        self._queue: queue.Queue[object] = queue.Queue()
        self._seen: set[str] = set()  # created+moved の二重イベント抑止
        self._seen_lock = threading.Lock()
        self._observer = None
        self._worker: threading.Thread | None = None

    # --- ライフサイクル ------------------------------------------------------

    def start(self) -> None:
        if not self.watch_dir.is_dir():
            raise ValueError(f"監視フォルダが存在しません: {self.watch_dir}")
        self._worker = threading.Thread(
            target=self._run_worker, name="kobunshoko-watch-worker", daemon=True
        )
        self._worker.start()
        if self.use_polling:
            self._observer = PollingObserver(timeout=self.poll_interval)
        else:
            self._observer = Observer()
        self._observer.schedule(
            _ZipCreatedHandler(self), str(self.watch_dir), recursive=False
        )
        self._observer.start()
        # 起動時に既に置かれているZIPも対象にする（取り込み済みは重複チェックでスキップされる）
        for p in sorted(self.watch_dir.glob("*.zip")):
            self.enqueue(p)
        logger.info("監視フォルダの自動取り込みを開始: %s", self.watch_dir)

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        if self._worker is not None:
            self._queue.put(_STOP)
            self._worker.join(timeout=30)
            self._worker = None
        logger.info("監視フォルダの自動取り込みを停止: %s", self.watch_dir)

    # --- 取り込みキュー -------------------------------------------------------

    def enqueue(self, path: Path) -> None:
        """ZIPらしきパスをワーカーへ渡す（それ以外は無視）。"""
        if path.suffix.lower() != ".zip":
            return
        key = str(path)
        with self._seen_lock:
            if key in self._seen:
                return
            self._seen.add(key)
        self._queue.put(path)

    def _run_worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            path = item  # type: ignore[assignment]
            try:
                self.process_zip(path)
            except Exception:
                logger.exception("自動取り込み中に予期しないエラー: %s", path)
            finally:
                # 同名ファイルの再ダウンロードを再度拾えるようにする
                with self._seen_lock:
                    self._seen.discard(str(path))

    # --- 1件ぶんの処理 --------------------------------------------------------

    def process_zip(self, path: Path) -> ingest.IngestResult | None:
        """ZIP 1件を（安定を待って）判定し、公文書らしければ取り込む。

        戻り値: 取り込みを試みた場合はその結果、対象外・消失なら None。
        """
        if not self._wait_settled(path):
            logger.warning("ZIPが読める状態になりませんでした（スキップ）: %s", path)
            return None
        if not ingest.is_kobunshoko_zip(path):
            logger.info("公文書らしくないZIPのため無視: %s", path.name)
            return None
        # sqlite3接続はスレッドをまたげないため、ワーカー側でその都度開く
        conn = db.connect(self.config.db_path)
        try:
            result = ingest.ingest_zip(self.config, conn, path)
        finally:
            conn.close()
        self.results.append(result)
        logger.info(
            "自動取り込み %s: %s %s（到達番号: %s）",
            path.name, result.status, result.message, result.tno or "-",
        )
        return result

    def _wait_settled(self, path: Path) -> bool:
        """ファイルサイズが安定し、ZIPとして読めるようになるまで待つ。"""
        last_size = -1
        for _ in range(self.settle_retries):
            try:
                size = path.stat().st_size
            except OSError:
                size = -1  # まだ存在しない／消えた
            if size >= 0 and size == last_size:
                try:
                    if zipfile.is_zipfile(path):
                        return True
                except OSError:
                    pass
            last_size = size
            time.sleep(self.settle_delay)
        return False
