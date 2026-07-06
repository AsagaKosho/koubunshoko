# テストフィクスチャ

実公文書と同じ要素構造・エンコーディングを模した**合成データ**（D-05）。
実在の到達番号・法人・個人情報は含まない。

- `totatuno_set/` … 厚労省審査結果系（TOTATUNO）。鑑XML＋表示用XSL（shift_jis出力指定）＋控えPDF
- `docno_set/` … 年金機構kagami系（DOCNO）。鑑XML＋kagami.xsl＋様式XML＋様式XSL＋Shift_JIS CSV明細

ZIP版（cp437名修復が必要なケースを含む）は `tests/zipbuild.py` で
これらのフォルダからテスト実行時に生成する。

鑑XMLの XMLDSig Reference のうちファイルを指すもの（URLエンコード済みURIの
ケースを含む）の DigestValue は、参照先フィクスチャファイルの実際の
SHA-256（base64）と一致させてある（改ざん検知テストの「一致」ケース用）。
フィクスチャのファイル内容を変更した場合は DigestValue も再計算すること。
同一文書内参照（`#DOCBODY`）の DigestValue はダミーのまま。
