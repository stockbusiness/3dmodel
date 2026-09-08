# 実装計画（フェーズA）

正本：`docs/spec/3d_art_validation_spec_v1_1.md`（Ver.1.1 / 2026-09-08）
作業ルール：`CLAUDE.md`
未決事項：`docs/decisions.md`

本書は仕様第15章のディレクトリ構成と第16章の実装順に基づく、ステージ別の作成ファイルと完了条件の一覧である。
先回り実装をしないための境界線を各ステージに明記する。

## 0. 前提と共通ルール

- 1回の作業は1ステージ以下。ステージをまたいで先回りしない（仕様第16章、CLAUDE.md §4）。
- 各ステージ完了時に `VERSION` を更新し、`CHANGELOG.md` に日本語で追記する。
- 各ステージの完了報告は仕様第17章の形式に従い、「実装済み」「モック検証済み」「実API検証済み」「未確認」を分ける。
- 仕様に無い判断は `docs/decisions.md` に分類タグ付きで記録する。
- `LIVE_API_ENABLED=false` が既定。A3.5とA5以外では実APIを呼ばない。

## 1. ステージ別の作成ファイルと完了条件

### A1：骨組み・モック（ブランチ `feature/a1-skeleton` ／ VERSION 0.1.0）

対象：仕様第16章 A1。ログイン→画像（同意含む）→疑似生成→GLB→評価→CSVまでをモックで通す。

作成ファイル：

| 区分 | ファイル |
| --- | --- |
| 基盤 | `pyproject.toml`、依存ロックファイル、`.gitignore`、`.env.example`、`Dockerfile`、`compose.yaml`、`README.md`、`VERSION`、`CHANGELOG.md`、`THIRD_PARTY_NOTICES.md` |
| 入口 | `app/main.py`、`app/config.py`、`app/auth.py`、`app/db.py` |
| モデル | `app/models/`（第9章の全テーブル。この時点で未使用の列も作る） |
| 保存処理 | `app/repositories/`、`app/services/storage.py`（save/read/stat境界。ローカル実装のみ） |
| 画面/API | `app/routes/auth.py`、`experiments.py`、`assets.py`、`generations.py`、`reviews.py`、`files.py`、`export.py` |
| サービス | `app/services/image_intake.py`（デコード検査・EXIF除去・送信用コピー）、`app/services/review_aggregate.py`（最小）、`app/services/csv_export.py` |
| アダプター | `app/providers/base.py`、`app/providers/mock.py`（成功系のみ） |
| ワーカー | `app/worker.py`（入口のみ。同期的な疑似処理で可） |
| 画面資産 | `app/templates/`（日本語）、`app/static/`（バージョン固定 model-viewer 同梱） |
| 変更管理 | `migrations/`（Alembic 初期リビジョン） |
| CLI | 初期管理者作成コマンド（対話入力。平文をログ・Gitに出さない） |
| 試験 | `tests/`（正常系＋第13章の試験10・11）、`fixtures/`（自作の単純形状サンプルGLB、合成データと明示） |

範囲：第3章の構成、第9章のデータ設計、第6.1章のログイン、第6.2〜6.3章の検証セット作成・画像アップロード（利用同意を含む）・加工画像追加（加工種別は選択式）、第8章のMockアダプター、第6.5章の最小表示、第10章の評価入力とCSV出力（UTF-8 BOM・数式エスケープ）、第12章のうち画像のデコード検査・EXIF除去・拡張子偽装拒否・認証付きファイル配信・パストラバーサル禁止。

**この段階で実装しない**：外部API、上限額判定、送信枠、冪等キー、ブラインド評価、形状メトリクス抽出、判定表、校正セット、限定公開環境。

完了条件：`docker compose up` でログイン→画像→疑似生成→GLB表示→評価→CSVまでモックで通る。READMEの手順で再現できる。試験10・11を含む pytest が通る。

### A2：永続ワーカー・復旧・冪等・上限額（ブランチ `feature/a2-worker` ／ VERSION 0.2.0）

対象：仕様第16章 A2。

作成・変更ファイル：

| 区分 | ファイル |
| --- | --- |
| ワーカー | `app/worker.py`（永続ループ、lease、状態確認とダウンロードの処理単位分離、全外部通信のタイムアウト） |
| サービス | `app/services/generation_manager.py`（状態遷移表の全分岐・復帰経路）、`app/services/cost_guard.py`（上限額判定）、`app/services/quota.py`（送信枠）、`app/services/idempotency.py`（フォームトークン方式） |
| API | `retry`、`refresh`、`retry-download`、`cancel`、`resolve-submission`、`resolve-artifact` を `app/routes/generations.py` に追加。`POST /api/comparisons` |
| アダプター | `app/providers/mock.py` に障害切替（成功／事業者失敗／429／作成応答タイムアウト／長時間待機／ダウンロード失敗／不正GLB／上限額超過） |
| 変更管理 | Alembic 追加リビジョン（必要な場合のみ） |
| 試験 | 第13章の必須試験 1〜9、13、15、16、17、19、20 |

範囲：第8章の状態遷移表とワーカーの処理単位、lease（BEGIN IMMEDIATE＋条件付きUPDATE、lease_owner/lease_until、期限切れ時のsubmitting→submission_unknown）、第7章の冪等キー（同キー・別本文は409）、第11章の上限額判定（セット・全体、比較は2社分合算）と送信枠（asset×providerで2回）を依頼登録と同一短期トランザクションで処理、第9章の枠の消費・返却ルール。

