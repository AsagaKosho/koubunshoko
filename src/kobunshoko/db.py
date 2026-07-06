"""SQLiteカタログ（接続・DDL・クエリ）。

カタログは索引に過ぎない。真実の源は常に書庫ディレクトリのファイルであり、
このDBは全損しても書庫の走査から再構築できる前提で設計する。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2"

DDL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT
);

CREATE TABLE IF NOT EXISTS documents (
  tno            TEXT PRIMARY KEY,   -- 到達番号
  received_date  TEXT,               -- ISO 8601（西暦正規化後）。抽出不能ならNULL
  received_raw   TEXT,               -- 和暦の原文（例: "令和 8年 6月27日"）
  agency         TEXT,               -- 発行機関
  title          TEXT,               -- 件名
  pattern        TEXT,               -- 適用した抽出パターン名。'fallback' = 形式未対応
  ingested_at    TEXT NOT NULL,      -- 取込日時（ISO 8601）
  verify_status  TEXT                -- NULL / ok / mismatch / unverifiable
);

CREATE TABLE IF NOT EXISTS files (
  id        INTEGER PRIMARY KEY,
  tno       TEXT NOT NULL REFERENCES documents(tno) ON DELETE CASCADE,
  name      TEXT NOT NULL,           -- NFC正規化済みファイル名（docs/{tno}/ からの相対パス）
  role      TEXT NOT NULL,           -- kagami / xsl / yoshiki / pdf / csv / zip / other
  sha256    TEXT NOT NULL,
  digest_ok INTEGER,                 -- NULL=未検証 1=一致 0=不一致
  UNIQUE (tno, name)
);

-- ユーザー注釈（タグ・メモ）。原本には一切書き込まない。
-- documents への外部キーは張らない: 再インデックスは documents を全削除して
-- 書庫の走査から再構築するが、注釈はファイルシステムから導出できないため
-- カタログ再構築をまたいで保持する必要がある。
CREATE TABLE IF NOT EXISTS tags (
  id   INTEGER PRIMARY KEY,
  tno  TEXT NOT NULL,               -- documents.tno（緩い参照）
  tag  TEXT NOT NULL,
  UNIQUE (tno, tag)
);

CREATE TABLE IF NOT EXISTS memos (
  id         INTEGER PRIMARY KEY,
  tno        TEXT NOT NULL,         -- documents.tno（緩い参照）
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL          -- ISO 8601
);
"""

DDL_SEARCH = """
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
  tno UNINDEXED, title, agency, body,
  tokenize = 'trigram'
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def has_schema(conn: sqlite3.Connection) -> bool:
    """カタログのスキーマが存在するか（稼働中のDB削除・差し替え検知に使う）。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
    ).fetchone()
    return row is not None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.executescript(DDL_SEARCH)
    # スキーマは追加のみ（CREATE TABLE IF NOT EXISTS）なので、版は常に現在値へ更新する
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )
    conn.commit()


# 一覧に出す添付種別（表示ラベル順）
_KIND_LABELS = [("pdf", "PDF"), ("yoshiki", "XML"), ("csv", "CSV")]


def _fts_query(q: str) -> str:
    """ユーザー入力をFTS5のフレーズ検索クエリにする（演算子を無効化）。"""
    return '"' + q.replace('"', '""') + '"'


