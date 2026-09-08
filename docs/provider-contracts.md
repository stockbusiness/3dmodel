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

**価格ページと公式APIドキュメントはこの環境からは読めていない。**
そのため、以下は**利用者が自分の環境で開いて内容を提示したもの**を根拠にしている。
出所を明記して区別する。

| 根拠 | 提示された内容 | 提示日 | 記録先 |
| --- | --- | --- | --- |
| `https://developers.tripo3d.ai/en/pricing`（画面） | クレジット単価、Image to 3D のクレジット数、加算オプション | 2026-09-08 | 第1.4節 |
| `https://docs.meshy.ai/api/pricing`（本文） | Image to 3D のクレジット数 | 2026-09-08 | 第2.4節 |
| `https://docs.meshy.ai/llms-full.txt`（公式のドキュメント一括配布） | 取消の意味と課金、レート制限、成果物の保持期間と配信ホスト、失敗時のクレジット、Taskの項目 | 2026-09-08 | 第2.2節・第2.6節 |
| `https://meshy.ai/settings/subscription`（画面・JPY表示） | プランの月額とクレジット数、生成物のライセンス | 2026-09-08 | 第2.4.1節・第2.4.2節 |
| `https://meshy.ai/settings/subscription`（画面・**USD表示**） | プランの月額（USD）、**追加クレジットパックの価格** | 2026-09-08 | 第2.4.1節・第2.4.3節 |

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
| 取消 | `DELETE /image-to-3d/{task_id}`（**実行中タスクの取消**。第2.2.1節） |
| 状態値 | `PENDING` / `IN_PROGRESS` / `SUCCEEDED` / `FAILED` / `CANCELED` |
| Taskの項目 | `id`, `type`, `status`, `progress`, `preceding_tasks`, `created_at`, `started_at`, `finished_at`, `expires_at`, `task_error.message`, `model_urls`（形式→URLの対応。GLBは `model_urls.glb`）, `texture_urls[]`, `thumbnail_url`, `image_urls[]` |
| エラーの分類 | 400/422 = 内容不正、401 = 認証、402 = 残高不足、404 = 見つからない、429 = レート制限、その他 = サーバー |
| エラーの詳細 | `task_error.{type, code, message, doc_url}` |
| 時刻の形式 | ミリ秒のエポック整数（`created_at` / `started_at` / `finished_at` / `expires_at`） |
| 消費クレジット | `consumed_credits`。**`FAILED` のタスクでは 0 になる**（失敗分は自動返却される） |
| ブラウザからの直接呼び出し | CORSで拒否される。サーバー経由でのみ呼ぶ（こちらの実装はサーバー経由） |

`POST /image-to-3d` の本文（公式CLIが送る項目そのまま）：

`image_url`（http(s) URL または `data:` URI）、`input_task_id`、
`model_type`（`standard` = meshy-7 / `smart-topology` = meshy-t2）、
`target_polycount`（smart-topology のみ。100〜15000、既定10000）、
`ultra_mode`（standard のみ。追加課金）、`should_texture`（**既定 false**。false だとテクスチャ無しの下書き）、
`enable_pbr`、`texture_prompt`（600文字まで）、`texture_resolution`（`2k` / `4k` / `8k`）、
`pose_mode`（`a-pose` / `t-pose`）、`image_enhancement`、`remove_lighting`、`target_formats`

#### 2.2.1 取消（U-9 解消・2026-09-08）

`DELETE /image-to-3d/{task_id}` は**実行中の依頼を取り消す操作**である（記録の削除ではない）。
課金の扱いは状態によって異なる。

| 取消時の状態 | 結果 | 作成時クレジット |
| --- | --- | --- |
| `PENDING` | 取消される | **返却される** |
| `IN_PROGRESS` | 取消される | **返却されない** |
| `SUCCEEDED` / `FAILED` / `CANCELED`（終了済み） | 取り消せない | — |

