# GPKI署名チェーン検証 調査報告

- 作成日: 2026-07-06
- ステータス: 調査完了（実装はしない。M3の調査タスク）
- 関連文書: [02_プロダクト要求仕様書.md](02_プロダクト要求仕様書.md)（FR-06のスコープ外事項） / [03_技術設計書.md](03_技術設計書.md)（§13 M3）

## 0. 要旨（結論先出し）

**実装は可能。ただし既製ライブラリ（signxml）はGPKIの署名プロファイルと非互換であることを実機で確認したため、既存 `verify.py` を拡張する「自前の最小実装」を推奨する。**

| 検証項目 | 可否 | 手段 |
|---|---|---|
| ① 参照ダイジェスト照合（改ざん検知） | 済 | 実装済み（FR-06, `verify.py`） |
| ② 署名値の検証（SignedInfoのRSA-SHA256） | **可**（PoCで実サンプル2件とも成功） | lxml `etree.canonicalize` ＋ cryptography |
| ③ 証明書チェーン検証（官職証明書→GPKI信頼点） | **可**（PoCで実サンプル2件とも成功） | cryptography `x509.verification`（カスタム拡張ポリシー、要 cryptography 45+） |
| ④ 失効確認（CRL） | **オフラインでは不可** | CRLはGPKIディレクトリ（LDAP）配布のみ。NFR-03（外部送信ゼロ）と衝突するためスコープ外のままとし、UIで「失効確認は未実施」と明示する |

## 1. 実文書の署名の構造（実サンプル2件の解析結果）

手元の実公文書2件（労基署審査結果通知・年金機構納入告知額通知）の鑑XMLを解析した。
解析はリポジトリ外で行い、実文書・証明書はリポジトリにコミットしていない。

### 1-1. XMLDSigの構成

両者とも同一プロファイル:

| 項目 | 値 |
|---|---|
| CanonicalizationMethod | `http://www.w3.org/TR/2001/REC-xml-c14n-20010315`（**inclusive C14N 1.0**） |
| SignatureMethod | `http://www.w3.org/2001/04/xmldsig-more#rsa-sha256` |
| DigestMethod | `http://www.w3.org/2001/04/xmlenc#sha256` |
| Reference | `URI="#DOCBODY"`（鑑本文。`<BODY ID="DOCBODY">` の**大文字ID属性**を参照）＋ 添付ファイルごとの相対URI参照（URLエンコード済みの場合あり） |
| Signature要素 | `<Signature xmlns="http://www.w3.org/2000/09/xmldsig#" Id="mhlw.go.jp">`（default名前空間宣言） |
| XAdES拡張 | なし（素のXMLDSig。タイムスタンプもなし） |

注意点:

- `#DOCBODY` はDTD/スキーマなしの大文字 `ID` 属性参照。標準のID解決（`xml:id` や DTD宣言）に頼る実装では解決できないため、属性名 `ID` を直接探す必要がある
- 添付ファイル参照はdetached署名。URI解決時はURLデコード＋NFC正規化が必要（既存 `verify.py` と同じ扱い）

### 1-2. 証明書チェーン

| | 労基署サンプル | 年金機構サンプル |
|---|---|---|
| 署名者（官職証明書） | `CN=Manager, OU=Tsuchiura Labour Standards Inspection Office, OU=Ibaraki Labour Bureau, OU=Ministry of Health, Labour and Welfare, O=Japanese Government, C=JP` | `CN=Director, Pension Service Management Division, Pension Bureau, OU=Ministry of Health, Labour and Welfare, O=Japanese Government, C=JP` |
| 発行者 | `OU=OfficialStatusCA, O=Japanese Government, C=JP`（官職認証局） | 同左 |
| 有効期間 | 2024-03〜2029-03（5年） | 2023-12〜2028-12（5年） |
| KeyInfoに同梱される証明書 | **署名者証明書のみ**（1枚） | 署名者証明書＋**自己署名ルート**（2枚） |
| 鍵用途 | digitalSignature, nonRepudiation（critical） | 同左 |
| critical拡張 | certificatePolicies（2.5.29.32）が**critical** | 同左 |
| CRL配布点 | **DirName のみ**（X.500名。HTTP URIなし） | 同左 |
| OCSP (AIA) | なし | なし |

チェーンは「官職証明書 → OfficialStatusCA（自己署名）」の**2段**で完結する。中間CAはない。