**この段階で実装しない**：実API、ブラインド評価、判定表、比較画面、限定公開環境。

完了条件：web と worker が別プロセスで動き、生成中に worker を再起動しても同じタスクを再確認して完了する。上記試験がすべて通る。DBロックを保持したまま外部通信しないことをコード上で確認できる。

### A3：Tripo/Meshyアダプター（ブランチ `feature/a3-providers` ／ VERSION 0.3.0）

対象：仕様第16章 A3。着手時にまず第4章の公式資料を実際に読み、`docs/provider-contracts.md` に記録する。

作成・変更ファイル：`app/providers/tripo.py`、`app/providers/meshy.py`、`app/services/download_guard.py`（HTTPS・許可ホスト・DNS/IP検査・リダイレクトごとの再検査）、`app/services/glb_inspect.py`（形式検査＋形状メトリクス抽出）、プリセット投入データと価格版データ、`docs/provider-contracts.md`、`THIRD_PARTY_NOTICES.md` 追記。

記録項目：採用SDK/ライブラリのタグまたはコミット、モデルID、パラメーター、返却形式、エラー、タイムアウト、課金条件、送信画像・生成物の保持期間、学習利用の可否とopt-out手段、生成物の利用条件、確認日。公式資料で確認できない項目は `UNVERIFIED` とし、推測で埋めない。`UNVERIFIED` を含むプリセットは画面で「未確認」表示にし、実生成を選択不可とする。

**この段階で実装しない**：実API呼び出し（`LIVE_API_ENABLED` は false のまま）、比較画面、判定表。

完了条件：`docs/provider-contracts.md` が確認日付きで埋まる。`UNVERIFIED` 項目が明示され、該当プリセットが実生成不可になっている。試験11が実アダプターの検査経路でも通る。

### A3.5：早期実感触（ブランチ `feature/a3-5-early-check` ／ VERSION 0.3.5）

対象：仕様第10章「早期実感触（A3.5）」。**ユーザーがAPIキーと上限額を設定済みであることが前提**。未設定なら手順のみ残して実施しない。

作成・変更ファイル：実行前チェック（キー有無・価格確認日・上限額・同意状態・`UNVERIFIED` なし）の画面とREADME手順、集計上の「早期確認」区分、`docs/early-check/`、`docs/validation-report.md`。

範囲：5題材×2社＝10件（$5程度）の実生成。完了後に各件の技術状態・待ち時間・見積・形状メトリクス・GLB読込可否を記録。

**禁止**：再生成の自動実行、10件を超える送信、APIレスポンス原文やキーのログ出力。

完了条件：10件の実生成結果が「早期確認」として通常検証と分離して記録される。目視所見が合格判定でないと明記されている。

### A4：品質比較画面・判定表・限定公開（ブランチ `feature/a4-review-ui` ／ VERSION 0.4.0）

対象：仕様第16章 A4。

作成・変更ファイル：`app/templates/` の比較画面、`app/services/blind.py`（A/B割当・開示）、`app/services/verdict_table.py`（判定表）、`app/services/calibration.py`（校正セット）、費用画面と手動実績入力、`POST /api/comparisons/{id}/reveal`、`deploy/`（Caddyfile例・限定公開compose上書き・ファイアウォール手順、すべてダミー値）、`docs/quality-test-plan.md`、Playwright試験、第13章の試験12・14・18。

範囲：第6.5章の比較画面（PC並列／スマホ縦並び、視点操作、基準角度調整、背景色切替、スマホはモデル切替）、ブラインド評価モード（既定ON）、第6.6章の校正セットと判定表、第11章の費用画面と警告・停止、第6.4章のポーリング（詳細5秒／一覧15〜30秒）。

**この段階で実装しない**：30題材の実API試験（A5）。

完了条件：ブラインド中に画面HTML・APIレスポンス・配信URL・ファイル名へサービス名等が出ないことを試験18で確認。判定表が仕様第10章の閾値表どおり表示される。実機確認の手順とチェックリストが `docs/quality-test-plan.md` にある（エミュレーションだけで実機合格としない）。

### A5：小額実API試験（ブランチ `feature/a5-live-test` ／ VERSION 1.0.0）

対象：仕様第16章 A5。**VPS配置と実機確認が済んでいることが前提**。

範囲：検証セット「初回30題材」を作成し2社比較で60件を送信。不合格の再生成は自動で行わず、運営の指示があった件のみ retry。`docs/validation-report.md` に第17章の項目を記録。

**禁止**：評価の代行、合格判定の記入、架空の数値の記入。

完了条件：60件の送信結果と待ち時間p50/p95、見積合計、要照合額、残る `UNVERIFIED`、発見した不具合が記録されている。品質評価と判定表の最終判断は運営が行う。

## 2. ソフトウェア完了条件（仕様第16章）

Dockerで起動、モックで全フロー利用、必須自動試験（第13章の20件）合格、秘密情報が出ない、状態復旧ができる、上限額と送信枠の超過を防ぐ、同意なし画像を送信しない、READMEが再現可能。

教室採用条件はこれと別であり、実作品による検証と第10章の判定表による運営の判断で決まる。開発エージェントが架空の成功率や判定を記入して完了にしない。
