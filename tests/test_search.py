"""全文検索（FR-05）のテスト。

件名・発行機関・XML本文テキスト（鑑・様式）を対象に、一覧の q パラメータで
日本語部分一致検索ができることを確認する。FTS5 trigram は3文字未満の語に
ヒットしないため、短い語はLIKE補完で検索できることも確認する。
"""

from __future__ import annotations

from conftest import DOCNO_TNO, TOTATUNO_TNO


def _upload(client, zip_path):
    with open(zip_path, "rb") as f:
        return client.post(
            "/ingest", files=[("files", (zip_path.name, f, "application/zip"))]
        )


def _search(client, q, **params):
    return client.get("/", params={"q": q, **params})


def test_search_title_agency_body(client, totatuno_zip, docno_zip):
    _upload(client, totatuno_zip)
    _upload(client, docno_zip)

    # 件名: docno の TITLE にのみ含まれる
    res = _search(client, "お知らせ")
    assert res.status_code == 200
    assert DOCNO_TNO in res.text
    assert TOTATUNO_TNO not in res.text

    # 件名: totatuno の固定件名「電子申請に対する審査結果について」
    res = _search(client, "審査結果")
    assert TOTATUNO_TNO in res.text
    assert DOCNO_TNO not in res.text

    # 発行機関: 「架空労働基準監督署」
    res = _search(client, "監督署")
    assert TOTATUNO_TNO in res.text
    assert DOCNO_TNO not in res.text

    # 鑑XML本文: totatuno の HOJIGRPNAME「株式会社テスト商事」（空白なし）
    res = _search(client, "株式会社テ")
    assert TOTATUNO_TNO in res.text
    assert DOCNO_TNO not in res.text

    # 様式XML本文: docno の DOCLINK 参照先（yoshiki）の goukeigaku
    res = _search(client, "76,543")
    assert DOCNO_TNO in res.text
    assert TOTATUNO_TNO not in res.text


def test_search_short_query_uses_like(client, totatuno_zip, docno_zip):
    _upload(client, totatuno_zip)
    _upload(client, docno_zip)
    # 2文字（trigram のMATCHではヒットしない長さ）でも部分一致する
    res = _search(client, "商事")
    assert TOTATUNO_TNO in res.text
    assert DOCNO_TNO in res.text
    # LIKEワイルドカードはエスケープされ、リテラルとして扱われる
    res = _search(client, "%")
    assert TOTATUNO_TNO not in res.text
    assert DOCNO_TNO not in res.text


def test_search_no_hit_and_combined_filter(client, totatuno_zip, docno_zip):
    _upload(client, totatuno_zip)
    _upload(client, docno_zip)

    res = _search(client, "存在しない語句XYZ")
    assert res.status_code == 200
    assert TOTATUNO_TNO not in res.text
    assert DOCNO_TNO not in res.text
    assert "一致する文書はありません" in res.text

    # 検索と機関絞り込みの併用（両方にヒットする語＋機関で1件に絞る）
    res = _search(client, "テスト商事", agency="架空年金機構")
    assert DOCNO_TNO in res.text
    assert TOTATUNO_TNO not in res.text


def test_search_operator_input_is_safe(client, totatuno_zip):
    _upload(client, totatuno_zip)
    # FTS5の演算子・引用符を含む入力でも500にならない（フレーズとして扱う）
    for q in ('審査 OR "', 'NEAR(a b)', '"""', "col:value*"):
        res = _search(client, q)
        assert res.status_code == 200
