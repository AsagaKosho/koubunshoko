"""監視フォルダ自動取り込み（FR-08）のテスト。

- 既定は無効（watch_dir 未設定なら何も起きない）
- 公文書らしいZIPだけを取り込む（無関係なZIPは無視）
- 重複は通常の重複チェックでスキップされる
- watchdog監視スレッド経由の自動取り込み（Polling監視で決定的に）
- サーバー（lifespan）起動時の初回スキャンで既存ZIPも取り込まれる
"""

from __future__ import annotations

import shutil
import time
import zipfile

import pytest

from conftest import TOTATUNO_TNO


def _wait_until(pred, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


@pytest.fixture()
def watch_config(tmp_path):
    from kobunshoko.config import Config

    watch_dir = tmp_path / "downloads"
    watch_dir.mkdir()
    cfg = Config(archive=tmp_path / "archive", watch_dir=watch_dir)
    cfg.ensure_dirs()
    from kobunshoko import db as db_mod
    from contextlib import closing

    with closing(db_mod.connect(cfg.db_path)) as conn:
        db_mod.ensure_schema(conn)
    return cfg


def _make_service(config, **kw):
    from kobunshoko.watch import WatchService

    kw.setdefault("use_polling", True)
    kw.setdefault("poll_interval", 0.05)
    kw.setdefault("settle_delay", 0.02)
    kw.setdefault("settle_retries", 50)
    return WatchService(config, **kw)


def _doc_count(config):
    from contextlib import closing

    from kobunshoko import db as db_mod

    with closing(db_mod.connect(config.db_path)) as conn:
        return conn.execute("SELECT count(*) FROM documents").fetchone()[0]


def test_config_watch_dir_resolution(monkeypatch, tmp_path):
    from kobunshoko.config import load_config

    # 既定は無効
    monkeypatch.delenv("KOBUNSHOKO_WATCH_DIR", raising=False)
    assert load_config(str(tmp_path / "a")).watch_dir is None
    # 環境変数 → CLI引数の順で上書き
    monkeypatch.setenv("KOBUNSHOKO_WATCH_DIR", str(tmp_path / "env"))
    assert load_config(str(tmp_path / "a")).watch_dir == (tmp_path / "env").resolve()
    cfg = load_config(str(tmp_path / "a"), watch_dir=str(tmp_path / "cli"))
    assert cfg.watch_dir == (tmp_path / "cli").resolve()


def test_process_zip_ingests_kobunshoko_zip(watch_config, totatuno_zip):
    service = _make_service(watch_config)
    dest = watch_config.watch_dir / totatuno_zip.name
    shutil.copy(totatuno_zip, dest)

    result = service.process_zip(dest)
    assert result is not None and result.status == "ok"
    assert (watch_config.docs_dir / TOTATUNO_TNO / "original.zip").is_file()
    # 監視フォルダ内の元ZIPは残る（副作用なし）
    assert dest.is_file()

    # 同じZIPをもう一度 → 重複スキップ
    result = service.process_zip(dest)
    assert result is not None and result.status == "skipped"
    assert _doc_count(watch_config) == 1


def test_process_zip_ignores_unrelated_zip(watch_config):
    decoy = watch_config.watch_dir / "photos.zip"
    with zipfile.ZipFile(decoy, "w") as zf:
        zf.writestr("a.txt", "not a kobunshoko")
    service = _make_service(watch_config)
    assert service.process_zip(decoy) is None
    assert _doc_count(watch_config) == 0


def test_process_zip_gives_up_on_unreadable_file(watch_config):
    service = _make_service(watch_config, settle_retries=3)
    broken = watch_config.watch_dir / "broken.zip"
    broken.write_bytes(b"this is not a zip")
    assert service.process_zip(broken) is None  # ZIPとして安定しない → 諦める
    missing = watch_config.watch_dir / "never_appears.zip"
    assert service.process_zip(missing) is None


def test_watcher_thread_picks_up_new_zip(watch_config, totatuno_zip):
    service = _make_service(watch_config)
    service.start()
    try:
        # 監視開始後に置かれたZIPが自動で取り込まれる
        shutil.copy(totatuno_zip, watch_config.watch_dir / totatuno_zip.name)
        assert _wait_until(
            lambda: (watch_config.docs_dir / TOTATUNO_TNO).is_dir(), timeout=15
        )
    finally:
        service.stop()
    assert _doc_count(watch_config) == 1
    assert any(r.status == "ok" and r.tno == TOTATUNO_TNO for r in service.results)


def test_start_requires_existing_dir(tmp_path):
    from kobunshoko.config import Config

    cfg = Config(archive=tmp_path / "archive", watch_dir=tmp_path / "no_such_dir")
    cfg.ensure_dirs()
    service = _make_service(cfg)
    with pytest.raises(ValueError):
        service.start()


def test_app_lifespan_ingests_preexisting_zip(watch_config, totatuno_zip):
    """サーバー起動（lifespan）で監視が始まり、起動時に既に置かれていた
    ZIPも初回スキャンで取り込まれる。"""
    from fastapi.testclient import TestClient

    from kobunshoko.main import create_app

    shutil.copy(totatuno_zip, watch_config.watch_dir / totatuno_zip.name)
    app = create_app(watch_config)
    with TestClient(app) as client:
        assert _wait_until(
            lambda: TOTATUNO_TNO in client.get("/").text, timeout=15
        )
    # 停止（lifespan終了）後はスレッドが片付いている
    watcher = app.state.watcher
    assert watcher._observer is None and watcher._worker is None


def test_no_watcher_when_disabled(config):
    """watch_dir 未設定（既定）では監視スレッドを起動しない。"""
    from fastapi.testclient import TestClient

    from kobunshoko.main import create_app

    app = create_app(config)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert not hasattr(app.state, "watcher")
