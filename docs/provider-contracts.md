# 事業者APIの確認記録（仕様第4章）

**確認日：2026-09-08**（別記のあるものを除く）
**確認者：開発エージェント（A3）**

この文書は「公式資料で確認できたこと」と「確認できていないこと」を分けて記録する。
確認できていない項目は推測で埋めない。未確認が残るプリセットは実生成に選べない
（`is_unverified=True`、画面で「価格未確認」と表示）。

## 0. この環境での確認方法（重要）

作業に使ったサンドボックスのネットワーク方針により、以下の**公式ドキュメントサイトへは
接続できなかった**（egress proxy が拒否）。

- `https://developers.tripo3d.ai/en/pricing`
- `https://docs.meshy.ai/en/api/image-to-3d`
- `https://docs.meshy.ai/en/api/pricing`（実際に開けるのは `https://docs.meshy.ai/api/pricing`）
- `https://help.meshy.ai/en/articles/9996860-how-to-use-meshy-image-to-3d`

そのため、**事業者自身が配布している公式の実装**を一次資料として使った。
いずれも配布物のハッシュを控えてある。

| 事業者 | 使った一次資料 | 入手元 | 検証 |
| --- | --- | --- | --- |
| Tripo | 公式Python SDK `tripo3d` 0.4.2（MIT） | PyPI sdist | sha256 `ded61fde78a830e971c95e3cd10ee68e2b0c3339202094b38fd6d46bc266a7b5`。GitHub `VAST-AI-Research/tripo-python-sdk` のタグ `v0.4.2` の存在も確認 |
| Meshy | 公式CLI `meshy-cli` 0.2.0（MIT、`meshy-dev` 配布） | npm tarball | sha1 `748e495d5d2e0550a1182cba67f90f51a231742a`／sha256 `764f372181709c7f81e0f3fe5631ceb379527d25b9ade60ed514a79dac2d55ca` |

**価格ページは読めていない。** したがって価格は未確認のままである。

---

## 1. Tripo

### 1.1 採用したSDK

| 項目 | 内容 |
| --- | --- |
| パッケージ | `tripo3d` |
| 版（固定） | `0.4.2`（PyPI 公開日 2026-07-01） |
| ライセンス | MIT（`Copyright (c) 2023 Tripo Development Team`） |
| GitHub | `VAST-AI-Research/tripo-python-sdk`、タグ `v0.4.2` |
| 追加依存 | `aiohttp`（extra `async`）、`boto3`（extra `s3`）。どちらも入れていない |

### 1.2 確認できた契約

| 項目 | 内容 |
| --- | --- |
| ベースURL | `https://api.tripo3d.ai/v2/openapi`（中国本土向けは `api.tripo3d.com`） |
| 認証 | `Authorization: Bearer <APIキー>`。SDKはキーが **`tsk_` で始まること**を検査する |
| 作成 | `POST /task`、本文 `{"type": "image_to_model", "file": {...}, ...}` → `{"data": {"task_id": ...}}` |
| 状態取得 | `GET /task/{task_id}` → `data` に task_id / type / status / input / output / progress / create_time / running_left_time / queuing_num / error_code / error_msg |
| 状態値 | `queued` / `running` / `success` / `failed` / `cancelled` / `unknown` / `banned` / `expired` |
| 成果物 | `output.model` / `output.base_model` / `output.pbr_model` / `output.rendered_image`（URL） |
| 残高 | `GET /user/balance` → `{balance, frozen}` |
| 画像の渡し方 | URL、アップロード済みトークン、またはローカルファイル。ローカルの場合は SDK が `POST /upload`（`boto3` があれば STS 経由のS3アップロード）を行う |

`image_to_model` の引数（SDK 0.4.2 の定義そのまま）：

`model_version`（`P1-20260311` / `Turbo-v1.0-20250506` / `v3.1-20260211` / `v3.0-20250812` /
**`v2.5-20250123`（既定）** / `v2.0-20240919` / `v1.4-20240625`）、`face_limit`、`texture`、`pbr`、
`model_seed`、`texture_seed`、`texture_quality`（`standard` / `detailed`）、
`geometry_quality`（`standard` / `detailed`）、`texture_alignment`（`original_image` / `geometry`）、
`auto_size`、`orientation`（`default` / `align_image`）、`quad`、`compress`、`generate_parts`、
`smart_low_poly`、`enable_image_autofix`、`export_uv`

### 1.3 SDKの挙動で、こちらが対処したこと