信頼点となる官職認証局（OfficialStatusCA）自己署名証明書（年金機構サンプル同梱分）:

- 有効期間: 2019-08-23 〜 2029-08-23
- 署名アルゴリズム: sha256WithRSAEncryption
- SHA-256フィンガープリント（手元計算値）:
  `FA:63:36:BB:C7:78:CA:24:1A:59:FA:CF:07:47:01:23:42:65:56:04:6C:3C:BF:F0:8A:B8:60:B2:81:F5:57:63`

**文書同梱のルートを信頼点にするのは自己言及であり不可。** 信頼点は帯域外で入手・照合する必要がある（→ §2-2）。労基署サンプルのようにルートが同梱されない文書もあるため、いずれにせよローカルに信頼点を持つ必要がある。

## 2. GPKIの現在の構成と信頼点の入手

### 2-1. 認証局の構成

政府認証基盤（GPKI）の府省認証局は現在**1つの政府共用認証局に集約**されており、その下に

- **官職認証局（OfficialStatusCA）** — 処分通知（＝e-Gov公文書の官職署名）用。今回の対象
- 日本政府認証局 — 文書署名用（ルート＋サブ認証局構成）

がある。かつて公文書署名に使われた「アプリケーション認証局2」は2019年に業務終了・廃局済み。手元のサンプルはすべて OfficialStatusCA 発行であり、本プロダクトが対象とすべき信頼点は当面 OfficialStatusCA の1枚である。

### 2-2. 信頼点（自己署名証明書）の入手と照合

- GPKIウェブサイト（gpki.go.jp）の官職認証局ページから自己署名証明書を入手できる
- e-Govポータルに「政府認証基盤(GPKI)におけるフィンガープリント」ページがあり、公表フィンガープリントとの照合で改変がないことを確認できる（本調査ではプログラムからの取得が403で拒否されたため、**公表値との照合は導入時に手動で行うこと**。§1-2の手元計算値はその照合材料）
- 運用案: 信頼点PEMは書庫外の設定領域またはリポジトリに同梱し、入手元URL・フィンガープリント・照合日を添えて管理する。**実行時にネットワークから取得しない**（NFR-03）

### 2-3. 将来の信頼点ローテーション

OfficialStatusCA証明書は2029-08に失効する。後継CA証明書への切替（複数信頼点の併存期間）が発生するため、信頼点は1枚固定ではなく「PEM束（ディレクトリ）」として持つ設計にしておく。

## 3. 失効確認の要否と可否

### 3-1. 技術的可否

- 官職証明書のCRL配布点は **DirName（X.500ディレクトリ名）のみ**で、HTTP URIがない。CRLの取得にはGPKIのディレクトリサービス（LDAP）への接続が必要
- OCSPレスポンダの記載（AIA拡張）もない
- したがって**失効確認は本質的にオンライン処理**であり、NFR-03「外部ネットワークへの送信を一切行わない」と根本的に衝突する

### 3-2. 要否の評価

- 本プロダクトの目的は「受領済み公文書の改ざん検知と保管時点の真正性記録」。失効は主に鍵漏えい・組織改編への対処であり、**受領時点で有効だった署名が後から失効しても、受領済み文書の証拠価値への影響は限定的**（署名時点で有効だったことが重要）
- 厳密な有効性検証が必要な場面（争訟等）では、e-Gov自身が提供する公文書署名検証機能（オンライン）を使えばよい。ローカルアーカイブが二重に実装する必然性は薄い

### 3-3. 結論

- **失効確認はスコープ外のままとする。** 検証結果の表示に「証明書チェーンは検証済み・失効確認は未実施」と明示し、誤解を防ぐ
- 将来オプション: ユーザーが手動で入手したCRLファイル（DER）を書庫外の設定領域に置けば読み込んで照合する「オフラインCRL」方式なら NFR-03 と両立する（pyhanko-certvalidator が同方式をサポート）。需要が出るまで実装しない

## 4. Pythonライブラリの評価（実機PoCの結果）

実サンプル2件に対して、候補ライブラリで検証を試行した（uv の一時環境で実施。プロジェクト依存には追加していない）。

### 4-1. signxml — ❌ そのままでは不可

signxml 5.1.0（＋cryptography 49.0.0、lxml 6.1.1）で `XMLVerifier().verify()` を試行した結果、**両サンプルとも2経路で失敗**:

