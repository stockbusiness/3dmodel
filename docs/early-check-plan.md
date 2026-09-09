# A3.5 早期実感触（Tripo 単独）実施手順

**目的：** そもそも見込みがあるかを、画面完成を待たずに少数の実生成で確かめる（仕様第10章）。
**この評価は合格判定ではない。** 集計上も「早期確認」として通常検証と分けて表示する。
（仕様は10件と定めるが、今回は下記の事情で5件になる。）

---

## 0. この回の条件（2026-09-09 時点）

仕様は「5題材 × 2社 ＝ 10件（$5程度）」と定めているが、**今回は Tripo 単独で行う。**

| 項目 | 内容 |
| --- | --- |
| 事業者 | **Tripo のみ** |
| 件数 | **5題材 × 1社 ＝ 5件** |
| 見積上限 | **$1.50**（$0.30/件 × 5） |
| 判断の記録 | `docs/decisions.md` **T-13**（案A採用） |

**Meshy を外した理由：** 無料プランでは API キーを発行できず、本システムから呼べないため
（`docs/provider-contracts.md` 第2.8節、`docs/decisions.md` T-12）。
Pro（$20/月）以上を契約すれば参加できる。

**そのため、この回では2社比較ができない。** 判定表の「サービス×題材タグ」は
Tripo の列だけになる。**事業者選定の材料にはならない**点を、結果を見るときに忘れないこと。

---

## 0.1 どこで実行するか（重要）

**A3.5 は運営の環境（手元のPC、またはVPS）で実行する。**

開発に使っているサンドボックスからは**事業者のAPIへ接続できない**
（egress proxy が CONNECT に 403 を返す。`api.tripo3d.ai` / `api.meshy.ai` /
`assets.meshy.ai` で確認済み。`docs/decisions.md` E-2）。
したがって、この手順は運営が自分の環境で実行する。

環境の作り方は `README.md` の「起動手順（Docker Compose）」のとおり。

---

## 1. 事前に用意するもの

| # | 用意するもの | 備考 |
| --- | --- | --- |
| 1 | **Tripo の APIキー** | `https://developers.tripo3d.ai/ja/keys` で作成。**作成時に1回だけ表示される**ので、その場で控える |
| 2 | **試験用の画像5枚** | 下記「2. 題材の選び方」を必ず読むこと |
| 3 | **上限額** | `APP_GLOBAL_COST_CAP_USD`。$1.50 に対して余裕を見て **$3** 程度を推奨 |

---

## 2. 題材の選び方（重要）

**Tripo の無料アカウントで送る画像は、Tripo に権利を渡してよいものに限る。**

Tripo 利用規約 第5.2.1条は、無料利用者について次のように定めている
（`docs/provider-contracts.md` 第1.6.1節）。

> Tripo retains all rights … in and to the **Inputs and Outputs** submitted or generated
> by Free Users, as well as all Intellectual Property rights arising therefrom.

`Inputs` は「送信した画像そのもの」を含む。したがって：

- ❌ **生徒の作品は使わない**
- ❌ 権利を手放したくない作品は使わない
- ⭕ **運営・講師が自作した試験用の画像**、または権利を運営が持つ画像

また、**個人を特定できる情報（顔・氏名・学校名など）が写り込んでいないこと**を
送信前に確認する。

**題材タグは5枚でばらけさせる。** 仕様第10章の判定表は題材タグ別に見るため、
同じタグに偏ると比較の材料にならない。

- 単体キャラ / 動物 / 雑貨 / 人物 / 風景 / 複数対象 / 細部多め / その他

**出所の分類は「運営・講師の自作」**（`staff_original`）にする。
**利用同意は「不要（自作・お手本等）」**（`not_required`）でよい。

画像は `docs/early-check/` に置く（このディレクトリの中身はコミットしない。
`docs/early-check/README.md` を参照）。

---

## 3. 実施手順

### 3.1 キーと上限額を設定する

**`.env` はリポジトリの一番上（`compose.yaml` と同じ場所）に置く。**
はじめは存在しないので、雛形を写して作る。

```
cd <リポジトリのルート>
cp .env.example .env
python3 -c "import secrets; print('APP_SECRET_KEY=' + secrets.token_urlsafe(48))"
```

Docker Compose は `compose.yaml` と同じ場所の `.env` を自動で読む。

**署名鍵は手で写さず、コマンドで書き換える。**
手で貼ると説明文（`（上で出た値）` など）を消し忘れやすく、
起動時にエラーになる。PowerShell ならこの2行で済む。