**(a) 作成系の自動リトライ：無い（確認済み）**
`create_task` は `POST /task` を1回行うだけで、再試行のループは無い。
仕様第8章が求める「作成系自動リトライの確認・制御」は、**無効化の必要なし**という結論。

**(b) ダウンロードはSDKを使わない（重要）**
`TripoClient._download_with_ssl_retry` は、SSL証明書の検証に失敗すると
**検証を無効にした接続で取得し直す**実装になっている（`verify_mode = ssl.CERT_NONE`）。
仕様第12章の要求（HTTPS・許可ホスト・DNS/IP検査）を満たさないため、
**SDKのダウンロード経路は使わず**、`app/services/download_guard.py` で自前に取得する。

**(c) import 時の外部通信を止める（重要）**
`tripo3d/__init__.py` は import 時にバックグラウンドスレッドを起動し、
第三者のIP位置情報サービスへ問い合わせる。

- `http://ip-api.com/json/?fields=countryCode`（**平文HTTP**）
- `https://ipapi.co/json/`
- `http://ipinfo.io/json`（**平文HTTP**）

サーバーのIPアドレスを第三者に渡すことになるため、
`app/providers/tripo.py` は import より前に `TRIPO_DISABLE_GEO_DETECTION=1` を設定する。

**(d) タイムアウトを自分でかける**
SDKは個々の要求にタイムアウトを設定していない（aiohttp の既定に委ねている）。
`asyncio.wait_for` で送信30秒・状態確認30秒（設定値）を適用する。

**(e) 取消は未対応**
SDK 0.4.2 に取消のメソッドは無い。`cancel()` は `unsupported` を返す。
`cancelled` という状態値は存在するため、事業者側の管理画面などで取り消された場合は
状態確認で検知できる。

**(f) 画像形式の申告**
`_image_to_file_content` は実際の形式によらず `{"type": "jpg"}` を送る実装になっている。
こちらの送信用コピーはPNG/JPEG/WebPのいずれかなので、この申告が結果に影響するかは未確認。

### 1.4 価格（2026-09-08 確認）

`https://developers.tripo3d.ai/en/pricing` の表示を利用者が確認した。

| 項目 | 値 |
| --- | --- |
| クレジット単価 | **1 credit = $0.01 USD**（従量制。100 credits = $1.00 USD） |
| Image to 3D（テクスチャなし） | 20 credits |
| **Image to 3D（標準テクスチャ）** | **30 credits** |

追加料金（基本クレジットに加算）：

| 追加 | クレジット |
| --- | --- |
| HD Texture | +10 |
| 8K Ultra Texture | +20 |
| HD Geometry Quality | +20 |
| Quad Mesh | +5 |
| Smart Low-poly | +10 |
| Generate Parts | +20 |

仕様第11章の2026-09-08時点の参照値（標準テクスチャ付き30credits、1credit=$0.01、
HDテクスチャ+10、HD形状+20）と一致することを確認した。

**採用プリセット `tripo-standard` の1件あたりの上限見積：**

`texture=true` かつ `texture_quality=standard`、`geometry_quality=standard` で、
`quad` / `smart_low_poly` / `generate_parts` はいずれも無効のため、
**追加料金は発生しない**。

```
30 credits × $0.01 = $0.30 /件 = 300,000 micro-USD
```

このプリセットでは追加料金の発生しうる項目を選べないため、この額が
そのまま上限側の見積になる（仕様第11章「見積は常に上限側を採る」）。

**残っている確認事項（U-12）**：価格表は「H Series / P Series / Splat Series」の
タブで分かれている。上の数値は **H Series** タブの表示である。
採用している `model_version = v2.5-20250123` がどのシリーズに属するかを
確認できていない。3つのシリーズで Image to 3D の価格が同じであれば影響しない。

### 1.5 採用したプリセット

| 項目 | 値 |
| --- | --- |
| code | `tripo-standard` |
| model_id | `v2.5-20250123`（SDKの既定値。より新しい版を選ぶかは価格と品質の確認後に判断する） |
| settings | `model_version=v2.5-20250123, texture=true, pbr=true, texture_quality=standard, geometry_quality=standard, texture_alignment=original_image, export_uv=true` |
| 1件あたりの上限見積 | **$0.30（300,000 micro-USD）** |
| 有効 | **無効**（`model_version` のシリーズ対応とデータ取扱い条件が未確認のため） |

---

