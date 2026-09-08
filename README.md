# 3Dアート品質検証システム（フェーズA）

運営・講師だけで3D生成の品質・成功率・待ち時間・費用・作業時間を記録し、
授業に採用する題材タグと生成サービスを判断するための検証システムです。

- 仕様の正本：`docs/spec/3d_art_validation_spec_v1_1.md`（Ver.1.1）
- 実装計画：`docs/plan.md`
- 判断記録：`docs/decisions.md`

## 現在の到達点（A3）

**モックで動きます。実APIはまだ呼びません。**

| 区分 | 内容 |
| --- | --- |
| 実装済み | ログイン、検証セット、画像登録（利用同意つき）、加工版追加、永続ワーカーによる生成、状態遷移の全分岐と復帰操作、冪等キー、上限額判定、送信枠、2社比較の受付、Tripo/Meshyアダプター、成果物取得の保護、GLB検査と形状メトリクス、3D表示、5段階評価、CSV出力 |
| モック検証済み | 上記の一連の流れ、必須試験 1〜11・13・15・16・17・19・20（自動試験86件）。web と worker を別プロセスで動かし、生成中にワーカーを再起動しても同じ外部タスクを再確認して完了することを確認。Tripo公式SDKはローカルの偽サーバーに向けて実際に動かして確認 |
| 実API検証済み | なし（`LIVE_API_ENABLED=false`） |
| 未確認 | 事業者のモデルID・価格・データ取扱い条件（`docs/decisions.md` の UNVERIFIED）、実機（iPhone Safari / Android Chrome）での表示、`docker compose build` の実行（開発環境からコンテナレジストリへ接続できないため。`docs/decisions.md` の E-1） |

A4以降で実装するもの：ブラインド評価の画面と開示、判定表、校正セット、
費用画面と手動実績入力の画面、一覧のポーリング、限定公開環境。

**両社のプリセットは現在も実生成に選べません。**
エンドポイント・モデルID・パラメーター・状態値は公式資料で確認済みですが、
**価格とデータ取扱い条件が未確認**のためです（`docs/provider-contracts.md`）。

## 起動手順（Docker Compose）

### 1. 設定ファイルを用意する

```bash
cp .env.example .env
# APP_SECRET_KEY に十分に長いランダム文字列を入れる
python3 -c "import secrets; print('APP_SECRET_KEY=' + secrets.token_urlsafe(48))"
```

`.env` はコミットしません（`.gitignore` 済み）。APIキーは A3 以降に必要になります。

### 2. イメージを作り、DBを初期化する

```bash
docker compose build
docker compose run --rm web python -m app.cli init-db
```

`init-db` は Alembic のマイグレーションを最新まで適用し、プリセットを投入します。
Tripo / Meshy のプリセットは「未確認」として無効のまま登録されます。

### 3. 初期アカウントを作る

```bash
docker compose run --rm web python -m app.cli create-operator teacher1 --display-name "講師1"
```

パスワードは対話入力です。画面に表示されず、ログにも残りません。
対話入力ができない環境では `--generate-password` を付けると、自動生成した値を
その場に1度だけ表示します（保管後は画面を閉じてください）。

### 4. 起動する

```bash
docker compose up -d
```

`http://127.0.0.1:8000` を開きます。ローカル開発では 127.0.0.1 にのみ公開します。
遠隔の実機確認は A4 で用意する限定公開環境（VPS＋Caddy）で行います。

### 5. 停止する

```bash
docker compose down          # コンテナのみ停止（データは残る）
docker compose down -v       # データボリュームごと削除（元に戻せません）
```

## ワーカー

`docker compose up` で web と worker が別プロセスとして起動します。
単体で動かす場合：

```bash
docker compose run --rm web python -m app.worker --once   # 1回だけ処理
docker compose run --rm web python -m app.worker          # 常駐
```

ワーカーは1周で次を行います（仕様第8章「ワーカーの処理単位」）。

1. lease が切れた対象の回収。`submitting` のままなら `submission_unknown` へ移し、**自動で再送信しません**
2. 送信（同時外部タスクの上限内で1件）
3. 状態確認（対象を全件、順に）
4. ダウンロード（1件だけ。長い保存が他タスクの状態確認を止めないようにするため）

外部通信のあいだDBのトランザクションを持ちません。ワーカーを止めて再起動しても、
保存済みの外部タスクIDを見て同じタスクを再確認します。新しい生成は依頼しません。

### 生成が止まったときの操作