**実装：`MeshyAdapter.supports_cancel = True` にし、`cancel()` を実装した。**
ただし、こちらからは「取消の瞬間に PENDING だったか IN_PROGRESS だったか」を確実には
判定できないため、**取消しても送信枠は返却せず保持する**（仕様第8章の状態遷移表の
「送信後の取消は枠を保持」に従う。返却される場合はこちらが損をしない側に倒れる）。
終了済みで取り消せなかった場合は「未取消」として状態と費用の照合を続ける。

#### 2.2.2 レート制限（U-8 の Meshy 分・2026-09-08）

| プラン | 1秒あたりの要求数 | 同時に待たせられるタスク数 |
| --- | --- | --- |
| Pro | 20 | 10 |
| Premium | 20 | 30 |
| Ultra | 20 | 100 |
| Studio | 20 | 20 |
| Enterprise | 100 | 50以上 |

こちらの同時外部タスク上限は**全体2件・各社1件**なので、最も低い Pro でも十分に下回る。
状態確認の間隔（既定10秒）も 20 req/s に対して余裕がある。

### 2.3 実装で決めたこと

- **画像は `data:` URI で送る。** 公式CLIもローカルファイルを `data:` URI として送っている。
  こちらの画像を公開URLに置く必要がなく、仕様第12章に沿う。
  送信用コピー（EXIF方向を正規化し位置情報を除いたもの）を base64 にして載せる。
- **自動リトライは無い。** 公式CLIのクライアントは `fetch` を1回行うだけで、
  `AbortController` によるタイムアウトのみを持つ。こちらも1回だけ送る。
- **取消に対応する（2026-09-08 変更）。** `DELETE /image-to-3d/{id}` が実行中タスクの
  取消であることを公式資料で確認したため `supports_cancel = True` にした。
  返却の有無はこちらから判定できないため、**送信枠は返さず保持する**（第2.2.1節）。
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

#### 2.4.1 プラン価格（2026-09-08 確認・**USD建て**）

`https://meshy.ai/settings/subscription` の画面を利用者が提示した。
**通貨をUSDに切り替えた表示**で確認したので、為替換算は挟んでいない。

| プラン | 通常の月額（USD） | 月次クレジット | 同時タスク | 1クレジットあたり（USD・計算値） |
| --- | --- | --- | --- | --- |
| Free | $0 | 100 | 1 | — |
| Pro | $20 | 1,000 | 10 | $0.02 |
| Premium | $40 | 3,000 | 30 | $0.013333… |
| Ultra | $100 | 8,000 | 100 | $0.0125 |

同じ画面のJPY表示は Free ¥0／Pro ¥3,084／Premium ¥6,168／Ultra ¥15,420 だった（参考）。
**見積額にはUSD表示のほうを使う。**

- 画面には「**最初の月**」の割引価格（Pro $10／Premium $20／Ultra $50）が大きく出ているが、
  **恒久単価として使わない**。これは利用者のアカウントに付いている
  「New User 50% Off First Month」クーポンによるもので、画面にもその旨の案内がある。
  上の表は「Billed monthly, $N / month」と併記された通常価格である。
- 1クレジットあたりの金額は**こちらの計算値**であり、事業者が表示した数字ではない。
  実際の請求は月額固定で、クレジットの繰越可否は未確認。
- 税込か税別かは画面から判断できない（未確認）。
- ここに記録したのは Free / Pro / Premium / Ultra の4プラン（個人向け）。
  公式APIドキュメントのレート制限表にある Studio / Enterprise は別建てである。
- **「最大100アセット」等の表記をこちらの件数見積もりに使わない。**
  Pro の「1,000クレジット（最大100アセット）」は 1件10クレジット換算であり、
  採用プリセット `meshy-standard`（テクスチャ付き **30クレジット**）とは前提が違う。
  Pro の1,000クレジットで作れるのは **33件**である。

**同時タスク数について：** こちらの同時外部タスク上限は各社1件なので、
Free（同時1タスク）でも上限には当たらない。ただしキュー優先度は Free が最も低い。

#### 2.4.3 追加クレジットパック（2026-09-08 確認・U-13 解消）