## 2. Meshy

### 2.1 一次資料

公式のPython SDKは無い。仕様第4章のとおり、公式契約に基づくHTTPクライアントとして実装した。
契約は公式CLI `meshy-cli` 0.2.0（MIT、`meshy-dev` 配布、リポジトリ `meshy-dev/meshy-cli`）の
ソースで確認した。

### 2.2 確認できた契約

| 項目 | 内容 |
| --- | --- |
| ベースURL | `https://api.meshy.ai/openapi/v1`（`text-to-3d` のみ `/openapi/v2`。今回は使わない） |
| 認証 | `Authorization: Bearer <APIキー>`（キーは `msy_` で始まる） |
| 作成 | `POST /image-to-3d` → `{"result": "<task_id>"}` |
| 状態取得 | `GET /image-to-3d/{task_id}` → Task |
| 一覧 | `GET /image-to-3d?page_num=&page_size=&sort_by=` |
| 削除 | `DELETE /image-to-3d/{task_id}` |
| 状態値 | `PENDING` / `IN_PROGRESS` / `SUCCEEDED` / `FAILED` / `CANCELED` |
| Taskの項目 | `id`, `type`, `status`, `progress`, `preceding_tasks`, `created_at`, `started_at`, `finished_at`, `expires_at`, `task_error.message`, `model_urls`（形式→URLの対応。GLBは `model_urls.glb`）, `texture_urls[]`, `thumbnail_url`, `image_urls[]` |
| エラーの分類 | 400/422 = 内容不正、401 = 認証、402 = 残高不足、404 = 見つからない、429 = レート制限、その他 = サーバー |

`POST /image-to-3d` の本文（公式CLIが送る項目そのまま）：

`image_url`（http(s) URL または `data:` URI）、`input_task_id`、
`model_type`（`standard` = meshy-7 / `smart-topology` = meshy-t2）、
`target_polycount`（smart-topology のみ。100〜15000、既定10000）、
`ultra_mode`（standard のみ。追加課金）、`should_texture`（**既定 false**。false だとテクスチャ無しの下書き）、
`enable_pbr`、`texture_prompt`（600文字まで）、`texture_resolution`（`2k` / `4k` / `8k`）、
`pose_mode`（`a-pose` / `t-pose`）、`image_enhancement`、`remove_lighting`、`target_formats`

### 2.3 実装で決めたこと

- **画像は `data:` URI で送る。** 公式CLIもローカルファイルを `data:` URI として送っている。
  こちらの画像を公開URLに置く必要がなく、仕様第12章に沿う。
  送信用コピー（EXIF方向を正規化し位置情報を除いたもの）を base64 にして載せる。
- **自動リトライは無い。** 公式CLIのクライアントは `fetch` を1回行うだけで、
  `AbortController` によるタイムアウトのみを持つ。こちらも1回だけ送る。
- **取消は未対応。** `DELETE` は用意されているが、
  **実行中のタスクを取り消す操作なのか、記録を消すだけなのかを公式資料で確認できていない。**
  課金の扱いも不明なため `cancel()` は `unsupported` を返す。
- **ダウンロードは `download_guard` を通す。** `model_urls.glb` のURLを検査してから取得する。

### 2.4 価格（2026-09-08 一部確認）

`https://docs.meshy.ai/api/pricing` の内容を利用者が確認した。

**Image to 3D のクレジット数：**

| モデル | テクスチャなし | テクスチャあり | 8Kテクスチャ |
| --- | --- | --- | --- |
| Meshy-6 | 20 | **30** | 35 |
| Meshy-7 | 20 | **30** | 35（`ultra_mode` 有効時はさらに +5） |
| Smart Topology（Meshy T2） | 5 | 15 | 20 |
| その他 | 5 | 15 | — |

**採用プリセット `meshy-standard` の1件あたりのクレジット数：30 credits**

`model_type=standard`（＝ Meshy-7）、`should_texture=true`、`texture_resolution=4k`、
`ultra_mode` は無効。8Kではないので 35 にはならず、`ultra_mode` の +5 も発生しない。

Meshy-6 と Meshy-7 はどちらも「テクスチャあり 30 credits」で同額のため、
サーバー側の既定モデルが 6 と 7 のどちらであっても 30 credits で変わらない。

**確認できていないのは USD 単価である（U-3b は未解消）。**
この価格ページはクレジット数だけを示しており、
「購入は subscription settings ページから」とだけ書かれている。
1クレジットあたりの USD 単価は別のページで確認する必要がある。

