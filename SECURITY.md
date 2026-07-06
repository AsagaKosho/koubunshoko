# セキュリティポリシー / Security Policy

## 設計上の前提

kobunshoko はローカル専用のWebアプリです。

- サーバーは `127.0.0.1` のみにバインドし、認証を持ちません。**外部ネットワークに公開しないでください**
- 外部への通信は一切行いません（XSLT処理・XMLパースもネットワークアクセス遮断済み）
- 取り込んだ公文書のXSL/HTMLはCSPとiframe sandboxでスクリプト実行を遮断して表示します

## 脆弱性の報告 / Reporting a Vulnerability

脆弱性を発見した場合は、公開Issueではなく **GitHubの [Private vulnerability reporting](https://github.com/AsagaKosho/kobunshoko/security/advisories/new)** から報告してください。
Please report vulnerabilities via GitHub's private vulnerability reporting, not public issues.

特に以下の領域の報告を歓迎します: パストラバーサル、Zip Slip、XXE、XSLT経由のファイル/ネットワークアクセス、CSP/sandboxの迂回。