1. `ca_pem_file=`（チェーン検証つき）経路:
   - 労基署: `certificate contains unaccounted-for critical extensions (2.5.29.32)` — GPKI証明書の**critical certificatePolicies** を signxml内蔵の検証ポリシーが許容しない
   - 年金機構: `certificate KeyUsage does not allow digitalSignature` — KeyInfoに同梱された**自己署名ルートを署名者候補として扱ってしまい**、CA証明書のKeyUsage（certSign/cRLSign）で弾かれる
2. `x509_cert=`（署名者証明書ピン止め）経路: `Signature verification failed`（署名値不一致）。原因は下記 4-2 のlxml部分木C14N問題とみられる（同一環境で `etree.canonicalize` を使った手動検証は成功するため、署名自体は正しい）

検証ポリシーのカスタマイズ余地が狭く、GPKIプロファイル（critical certificatePolicies・ルート同梱・大文字ID参照）との相性が悪い。**採用しない。**

### 4-2. lxml — ⚠️ C14NのAPI選択に注意（本体は既存依存で済む）

lxml 6.1.1（libxml2 2.14.6、本プロジェクトの現行venv）で確認した挙動:

- `etree.tostring(el, method="c14n", exclusive=False)` による**部分木**の inclusive C14N は、部分木ルートから3階層以上深い子孫要素に**偽の `xmlns=""` を出力する**（最小再現を確認済み）。この出力で署名値を検証すると必ず失敗する
- `etree.canonicalize(xml_data=etree.tostring(si).decode())`（SignedInfoを単独ツリーに切り出してからC14N 1.0）は正しい出力になり、**両サンプルの署名値がRSA-SHA256で検証成功**

本文書の署名はSignedInfo自身が名前空間を1つしか持たないため「単独ツリーに切り出してからinclusive C14N」で正しく再現できる。

### 4-3. cryptography — ✅ チェーン検証・RSA検証とも可（要 45+）

- RSA-SHA256（PKCS#1 v1.5）の署名値検証: 問題なし
- `cryptography.x509.verification` のチェーン検証: 既定ポリシーはWebPKI（TLS）向けでGPKI証明書のcritical certificatePolicies拡張で失敗するが、**カスタム拡張ポリシー**（cryptography 45で追加）で通る。PoCで成功した構成:
  - CAポリシー: `ExtensionPolicy.permit_all()` に `BasicConstraints`（critical）必須を追加（必須にしないとAPIがポリシーを拒否する）、`CertificatePolicies` は許容
  - EEポリシー: `permit_all()` ＋ `CertificatePolicies` 許容
  - 検証時刻・信頼点Store・チェーン深さを指定でき、失効確認は行わない（現状のcryptographyはCRL検証未対応 — 本件では§3の結論と整合し、むしろ好都合）
- 両サンプルで「官職証明書 → OfficialStatusCA」の2段チェーン構築・検証に成功

### 4-4. その他の候補

| ライブラリ | 評価 |
|---|---|
| python-xmlsec（libxmlsec1バインディング） | XMLDSig標準実装としては最有力だが、Cライブラリ依存（ビルド・配布の負担、macOSでのwheel供給が不安定）。チェーン検証の信頼点・ポリシー制御も別途必要。「uv syncだけで動く」現行構成（NFR-07）を壊すため次点 |
| pyhanko-certvalidator | RFC 5280パス検証＋オフラインCRL供給に対応。失効確認を将来実装する場合の候補。純粋な追加依存になるため現時点では不採用 |
| oscrypto/certvalidator（wbond版） | メンテナンス停滞。不採用 |

## 5. 推奨方針（実装する場合の設計）

### 5-1. 方式: 既存 `verify.py` を拡張する自前最小実装

依存追加は **cryptography 1つ**（≥45）。実装は2関数・合計100行程度の見込み:

1. **署名値検証**: 鑑XMLからSignedInfoを取り出し、単独ツリーとしてC14N 1.0（§4-2の方法）→ 同梱の署名者証明書の公開鍵でRSA-SHA256検証。`#DOCBODY` 参照のダイジェストは大文字`ID`属性で解決してC14N後にSHA-256照合（外部ファイル参照は既存実装を流用）
2. **チェーン検証**: 署名者証明書を、ローカルに置いた信頼点PEM束（§2-2）に対して `x509.verification`＋カスタム拡張ポリシー（§4-3の構成）で検証