仕様第11章のとおり、**Tripo と同じ単価（$0.01）を仮定しない**。
単価が確認できるまで、このプリセットの見積額は設定しない。

### 2.5 採用したプリセット

| 項目 | 値 |
| --- | --- |
| code | `meshy-standard` |
| model_id | `standard (meshy-7)`（Meshyは「モードがモデル」でモデル指定の項目が無い） |
| settings | `model_type=standard, should_texture=true, enable_pbr=true, texture_resolution=4k, target_formats=["glb"]` |
| 1件あたりのクレジット数 | **30 credits**（USD単価が未確認のため金額は未設定） |
| 有効 | **無効**（クレジットのUSD単価とデータ取扱い条件が未確認のため） |

`should_texture` の既定は false（テクスチャ無しの下書き）だが、
教室での品質検証にはテクスチャ付きが必要なため true にした。
**この設定は課金が増える側の選択**である（`docs/decisions.md` A-28）。

---

## 3. 未確認の項目（`UNVERIFIED`）

**これらが確認できるまで、両社とも実生成に選べない。**

| ID | 内容 | 確認先 | 影響 |
| --- | --- | --- | --- |
| ~~U-3a~~ | ~~Tripo：画像→3Dの1件あたりのクレジット数、オプションの加算、1クレジットのUSD単価~~ → **確認済み（2026-09-08）**：30 credits／$0.01 per credit ＝ **$0.30/件**。第1.4節を参照 | `https://developers.tripo3d.ai/en/pricing` | 解消 |
| U-12 | Tripo：価格表の「H Series / P Series / Splat Series」のうち、`model_version = v2.5-20250123` がどれに当たるか。確認した 30 credits は H Series タブの値 | 価格ページの他タブ、または各シリーズの対象モデル一覧 | 3シリーズで Image to 3D の価格が同じなら影響しない。異なる場合は見積額の見直しが要る |
| U-3b | Meshy：**クレジットのUSD単価**。クレジット数（30 credits/件）は確認済み（第2.4節）だが、価格ページには購入単価が載っていない | `https://www.meshy.ai/settings/subscription` の購入画面、または Meshy の料金ページ | 上限額を見積もれないため実行不可。**Tripoと同じ単価を仮定しない**（仕様第11章） |
| U-4a | 両社：送信した画像と生成物の保持期間 | 各社の規約 | 生徒作品を送る判断に必要 |
| U-4b | 両社：学習利用の可否と opt-out の手段 | 各社の規約 | 同上 |
| U-4c | 両社：生成物の利用条件（授業・販促での使用可否） | 各社の規約 | 教室採用の判断に必要 |
| U-7 | 両社：成果物を配信するホスト名 | 実物のURL、または公式資料 | `APP_TRIPO_DOWNLOAD_HOSTS` / `APP_MESHY_DOWNLOAD_HOSTS` が空のあいだはダウンロードしない |
| U-8 | 両社：レート制限の具体的な数値 | 公式資料 | 同時外部タスク上限（全体2・各社1）を実際の上限以下に保てているかを確認できない |
| U-9 | Meshy：`DELETE /image-to-3d/{id}` が実行中タスクの取消になるか、課金はどうなるか | `https://docs.meshy.ai/en/api/image-to-3d` | 取消を未対応のままにしている |
| U-10 | Tripo：`model_version` をより新しい版（`v3.1-20260211` 等）にすべきか。品質と価格の差 | 公式資料 | 既定の `v2.5-20250123` を採用中 |
| U-11 | Tripo：送信時の `{"type": "jpg"}` 固定申告が、PNG/WebP入力の結果に影響するか | 公式資料 | 影響があれば送信用コピーをJPEGに揃える必要がある |

### 確認できたら行うこと

1. この文書の該当行を「確認済み」に書き換え、確認日と根拠URLを入れる
2. `app/services/presets.py` の `price_max_micro_usd`・`price_version`・`price_checked_on`・
   `price_source_url` を埋め、`is_unverified=False`・`is_enabled=True` にする
3. `.env` の `APP_TRIPO_DOWNLOAD_HOSTS` / `APP_MESHY_DOWNLOAD_HOSTS` に配信ホストを設定する
4. セット上限額と全体上限額（`APP_GLOBAL_COST_CAP_USD`）を設定する
5. そのうえで A3.5（早期実感触、5題材×2社＝10件）に進む
