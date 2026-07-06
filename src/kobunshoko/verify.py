"""ダイジェスト照合（改ざん検知・FR-06）。

鑑XMLの XMLDSig Reference が指す添付ファイルの SHA-256 ダイジェストと、
書庫にある実ファイルのハッシュを照合し、documents.verify_status と
files.digest_ok を更新する。

- Reference の URI はURLエンコード済みの場合がある（例: "01_%E6%8E%A7..."）ため
  デコード＋NFC正規化してから実ファイルに解決する
- URI が "#..."（同一文書内参照。DOCBODY等）のものはファイル照合の対象外
- 証明書チェーンの検証（GPKI）は行わない（スコープ外・Phase 3）

結果の三値:
  ok           … 照合できたファイル参照がすべて一致
  mismatch     … 1つでもダイジェスト不一致（改ざん・破損の疑い）
  unverifiable … 署名やファイル参照がない／参照先ファイル欠落／
                 未対応ダイジェスト方式などで照合しきれない
"""

from __future__ import annotations

import base64
import hashlib
import logging
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from .config import Config

logger = logging.getLogger(__name__)

_DS_NS = "http://www.w3.org/2000/09/xmldsig#"
_REFERENCE = f"{{{_DS_NS}}}Reference"
_DIGEST_METHOD = f"{{{_DS_NS}}}DigestMethod"
_DIGEST_VALUE = f"{{{_DS_NS}}}DigestValue"

# SHA-256を表すDigestMethodのAlgorithm URI
_SHA256_ALGORITHMS = {
    "http://www.w3.org/2001/04/xmlenc#sha256",
}

STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_UNVERIFIABLE = "unverifiable"


@dataclass
class RefResult:
    """Reference 1つぶんの照合結果。"""

    uri: str  # 鑑XMLに書かれたままのURI
    name: str | None  # デコード・NFC正規化後のファイル名（同一文書内参照はNone）
    ok: bool | None  # True=一致 / False=不一致 / None=照合不能
    detail: str = ""


@dataclass
class VerifyResult:
    status: str  # ok / mismatch / unverifiable
    refs: list[RefResult] = field(default_factory=list)
    message: str = ""


def _sha256_b64(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii")


def _resolve_ref_file(doc_dir: Path, name: str) -> Path | None:
    """参照名を文書セット配下の実ファイルに解決する（NFD差異も吸収）。"""
    base = doc_dir.resolve()
    target = (doc_dir / name).resolve()
    if not target.is_relative_to(base):
        return None  # 文書セット外を指す参照は不正として扱う
    if target.is_file():
        return target
    alt = doc_dir / unicodedata.normalize("NFD", name)
    if alt.is_file():
        return alt
    return None


def check_references(kagami_path: Path, doc_dir: Path) -> VerifyResult:
    """鑑XMLのReference群と実ファイルを照合する（DB更新なし・純粋な照合）。"""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.parse(str(kagami_path), parser).getroot()
    except (OSError, etree.XMLSyntaxError) as e:
        return VerifyResult(
            status=STATUS_UNVERIFIABLE,
            message=f"鑑XMLを読み取れません: {e}",
        )

    references = root.findall(f".//{_REFERENCE}")
    if not references:
        return VerifyResult(
            status=STATUS_UNVERIFIABLE,
            message="鑑XMLにXMLDSig署名（Reference）がありません",
        )

    refs: list[RefResult] = []
    for ref in references:
        uri = ref.get("URI") or ""
        if not uri or uri.startswith("#"):
            # 同一文書内参照（例: #DOCBODY）はC14N前提のため対象外
            refs.append(
                RefResult(uri=uri, name=None, ok=None, detail="同一文書内参照（対象外）")
            )
            continue
        name = unicodedata.normalize("NFC", unquote(uri))
        digest_el = ref.find(_DIGEST_VALUE)
        method_el = ref.find(_DIGEST_METHOD)
        algorithm = method_el.get("Algorithm") if method_el is not None else None
        expected = (digest_el.text or "").strip() if digest_el is not None else ""
        if algorithm not in _SHA256_ALGORITHMS:
            refs.append(
                RefResult(
                    uri=uri, name=name, ok=None,
                    detail=f"未対応のダイジェスト方式です: {algorithm}",
                )
            )
            continue
        if not expected:
            refs.append(
                RefResult(uri=uri, name=name, ok=None, detail="DigestValueがありません")
            )
            continue
        path = _resolve_ref_file(doc_dir, name)
        if path is None:
            refs.append(
                RefResult(uri=uri, name=name, ok=None, detail="参照先ファイルがありません")
            )
            continue
        actual = _sha256_b64(path)
        if actual == expected:
            refs.append(RefResult(uri=uri, name=name, ok=True, detail="一致"))
        else:
            refs.append(
                RefResult(
                    uri=uri, name=name, ok=False,
                    detail="ダイジェスト不一致（改ざんまたは破損の疑い）",
                )
            )

    file_refs = [r for r in refs if r.name is not None]
    if not file_refs:
        status = STATUS_UNVERIFIABLE
        message = "照合可能なファイル参照がありません"
    elif any(r.ok is False for r in file_refs):
        status = STATUS_MISMATCH
        message = "ダイジェストが一致しないファイルがあります"
    elif any(r.ok is None for r in file_refs):
        status = STATUS_UNVERIFIABLE
        message = "一部のファイル参照を照合できませんでした"
    else:
        status = STATUS_OK
        message = f"全{len(file_refs)}件のファイル参照が一致しました"
    return VerifyResult(status=status, refs=refs, message=message)


def verify_document(
    config: Config, conn: sqlite3.Connection, tno: str
) -> VerifyResult:
    """文書セット1件を照合し、documents.verify_status / files.digest_ok を更新する。"""
    kagami_row = conn.execute(
        "SELECT name FROM files WHERE tno = ? AND role = 'kagami' ORDER BY id LIMIT 1",
        (tno,),
    ).fetchone()
    doc_dir = config.docs_dir / tno
    if kagami_row is None:
        result = VerifyResult(
            status=STATUS_UNVERIFIABLE, message="鑑XMLがないため検証できません"
        )
    else:
        kagami_path = doc_dir / kagami_row["name"]
        if not kagami_path.is_file():
            alt = doc_dir / unicodedata.normalize("NFD", kagami_row["name"])
            kagami_path = alt if alt.is_file() else kagami_path
        result = check_references(kagami_path, doc_dir)

    with conn:  # 照合結果を単一トランザクションで反映
        conn.execute(
            "UPDATE documents SET verify_status = ? WHERE tno = ?",
            (result.status, tno),
        )
        # 前回の照合結果を必ずリセットする。今回照合できなかったファイル
        # （削除・リネーム等で参照先が欠落したもの）に前回の「一致/不一致」が
        # 残留すると、存在しないファイルに一致バッジが表示され続けてしまう
        conn.execute("UPDATE files SET digest_ok = NULL WHERE tno = ?", (tno,))
        for ref in result.refs:
            if ref.name is None or ref.ok is None:
                continue
            conn.execute(
                "UPDATE files SET digest_ok = ? WHERE tno = ? AND name = ?",
                (1 if ref.ok else 0, tno, ref.name),
            )
    logger.info("検証 %s: %s (%s)", tno, result.status, result.message)
    return result
