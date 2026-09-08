# 3Dアート品質検証システム（フェーズA）

運営・講師だけで3D生成の品質・成功率・待ち時間・費用・作業時間を記録し、
授業に採用する題材タグと生成サービスを判断するための検証システムです。

- 仕様の正本：`docs/spec/3d_art_validation_spec_v1_1.md`（Ver.1.1）
- 実装計画：`docs/plan.md`
- 判断記録：`docs/decisions.md`

## 現在の到達点（A1）

**モックで動きます。実APIはまだ呼びません。**

| 区分 | 内容 |
| --- | --- |
| 実装済み | ログイン、検証セット、画像登録（利用同意つき）、加工版追加、モック生成、GLB検査と形状メトリクス、3D表示、5段階評価、CSV出力 |
| モック検証済み | 上記の一連の流れ、必須試験10・11 |
| 実API検証済み | なし（`LIVE_API_ENABLED=false`。Tripo / Meshy アダプターは A3 で実装） |
| 未確認 | 事業者のモデルID・価格・データ取扱い条件（`docs/decisions.md` の UNVERIFIED）、実機（iPhone Safari / Android Chrome）での表示、`docker compose build` の実行（開発環境からコンテナレジストリへ接続できないため。`docs/decisions.md` の E-1） |

A2以降で実装するもの：永続ワーカー、再起動復旧、冪等キー、上限額判定、送信枠、
2社比較、ブラインド評価、判定表、限定公開環境。

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

A1のワーカーは、受付済みのまま残った生成を拾う保険として動きます。
永続ジョブ処理・lease・状態の分岐は A2 で実装します。

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
A1で実施している必須試験は10（未認証拒否・CSRF拒否・任意URL取得拒否）と
11（外部URI参照GLB・偽拡張子画像・過大ファイルの拒否）です。

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

1. A3で Tripo / Meshy アダプターを実装し、`docs/provider-contracts.md` に
   モデルID・パラメーター・価格・データ取扱い条件を確認日つきで記録すること
2. プリセットの `UNVERIFIED` が解消していること
3. セット上限額と全体上限額（USD）が設定されていること
4. 送信する画像の利用同意が `granted` または `not_required` であること
5. `.env` の `APP_LIVE_API_ENABLED=true` と APIキーがユーザーの明示的な操作で設定されること

## セキュリティ上の約束

- APIキーはサーバーの環境変数のみ。ログ・CSV・画面・コミットに出しません
- 利用者が入力したURLをサーバーに取得させません。処理対象は画像IDで指定します
- 利用同意が未取得の画像は、UIを迂回してAPIを直接呼んでも外部送信しません
- 画像・GLBは認証つきAPIからのみ配信します。保存キーはUUIDで、元ファイル名は使いません
- model-viewer はバージョンを固定してローカル配信します。CDNのlatestを動的取得しません
