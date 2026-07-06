from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

TOTATUNO_TNO = "209906019912345678"
DOCNO_TNO = "209901010188888888"


@pytest.fixture()
def config(tmp_path):
    from kobunshoko.config import Config

    cfg = Config(archive=tmp_path / "archive")
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def client(config):
    from fastapi.testclient import TestClient

    from kobunshoko.main import create_app

    app = create_app(config)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def totatuno_zip(tmp_path):
    """cp437名修復が必要な（UTF-8フラグなし・cp932名の）ZIPを生成する。"""
    from zipbuild import zip_from_dir

    dest = tmp_path / f"{TOTATUNO_TNO}_20260706120000.zip"
    return zip_from_dir(
        FIXTURES / "totatuno_set", dest, inner_dir=TOTATUNO_TNO,
        encoding="cp932", utf8_flag=False,
    )


@pytest.fixture()
def docno_zip(tmp_path):
    from zipbuild import zip_from_dir

    dest = tmp_path / f"{DOCNO_TNO}.zip"
    return zip_from_dir(
        FIXTURES / "docno_set", dest, inner_dir=DOCNO_TNO,
        encoding="cp932", utf8_flag=False,
    )