```powershell
$key = python -c "import secrets; print(secrets.token_urlsafe(48))"
(Get-Content .env) -replace '^APP_SECRET_KEY=.*', "APP_SECRET_KEY=$key" | Set-Content .env -Encoding utf8NoBOM
```

そのうえで `.env` をエディタで開き、次の3つを設定する。
**`（…）` のような説明文は必ず消して、実際の値だけを残すこと。**

```
TRIPO_API_KEY=tsk_ここに実際のキー
APP_GLOBAL_COST_CAP_USD=3
APP_LIVE_API_ENABLED=true
```

書けたか確認する（値は表示されない）。

```powershell
Get-Content .env | Where-Object { $_ -match '^(APP_SECRET_KEY|TRIPO_API_KEY|APP_LIVE_API_ENABLED|APP_GLOBAL_COST_CAP_USD)=' } | ForEach-Object {
  $n, $v = $_ -split '=', 2
  $v = $v.Trim()
  $note = if ($v -match '[^\x20-\x7E]') { '← 日本語が混じっています（置き換え漏れ）' } else { '' }
  '{0,-28} 長さ{1,-4} {2}' -f $n, $v.Length, $note
}
```

**`.env` はコミットしない。**

**画面を開かずに端末で確かめることもできる。**

```
docker compose run --rm web python -m app.cli check-provider tripo
```

APIキーの値は表示されない。設定されているか、先頭が想定どおりかだけが出る。

再起動したら `/admin` を開き、Tripo の欄が次の状態になっていることを確認する。

- APIキー：**設定済み**
- 実API生成：**有効**
- 全体上限額：**3 USD**
- 成果物の配信ホスト：**未設定（注意）** ← この時点ではこれで正しい（3.3で設定する）

#### 3.1.1 Windows（PowerShell）の場合

コマンドは同じだが、`docker compose` を使うには **Docker Desktop が起動している**
必要がある。次のエラーは「Docker Desktop が動いていない」という意味である。

```
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

スタートメニューから Docker Desktop を起動し、左下が **Engine running** に
なるのを待ってからやり直す。

#### 3.1.2 Docker を使わずに動かす場合（要注意）

Docker Desktop が使えないときは Python で直接動かせる。ただし**落とし穴がひとつある。**

**`.env` から読まれるのは `APP_` で始まる設定だけである。**
`TRIPO_API_KEY` は `APP_` で始まらないため、**`.env` に書いても読まれない。**
シェルの環境変数として設定する必要がある
（Docker Compose 経由なら `.env` から読まれるので、この問題は起きない）。

PowerShell の場合：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pip install "tripo3d[async]==0.4.2"

# APP_ で始まるものは .env から読まれる。APIキーだけは環境変数に置く
$env:TRIPO_API_KEY = "tsk_（作成したキー）"

.\.venv\Scripts\python -m app.cli init-db
.\.venv\Scripts\python -m app.cli check-provider tripo
.\.venv\Scripts\python -m app.cli check-provider tripo --connect
```

画面も見る場合は続けて：

