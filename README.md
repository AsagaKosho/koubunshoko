# 公文書庫 kobunshoko

[![CI](https://github.com/AsagaKosho/kobunshoko/actions/workflows/ci.yml/badge.svg)](https://github.com/AsagaKosho/kobunshoko/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

e-Gov からダウンロードした電子公文書（XML＋XSL＋添付）を、ブラウザの XSLT サポートに
依存せず恒久的に保全・整理・検索・閲覧するためのローカル Web アプリ。

> **kobunshoko** is a local web app to archive, organize, search and view official
> documents (XML + XSLT + attachments) downloaded from [e-Gov](https://shinsei.e-gov.go.jp/),
> Japan's government e-application portal. Browsers are
> [removing XSLT support](https://developer.chrome.com/docs/web-platform/deprecating-xslt),
> which breaks the traditional "open the XML in a browser" workflow — kobunshoko performs
> XSLT 1.0 transforms server-side (lxml/libxslt) and serves plain HTML, so the documents
> stay readable in any browser, forever. Runs 100% offline on `127.0.0.1`.

- ZIP／解凍済みフォルダを放り込むと、鑑XMLから到達番号・受付日・発行機関・件名を抽出してカタログ化
- XML＋XSL は**サーバー側で XSLT 1.0 変換**して HTML 配信（ブラウザの XSLT 廃止の影響を受けない）
- PDF はインライン表示、Shift_JIS の CSV は UTF-8 のテーブル表示
- 原本は無加工で `書庫/docs/{到達番号}/` に平置き保全。XMLDSig ダイジェスト照合で改ざん検知
- 全文検索（SQLite FTS5 trigram）、再インデックス、タグ・メモ、監視フォルダ取り込み

## セットアップ

必要なもの: [uv](https://docs.astral.sh/uv/)（Python 3.12+ は uv が自動解決）。

```sh
git clone https://github.com/AsagaKosho/kobunshoko.git
cd kobunshoko
uv sync          # 省略可（uv run が初回に自動で行う）
```

クローンせずに試す場合:

```sh
uvx --from git+https://github.com/AsagaKosho/kobunshoko kobunshoko
```

## 起動

```sh
uv run kobunshoko                     # http://127.0.0.1:8720
uv run kobunshoko --archive ~/my-archive --port 8720
```

または:

```sh
uv run uvicorn kobunshoko.main:app --host 127.0.0.1 --port 8720
```

サーバーは `127.0.0.1` のみにバインドし、外部ネットワークへの送信は行わない。

## 書庫の場所

書庫ルートの解決順: `--archive` → 環境変数 `KOBUNSHOKO_ARCHIVE` → 既定 `~/kobunshoko-archive/`。

```
書庫ルート/
├── catalog.db        … SQLite カタログ（索引のみ。消えても /reindex で再構築可能）
└── docs/
    └── {到達番号}/   … 原本一式を無加工で平置き（ZIP 取り込み時は original.zip も保管）
```

真実の源は常にファイルシステム側。書庫ディレクトリを丸ごとコピーすればバックアップ・移行が完了する。

## 監視フォルダ（自動取り込み）

指定フォルダに置かれた公文書ZIPを自動で取り込む（既定は無効）:

```sh
uv run kobunshoko --watch-dir ~/Downloads
```

解決順: `--watch-dir` → 環境変数 `KOBUNSHOKO_WATCH_DIR` → 既定 なし（無効）。
公文書らしくないZIPは無視し、取り込み済みの到達番号はスキップする。
監視フォルダ内のファイルは読み取りのみで、削除・移動はしない。

## テスト

```sh
uv run pytest
```

テストフィクスチャは実公文書と同じ構造・エンコーディングの合成データ
（`tests/fixtures/`）のみを使う。**実公文書はリポジトリにコミットしない**。
合成データの到達番号は `2099` 始まりとする規約（CIが実在番号らしき数字列を検知して失敗する）。

## ライセンス

[MIT](LICENSE)。脆弱性の報告は [SECURITY.md](SECURITY.md) を参照。

## ドキュメント

| 文書 | 内容 |
|---|---|
| [docs/01_企画書.md](docs/01_企画書.md) | 背景調査（ブラウザXSLT廃止・e-Gov取得期限）と企画 |
| [docs/02_プロダクト要求仕様書.md](docs/02_プロダクト要求仕様書.md) | 機能・非機能要求、受け入れ基準 |
| [docs/03_技術設計書.md](docs/03_技術設計書.md) | アーキテクチャ・DB設計・セキュリティ設計・Decision Log |
| [docs/05_GPKI署名検証調査.md](docs/05_GPKI署名検証調査.md) | GPKI 証明書チェーン検証（Phase 3）の調査 |
| [docs/06_e-Gov電子申請API調査.md](docs/06_e-Gov電子申請API調査.md) | 電子申請APIによる公文書取得の実現性調査 |