| 状態 | 意味 | できること | 追加の課金 |
| --- | --- | --- | --- |
| 受付結果不明 | 外部に届いたか確認できていない | 事業者の履歴と照合して「ひも付け」または「未作成を確認」 | なし |
| 保存失敗 | 生成は終わったがファイルを受け取れなかった | 保存だけ再試行 | なし |
| 検査で不合格 | 受け取ったファイルが検査に通らなかった | 保存だけ再試行 → 続く場合は事業者側の失敗と確定 | なし |
| 確認を一時停止 | 30分たっても完了しない | 状態を再確認して監視を再開 | なし |
| 事業者側で失敗 | 事業者が失敗を返した | 理由を書いて再生成 | **あり** |

追加の課金が発生するのは「再生成」だけです。画面にも同じ説明を出します。

## 開発環境（Dockerを使わない場合）

```bash
uv venv --python 3.11
uv pip install -e '.[dev]'
export APP_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
export APP_DATA_DIR=./data
.venv/bin/python -m app.cli init-db
.venv/bin/python -m app.cli create-operator teacher1 --display-name "講師1"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 試験

```bash
.venv/bin/python -m pytest -q
```

テストは本番APIを自動で呼びません。実API試験は明示的な有効化が必要です（仕様第13章）。
現在実施している仕様第13章の必須試験は 1〜11、13、15、16、17、19、20 です
（12・14・18 は A4 で実施します）。

## バックアップと復元

SQLiteのWALを無視した単純コピーはしません（仕様第15章）。

```bash
# バックアップ（SQLiteの正規バックアップAPIを使う）
docker compose exec web python -c "
import sqlite3, os
src = sqlite3.connect(os.environ['APP_DATA_DIR'] + '/app.db')
dst = sqlite3.connect(os.environ['APP_DATA_DIR'] + '/backup.db')
src.backup(dst); dst.close(); src.close()
print('backup.db を作成しました')
"

# 保存物（画像・GLB）ごと取り出す
docker compose cp web:/srv/data ./backup-$(date +%Y%m%d)
```

復元は、停止した状態で `data` ボリュームへ書き戻し、`app.db` と `objects/` の
整合（DBの storage_key に対応するファイルが存在すること）を確認してから起動します。

## 実APIの有効化について

現時点では有効化できません。有効化には次のすべてが必要です（仕様第11章・第16章）。

1. **価格の確認**：Tripo / Meshy それぞれの1件あたりのクレジット数とUSD単価を
   公式の価格ページで確認し、`docs/provider-contracts.md` と
   `app/services/presets.py` に反映すること（第3節に手順あり）
2. **データ取扱い条件の確認**：送信画像・生成物の保持期間、学習利用の可否と
   opt-out手段、生成物の利用条件
3. **成果物の配信ホストの設定**：`APP_TRIPO_DOWNLOAD_HOSTS` /
   `APP_MESHY_DOWNLOAD_HOSTS`。空のままだと成果物を取得しません
4. プリセットの `is_unverified` を解除し、`is_enabled` を有効にすること
5. セット上限額と全体上限額（`APP_GLOBAL_COST_CAP_USD`）が設定されていること
6. 送信する画像の利用同意が `granted` または `not_required` であること
7. `.env` の `APP_LIVE_API_ENABLED=true` と APIキー（Tripoは `tsk_`、Meshyは `msy_` で始まる）が
   ユーザーの明示的な操作で設定されること

1〜4が済むまで、画面上でプリセットを選ぶことはできません。

## セキュリティ上の約束

- APIキーはサーバーの環境変数のみ。ログ・CSV・画面・コミットに出しません
- 利用者が入力したURLをサーバーに取得させません。処理対象は画像IDで指定します
- 利用同意が未取得の画像は、UIを迂回してAPIを直接呼んでも外部送信しません
- 画像・GLBは認証つきAPIからのみ配信します。保存キーはUUIDで、元ファイル名は使いません
- model-viewer はバージョンを固定してローカル配信します。CDNのlatestを動的取得しません
- 事業者からの成果物は、HTTPS・許可ホスト・名前解決したアドレスの検査を通してから取得します。
  リダイレクトは1ホップごとに検査し直し、localhost や private、クラウドのメタデータ用アドレスへは接続しません
- Tripo公式SDKが持つ「SSL検証を無効にして取得し直す」経路と、
  import 時に第三者のIP位置情報サービスへ問い合わせる動作は、いずれも使わないようにしています