理由: 既製ライブラリ（signxml）がGPKIプロファイルと非互換である以上、回避策の積み重ねより、対象プロファイルが単一（§1-1）であることを活かした最小実装のほうが保守しやすい。検証済みのPoCコードがそのまま土台になる。

### 5-2. 検証時刻と長期保存（LTV）の扱い

- 官職証明書は5年、信頼点も2029年に期限が切れる。**期限切れ後に「現在時刻」で検証すると正当な署名も失敗する**
- タイムスタンプ（XAdES-T等）が付いていないため、厳密な過去時点検証は原理的にできない
- 現実解: **取り込み時（または初回検証時）に検証し、結果と検証時刻をDBに記録**する。「YYYY-MM-DD時点でチェーン有効・署名一致」という記録を保全する運用とし、期限切れ後の再検証は「検証時刻を明示したうえで参考情報」とする

### 5-3. UI・データモデル

- `documents.verify_status`（ダイジェスト照合）とは別に `signature_status`（ok / invalid / no_anchor / expired / unverifiable）と `signature_verified_at` を持たせる
- 詳細画面のバッジは「改ざんなし（ダイジェスト） / 署名有効（チェーン検証済・失効確認なし）」の2段表示
- 信頼点PEMが未配置の場合は `no_anchor` とし、入手手順（§2-2）へのガイダンスを表示

### 5-4. 実装しないこと

- オンラインの失効確認（CRL/OCSP） — NFR-03と衝突（§3）
- XAdES/タイムスタンプ付与 — 原本を書き換えないため不可能かつ不要（NFR-01）
- 汎用XMLDSig検証器 — 対象はGPKI官職署名プロファイルに限定する

## 6. PoC記録（再現手順の要旨）

一時環境（`uv run --with cryptography --with signxml --no-project`）で実施。実文書はリポジトリ外で読み取りのみ。

| 試行 | 結果 |
|---|---|
| signxml 5.1.0 `verify(ca_pem_file=...)` | 両サンプル失敗（critical certificatePolicies / CA証明書を署名者扱い） |
| signxml 5.1.0 `verify(x509_cert=<署名者PEM>)` | 両サンプル失敗（署名値不一致 — lxml部分木C14Nの偽xmlns=""による） |
| lxml `tostring(si, method="c14n")` → RSA検証 | 失敗（Transforms以深に `xmlns=""` が混入することを確認） |
| lxml `etree.canonicalize`（SignedInfo単独ツリー） → cryptographyでRSA-SHA256検証 | **両サンプル成功** |
| `#DOCBODY`（大文字ID属性）のC14N＋SHA-256照合 | **両サンプル成功**（inclusive/exclusiveとも一致） |
| cryptography 49.0.0 `x509.verification`（既定ポリシー） | 失敗（critical certificatePolicies） |
| 同・カスタム拡張ポリシー（BasicConstraints必須＋CertificatePolicies許容） | **両サンプル成功**（官職証明書→OfficialStatusCAの2段チェーン） |

## 出典

- [政府認証基盤（GPKI）について](https://www.gpki.go.jp/documents/gpki.html) — 政府共用認証局への集約、官職認証局・日本政府認証局の構成
- [政府認証基盤(GPKI)におけるフィンガープリント | e-Govポータル](https://www.e-gov.go.jp/digital-government/gpki.html) — 自己署名証明書フィンガープリントの公表ページ
- [公文書署名検証について | e-Gov電子申請](https://shinsei.e-gov.go.jp/contents/help/guide/signature-verification) — e-Govのオンライン署名検証機能・官職証明書の説明
- [アプリケーション認証局2の廃止告知（gpki.go.jp/apca2/）](https://www.gpki.go.jp/apca2/) — 2019年業務終了
- [SignXML: XML Signature and XAdES in Python](https://xml-security.github.io/signxml/) / [GitHub](https://github.com/XML-Security/signxml)
- [cryptography — X.509 verification](https://cryptography.io/en/latest/x509/verification/) — カスタム拡張ポリシーAPI
- [pyhanko-certvalidator](https://pypi.org/project/pyhanko-certvalidator/) — オフラインCRL対応のパス検証
- 実サンプル2件の鑑XML・同梱証明書の解析（ローカル、リポジトリ外・非コミット）
