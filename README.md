# 名刺管理アプリ（a013_card） 設計ドキュメント一覧

## ファイル一覧

| ファイル | 内容 |
|---------|------|
| CLAUDE.md | Claude Code用プロジェクト指示書（★これが最重要） |
| meishi_ddl.sql | PostgreSQL DDL（CREATE TABLE文一式） |
| meishi_screen_design.md | 画面設計書（11画面の詳細 + Blueprint構成） |
| meishi_ocr_design.md | OCRフロー設計書（Vision + Claude パイプライン + コード例） |
| meishi_r2_design.md | R2接続設計書（boto3ユーティリティ + アップロードフロー） |
| meishi_app_handoff.md | 元の引き継ぎドキュメント（Sonnetで作成した要件定義） |

## Claude Codeでの開発開始手順

1. `D:\kanade_system\app\a013_card` ディレクトリを作成
2. CLAUDE.md をそのディレクトリに配置
3. Claude Codeでそのディレクトリを開く
4. 「Phase 1の基盤構築から始めて」と指示

CLAUDE.mdに全設計が凝縮されているので、これ1枚あればClaude Codeが開発を進められます。
他の設計ドキュメントは参照用・詳細確認用です。