定額プランとは別に、クレジットを単品で購入できる。**こちらのほうが単価は高い。**

| 支払額 | クレジット | 事業者表示の単価 | 1クレジットあたり |
| --- | --- | --- | --- |
| $10 | 250 | $4 / 100 credits | **$0.04** |
| $32（定価 $40、-20%） | 1,000 | $3.2 / 100 credits | $0.032 |
| $84（定価 $120、-30%） | 3,000 | $2.8 / 100 credits | $0.028 |

- -20% / -30% は数量による値引きで、期間限定の販促ではない（画面上、各パックの
  定価と並べて常時表示されている）。
- Pro 以上のプランには「追加クレジットパックが 20% OFF」という特典が付く。
  上の価格は Free アカウントで表示されたもの、つまり**割引の無い価格**である。
- 利用者のアカウントの「Discounts」欄は「No discount」だった。

#### 2.4.4 採用する見積単価（仕様第11章「見積は常に上限側を採る」）

購入経路によって1クレジットの単価が変わるため、**確認できた中で最も高い単価**を採る。

| 経路 | 1クレジット | 1件（30credits） |
| --- | --- | --- |
| **追加クレジットパック $10 / 250credits** | **$0.04** | **$1.20** ← 採用 |
| 追加クレジットパック $32 / 1,000credits | $0.032 | $0.96 |
| 追加クレジットパック $84 / 3,000credits | $0.028 | $0.84 |
| Pro 定額（$20 / 1,000credits） | $0.02 | $0.60 |
| Premium 定額（$40 / 3,000credits） | $0.013333… | $0.40 |
| Ultra 定額（$100 / 8,000credits） | $0.0125 | $0.375 |

`price_max_micro_usd` は名前のとおり**上限額**なので、最大値を入れるのが正しい。
実際にどの経路で支払っても、見積が実費を下回ることはない。
定額プランを契約すれば実費はこれより安くなり、そのぶん上限額判定は早めに止まる側に働く。

**`meshy-standard` の見積額：$1.20/件 ＝ 1,200,000 micro-USD**
（`price_version = meshy-2026-09-08-extra-pack`）

**`tripo-standard` の見積額：$0.30/件 ＝ 300,000 micro-USD**
（1credit = $0.01 × 30credits。`price_version = tripo-2026-09-08`）

#### 2.4.2 生成物のライセンス（U-4c の一部・2026-09-08）

プラン表の記載を確認した。**プランによって生成物の扱いが変わる。**

| プラン | 画面の記載 |
| --- | --- |
| Free | **CC BY 4.0 ライセンス（クレジット表記により商用利用可）** |
| Pro 以上 | **Private license for all assets**（すべてのアセットにプライベートライセンス） |

**教室採用に対する意味：Free プランで生成した成果物は CC BY 4.0 になる。**
これは「クレジット表記をすれば商用利用できる」一方で、
**成果物自体がその条件で第三者にも再利用を許すライセンスになる**ということである。
生徒の作品を素材にする以上、この点は運営が明示的に判断する必要がある。
Pro 以上は「Private license for all assets」と明記されている。
ただし**「プライベートライセンス」の条文そのものは未取得**なので、
授業・販促での具体的な使用可否は規約本文で確認する必要がある（U-4c は未解消）。

**あわせて確認できたこと：**

- **APIの利用はPro以上の特典として挙げられている。** Pro の「Advanced Tools &
  Integration」に `API & 3D platform plugins` と `MCP & Skill for AI Agent` があり、
  Free の欄には無い（Free は「Basic AI features」のみ）。
  **Free でAPIキーを発行できるかは画面から断定できない**（U-15）。
  本システムはAPI経由でしか動かないため、これはA3.5の前提条件になる。
- **Meshy Education Plan** がある。「現在学生または教職員であれば、
  教育プログラムに申し込んで割引を受けられる」と案内されている（`Apply Now`）。
  **教室での採用なら、まずこれを確認する価値がある**（U-16）。