def _like_pattern(q: str) -> str:
    """ユーザー入力をLIKE部分一致パターンにする（%_\\ をエスケープ）。"""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_documents(
    conn: sqlite3.Connection,
    agency: str | None = None,
    month: str | None = None,
    sort: str = "received",
    q: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """一覧クエリ。agency=完全一致、month='YYYY-MM'、sort=received|ingested、
    q=全文検索（FR-05。件名・機関・XML本文テキストが対象）、tag=タグ完全一致（FR-09）。"""
    where: list[str] = []
    params: list[Any] = []
    if agency:
        where.append("d.agency = ?")
        params.append(agency)
    if tag:
        where.append("d.tno IN (SELECT tno FROM tags WHERE tag = ?)")
        params.append(tag)
    if month:
        where.append("substr(d.received_date, 1, 7) = ?")
        params.append(month)
    q = (q or "").strip()
    if q:
        if len(q) >= 3:
            # trigramトークナイザによる日本語部分一致（フレーズ検索）
            where.append("d.tno IN (SELECT tno FROM search WHERE search MATCH ?)")
            params.append(_fts_query(q))
        else:
            # trigramは3文字未満のMATCHにヒットしないため、短い語はLIKEで補完する
            where.append(
                "d.tno IN (SELECT tno FROM search WHERE "
                "title LIKE ? ESCAPE '\\' OR agency LIKE ? ESCAPE '\\' "
                "OR body LIKE ? ESCAPE '\\')"
            )
            params.extend([_like_pattern(q)] * 3)
    if sort == "ingested":
        order = "d.ingested_at DESC, d.tno"
    else:
        order = "(d.received_date IS NULL), d.received_date DESC, d.ingested_at DESC, d.tno"
    sql = (
        "SELECT d.*, "
        " (SELECT group_concat(DISTINCT f.role) FROM files f WHERE f.tno = d.tno) AS roles "
        "FROM documents d "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + f"ORDER BY {order}"
    )
    docs = []
    for row in conn.execute(sql, params):
        d = dict(row)
        roles = set((d.pop("roles") or "").split(","))
        d["kinds"] = [label for role, label in _KIND_LABELS if role in roles]
        docs.append(d)
    # タグはカンマを含み得るため group_concat を使わず別クエリで引く
    tag_map: dict[str, list[str]] = {}
    for r in conn.execute("SELECT tno, tag FROM tags ORDER BY tag"):
        tag_map.setdefault(r["tno"], []).append(r["tag"])
    for d in docs:
        d["tags"] = tag_map.get(d["tno"], [])
    return docs


def list_agencies(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT agency FROM documents WHERE agency IS NOT NULL AND agency != '' ORDER BY agency"
    )
    return [r["agency"] for r in rows]


def list_months(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT substr(received_date, 1, 7) AS m FROM documents "
        "WHERE received_date IS NOT NULL ORDER BY m DESC"
    )
    return [r["m"] for r in rows]


def get_document(conn: sqlite3.Connection, tno: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE tno = ?", (tno,)).fetchone()


def list_files(conn: sqlite3.Connection, tno: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM files WHERE tno = ? ORDER BY "
        "CASE role WHEN 'kagami' THEN 0 WHEN 'yoshiki' THEN 1 WHEN 'pdf' THEN 2 "
        "WHEN 'csv' THEN 3 WHEN 'xsl' THEN 4 WHEN 'zip' THEN 5 ELSE 6 END, name",
        (tno,),
    ).fetchall()


def get_file(conn: sqlite3.Connection, tno: str, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM files WHERE tno = ? AND name = ?", (tno, name)
    ).fetchone()


def clear_catalog(conn: sqlite3.Connection) -> None:
    """再インデックス（NFR-02）の前処理。カタログの索引を全削除する。

    真実の源はファイルシステムであり、ここで消しても書庫の走査から再構築できる。
    タグ・メモ（ユーザー注釈）はファイルシステムから導出できないため削除しない。
    """
    with conn:
        conn.execute("DELETE FROM search")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM documents")


# --- タグ・メモ（FR-09） ------------------------------------------------------


def list_tags(conn: sqlite3.Connection, tno: str) -> list[str]:
    rows = conn.execute("SELECT tag FROM tags WHERE tno = ? ORDER BY tag", (tno,))
    return [r["tag"] for r in rows]


def all_tags(conn: sqlite3.Connection) -> list[str]:
    """一覧の絞り込み候補。現存する文書に付いているタグのみ返す。"""
    rows = conn.execute(
        "SELECT DISTINCT tag FROM tags "
        "WHERE tno IN (SELECT tno FROM documents) ORDER BY tag"
    )
    return [r["tag"] for r in rows]


def add_tag(conn: sqlite3.Connection, tno: str, tag: str) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO tags (tno, tag) VALUES (?, ?)", (tno, tag))


def remove_tag(conn: sqlite3.Connection, tno: str, tag: str) -> None:
    with conn:
        conn.execute("DELETE FROM tags WHERE tno = ? AND tag = ?", (tno, tag))


def list_memos(conn: sqlite3.Connection, tno: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, body, created_at FROM memos WHERE tno = ? ORDER BY created_at, id",
        (tno,),
    )
    return [dict(r) for r in rows]


def add_memo(conn: sqlite3.Connection, tno: str, body: str, created_at: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO memos (tno, body, created_at) VALUES (?, ?, ?)",
            (tno, body, created_at),
        )


def delete_memo(conn: sqlite3.Connection, tno: str, memo_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM memos WHERE tno = ? AND id = ?", (tno, memo_id))