```powershell
.\.venv\Scripts\python -m app.cli create-operator teacher1 --display-name "講師1"
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**ワーカーは別のウィンドウで動かす。** そのウィンドウでも
`$env:TRIPO_API_KEY` を設定してから起動すること（外部へ送信するのはワーカー）。

```powershell
$env:TRIPO_API_KEY = "tsk_（作成したキー）"
.\.venv\Scripts\python -m app.worker
```

### 3.2 接続テストで残高を確かめる

`/admin` の Tripo の「接続テストを実行」を押す。
**生成は行わないので課金は発生しない。**

端末から行う場合はこちら。

```
docker compose run --rm web python -m app.cli check-provider tripo --connect
```

- **成功すれば、事業者側のクレジット残高が参考表示される。**
  ここで **無料アカウントにクレジットがあるか（U-23 の残り）が分かる。**
- 残高が **0 なら、この先に進めない。** クレジットの購入が必要になる
  （$0.01/credit、1件30credits）。
- 認証が通らなければ、キーの取り違えか、IP制限（3.5参照）を疑う。

### 3.3 配信ホストを確認する（1件だけ実行する）

**Tripo の成果物がどのホストから配信されるかは、実物のURLでしか分からない**
（`docs/provider-contracts.md` の U-7）。そのため次の順で確認する。

1. 検証セットを **「実API」「早期確認」** で作る（3.4 参照）
2. 題材を1枚だけ登録し、`tripo-standard` で生成する（**$0.30 かかる**）
3. 生成は成功するが、**保存で失敗する**。生成の詳細画面に次のように出る
   （画面は5秒ごとに更新されるので、読み込み直さなくても出る）：

   > ダウンロードを許可するホストが設定されていません。**このURLのホストは
   > `〇〇〇` です。**確認のうえ設定してください

4. その `〇〇〇` を `.env` に設定し、**web と worker を再起動する**

   ```
   APP_TRIPO_DOWNLOAD_HOSTS=〇〇〇
   ```

5. 生成の詳細画面で **「保存だけ再試行」** を押す。
   **これは追加課金の無い操作である**（生成をやり直さない）

**この1件は捨てない。** 保存し直せば、5件のうちの1件としてそのまま使える。

> **なぜ最初から設定しておけないのか：** 配信ホストを推測で設定することは
> `CLAUDE.md` 第2章が禁じている（存在しない値を推測して実装しない）。
> 実物で確認するのが唯一の方法である。

### 3.4 検証セットを作って5件を流す

1. 検証セットを作る
   - **実/モック：実API**
   - **区分：早期確認**（`early_check`）。**通常検証と混ぜない**
   - セット上限額：**$1.50**
2. 題材を5枚登録する（分類・題材タグ・利用同意を入れる）
3. 各題材に `tripo-standard` で生成を1件ずつ受け付ける
4. 一覧が自動で更新される。全件が「評価待ち」になるまで待つ

**同時外部タスクは全体2件・各社1件**に制限してあるので、5件は順に処理される。

### 3.5 資産の公開範囲を確認する（U-17）

Tripo 利用規約 第4条は「選ばなければ最も公開度の高い設定になりうる」と定めている
（`docs/provider-contracts.md` 第1.6.5節）。

**1件目が生成できたら、Tripo 側の画面でその資産の公開範囲を確認する。**
公開されている場合は、非公開に変更できるかを調べ、結果を
`docs/provider-contracts.md` の U-17 に記録する。

あわせて、**Tripo の APIキー画面に IP 制限がある**（`docs/decisions.md` S-7）。
限定公開のVPSから使うなら、そのVPSのIPに絞っておくと安全である。

---

## 4. 結果の見方

### 4.1 目視でみる

`/comparisons/{id}` は2社比較用なので、単社の今回は各生成の詳細画面
（`/generations/{id}`）で元画像と3Dモデルを見比べる。

見るのは仕様第10章の5軸である。

| 軸 | 意味 |
| --- | --- |
| 元画像への忠実さ | 形・比率・特徴が元の絵に合っているか |
| 形状の破綻の少なさ | 穴・貫通・溶けた部分・分離した部品がないか |
| 色・質感の再現 | 色味とテクスチャが元の印象に近いか |
| 教材としての見栄え | 生徒に見せて「おお」と思えるか |
| スマホでの見やすさ | 実機で回して細部が分かるか |

### 4.2 集計で見る

`/experiments/{id}/report` を開く。**「早期確認」と表示され、通常検証とは
別のセットとして集計される**ことを確認する。

**この5件の判定は合格判定ではない。** 判定表は Tripo 1列しかなく、
題材タグごとのサンプルも1件ずつなので、「サンプル不足」と出るのが正しい。

### 4.3 費用を照合する

`/experiments/{id}` の費用欄で、見積累計が **$1.50** になっていることを確認する。
実費は Tripo 側の請求で確かめ、**手動で実績を入力する**（仕様第11章）。
見積と実費がずれていたら、その差の原因を `docs/provider-contracts.md` に記録する。

---

## 5. 終わったら記録すること

| 記録先 | 内容 |
| --- | --- |
| `docs/validation-report.md` | 目視所見（5軸それぞれ）、所要時間、実費。**合格判定ではないと明記する** |
| `docs/provider-contracts.md` | 配信ホスト（U-7）、無料枠のクレジット有無（U-23）、公開範囲（U-17） |
| `docs/decisions.md` | 見込みがあるか無いかの運営判断、次に進むかどうか |

**見込みが無いと判断すれば、以降の実装を止められる。** それがこの5件の目的である。

---

## 6. 途中で止まったときの見方

| 症状 | 意味 | 操作 |
| --- | --- | --- |
| 保存失敗 | 生成は終わっている。配信ホストの設定漏れが濃厚 | ホストを設定して **保存だけ再試行**（課金なし） |
| 受付結果不明 | 外部に届いたか分からない。**自動で再送信しない** | Tripo 側の履歴と照合し、手動で紐付けるか未作成を確認する |
| 検査で不合格 | GLBの中身が要件を満たさない（外部URI参照など） | 保存はしない。内容を記録して次へ |
| 事業者側で失敗 | 生成が失敗した。**送信枠は返却される** | 必要なら再生成（**追加課金あり**） |

**追加課金が発生するのは「再生成」だけである。**
「状態の再確認」「保存だけ再試行」は課金されない。