### 2.5 採用したプリセット

| 項目 | 値 |
| --- | --- |
| code | `meshy-standard` |
| model_id | `standard (meshy-7)`（Meshyは「モードがモデル」でモデル指定の項目が無い） |
| settings | `model_type=standard, should_texture=true, enable_pbr=true, texture_resolution=4k, target_formats=["glb"]` |
| 1件あたりのクレジット数 | **30 credits** |
| 見積額（上限側） | **$1.20/件 ＝ 1,200,000 micro-USD**（第2.4.4節） |
| 価格の版 | `meshy-2026-09-08-extra-pack` |
| 有効 | **無効**（データ取扱い条件が未確認のため。価格は確認済み） |

`should_texture` の既定は false（テクスチャ無しの下書き）だが、
教室での品質検証にはテクスチャ付きが必要なため true にした。
**この設定は課金が増える側の選択**である（`docs/decisions.md` A-28）。

### 2.6 データの取扱い（2026-09-08 一部確認）

`https://docs.meshy.ai/llms-full.txt` の内容を利用者が提示した。

| 項目 | 確認できた内容 | 状態 |
| --- | --- | --- |
| 生成物の保持期間 | **APIで生成したモデルは最大3日間しか保持されない**（Enterprise以外）。Enterprise 契約では無期限保持が可能 | 確認済み |
| 成果物のURL | `https://assets.meshy.ai/...` の**署名付き・期限付きURL**。有効期限は Task の `expires_at`（= `finished_at` の3日後） | 確認済み |
| 送信した画像の保持期間 | 記載を確認できていない | **未確認（U-4a に残る）** |
| 学習利用の可否・opt-out | APIドキュメントには記載が無い。規約側の確認が要る | **未確認（U-4b）** |
| 生成物の利用条件 | 同上 | **未確認（U-4c）** |

**運用への影響：3日で消える。**
生成後3日を過ぎるとダウンロードできなくなるため、
検証で使う成果物は**受領後すみやかにこちら側へ保存する**必要がある。
現在の実装は成功を検知した巡回でそのままダウンロードして保存するので、
通常運転ではこの制限に当たらない。ただし
「保存だけ再試行」を使う場合は `expires_at` を過ぎていないことを確認する。

**配信ホスト（U-7 の Meshy 分）：`assets.meshy.ai`。**
`APP_MESHY_DOWNLOAD_HOSTS=assets.meshy.ai` を `.env.example` の記載例に入れた。
実際の設定は運用時に行う（既定は空のままで、空のあいだは何もダウンロードしない）。

---

## 3. 未確認の項目（`UNVERIFIED`）

**これらが確認できるまで、両社とも実生成に選べない。**

| ID | 内容 | 確認先 | 影響 |
| --- | --- | --- | --- |
| ~~U-3a~~ | ~~Tripo：画像→3Dの1件あたりのクレジット数、オプションの加算、1クレジットのUSD単価~~ → **確認済み（2026-09-08）**：30 credits／$0.01 per credit ＝ **$0.30/件**。第1.4節を参照 | `https://developers.tripo3d.ai/en/pricing` | 解消 |
| U-12 | Tripo：価格表の「H Series / P Series / Splat Series」のうち、`model_version = v2.5-20250123` がどれに当たるか。確認した 30 credits は H Series タブの値 | 価格ページの他タブ、または各シリーズの対象モデル一覧 | 3シリーズで Image to 3D の価格が同じなら影響しない。異なる場合は見積額の見直しが要る |
| ~~U-3b~~ | ~~Meshy：クレジットのUSD単価~~ → **確認済み（2026-09-08）**：USD表示の購入画面で確認。上限側を採り **$0.04/credit × 30credits ＝ $1.20/件**。第2.4.1節・第2.4.3節・第2.4.4節を参照 | `https://meshy.ai/settings/subscription`（USD表示） | 解消 |
| ~~U-13~~ | ~~Meshy：追加クレジットパックの価格~~ → **確認済み（2026-09-08）**：$10/250、$32/1,000、$84/3,000。第2.4.3節を参照 | 同上 | 解消 |
| U-14 | Meshy：月次クレジットの繰越可否、および月額表示が税込か税別か | 購入画面・請求書 | 予算計画に影響（見積額の算定には影響しない） |
| U-15 | Meshy：**Free プランでAPIキーを発行できるか**。API利用は Pro 以上の特典として挙げられており、Free の欄には無い | 設定の「API」タブでキーを発行できるか | **本システムはAPI経由でしか動かない**。発行できなければ、A3.5の前に Pro（$20/月）の契約が要る |
| U-16 | Meshy：**Meshy Education Plan** の割引内容と対象条件 | 購入画面の `Apply Now` | 教室での採用なら費用が変わる可能性がある |
| U-4a | 両社：送信した画像と生成物の保持期間。**Meshy の生成物は確認済み（最大3日・第2.6節）**。残るのは「Meshy へ送信した画像の保持期間」と「Tripo の画像・生成物の保持期間」 | 各社の規約 | 生徒作品を送る判断に必要 |
| U-4b | 両社：学習利用の可否と opt-out の手段 | 各社の規約 | 同上 |
| U-4c | 両社：生成物の利用条件（授業・販促での使用可否）。**Meshy は Free = CC BY 4.0（クレジット表記で商用利用可）、Pro以上 = プライベートライセンスと画面で確認（第2.4.2節）**。ただし「プライベートライセンス」の全文と Tripo 側は未取得 | 各社の規約 | 教室採用の判断に必要。**Free で生成すると成果物が CC BY 4.0 になる点は運営判断が要る** |
| U-7 | 両社：成果物を配信するホスト名。**Meshy は確認済み：`assets.meshy.ai`（第2.6節）**。Tripo は未確認 | 実物のURL、または公式資料 | `APP_TRIPO_DOWNLOAD_HOSTS` が空のあいだは Tripo の成果物をダウンロードしない |
| U-8 | 両社：レート制限の具体的な数値。**Meshy は確認済み（第2.2.2節。最も低いプランでも 20 req/s・同時10件）**。Tripo は未確認 | 公式資料 | Meshy 側は上限を十分下回ることを確認済み。Tripo 側は未確認 |
| ~~U-9~~ | ~~Meshy：`DELETE /image-to-3d/{id}` が実行中タスクの取消になるか、課金はどうなるか~~ → **確認済み（2026-09-08）**：実行中タスクの取消。`PENDING` は返却、`IN_PROGRESS` は返却なし、終了済みは取消不可。第2.2.1節を参照 | `https://docs.meshy.ai/llms-full.txt` | 解消（`supports_cancel = True` にした。枠は保持する） |
| U-10 | Tripo：`model_version` をより新しい版（`v3.1-20260211` 等）にすべきか。品質と価格の差 | 公式資料 | 既定の `v2.5-20250123` を採用中 |
| U-11 | Tripo：送信時の `{"type": "jpg"}` 固定申告が、PNG/WebP入力の結果に影響するか | 公式資料 | 影響があれば送信用コピーをJPEGに揃える必要がある |

### 確認できたら行うこと

1. この文書の該当行を「確認済み」に書き換え、確認日と根拠URLを入れる
2. ~~`app/services/presets.py` の `price_max_micro_usd`・`price_version`・`price_checked_on`・
   `price_source_url` を埋め、`is_unverified=False` にする~~ → **完了（2026-09-08）**。
   `is_enabled=True` にするのは**データ取扱い条件（U-4a/U-4b/U-4c）が確認できてから**
3. `.env` の `APP_TRIPO_DOWNLOAD_HOSTS` / `APP_MESHY_DOWNLOAD_HOSTS` に配信ホストを設定する
   （Meshy は `assets.meshy.ai`。Tripo は実物のURLで確かめる）
4. セット上限額と全体上限額（`APP_GLOBAL_COST_CAP_USD`）を設定する
5. そのうえで A3.5（早期実感触、5題材×2社＝10件）に進む
