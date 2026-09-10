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
| Tripo AI「Terms of User Agreement」（Last updated: July 11, 2025）本文 | 無料／有料での権利の違い、学習利用、生成物の利用条件、保持期間、公開範囲の既定、年齢制約 | 2026-09-08 | 第1.6節 |
| Meshy「Terms of Service」（Last Updated: March 7, 2026）本文 | 学習利用、生成物の権利、保持期間、送信できない情報、クレジットの繰越、税 | 2026-09-08 | 第2.7節 |
| `https://meshy.ai/settings/api`（画面） | 無料プランでAPIキーを発行できないこと | 2026-09-09 | 第2.8節 |
| `https://developers.tripo3d.ai/ja/keys`（画面） | 無料アカウントでもAPIキーを発行できること、キーは1回だけ表示されること、IP制限があること | 2026-09-09 | 第1.7節 |

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

**単価は実取引で確認済み（2026-09-09）：$5 の購入で 500credits が付与された
（＝$0.01/credit）。** 第1.9.1節を参照。価格ページの記載と一致した。

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

### 1.6 データの取扱いと権利（2026-09-08 確認）

**根拠：Tripo AI「Terms of User Agreement」（Last updated: July 11, 2025）。
運営会社は Holymolly Ltd（香港）。準拠法は香港法、紛争解決は HKIAC 仲裁。**
利用者が本文を提示した（この環境からは `www.tripo3d.ai` へ接続できない）。

#### 1.6.1 最重要：無料利用と有料利用で権利が正反対である

**第5.2.1条（Free Users）** — 無料で使う利用者について：

> Tripo retains all rights, including without limitation the rights to use, copy,
> reproduce, modify, adapt, publish, translate, create derivative works from,
> distribute, promote, transfer, authorize, license, optimize, derive revenue or
> other remuneration from, communicate to the public, perform, and display the
> **Inputs and Outputs** submitted or generated by Free Users, as well as all
> Intellectual Property rights arising therefrom.

`Inputs` は「利用者が入力・アップロードした一切の素材」と第3.1条で定義されている。
つまり**無料利用では、送信した画像そのものと生成物の両方について、
Tripo が利用・改変・公開・再許諾・収益化の権利と、そこから生じる知的財産権を保持する。**

**第5.2.2条（Paid Users）** — 有料の利用者について：

> Paid Users generally have all rights （中略） of the Inputs and Outputs by the paid
> Users and the Intellectual Property Rights based on the Inputs and Outputs by the
> paid Users. **For the avoidance of doubt, Company will not use Inputs and Outputs
> as training data to train, validate, test, or improve any AI Technology.**

ただし有料でも、役務提供に必要な範囲で
「royalty-free, perpetual, irrevocable, worldwide, non-exclusive」な
利用・表示の許諾を会社に与えることが前提になっている。

**運用上の結論：生徒の作品を Tripo に送るなら、有料の利用者であることが前提になる。**
無料のまま送ると、作品の権利を会社が保持する条項が適用される。
これは価格の問題ではなく、権利の問題である。

#### 1.6.2 学習利用（U-4b の Tripo 分）

| 区分 | 学習利用 | opt-out |
| --- | --- | --- |
| 有料 | **使わないと明記**（第5.2.2条） | 既定で使わないため不要 |
| 無料 | 明文の禁止が無い。第5.2.1条が `optimize` を含む広範な権利を会社に留保しているため、**学習利用を妨げる条項が無い** | 記載なし |

#### 1.6.3 生成物の利用条件（U-4c の Tripo 分）

**第3.2条**より：

> you may use Outputs for lawful commercial or non-commercial purposes, so long as
> such Outputs are not distributed or made available to third parties in a manner
> inconsistent with this Agreement or applicable laws

- **商用・非商用いずれの利用も可**（有料利用者が権利を持つ前提。授業・販促での使用は可）。
- 禁止されるのは、**Outputs を使って Holymolly と直接競合するモデル・サービスを作ること**。
- Outputs の独占性は保証されない。他の利用者に類似・同一の Outputs が出る可能性があると明記。

#### 1.6.4 保持期間（U-4a の Tripo 分）

**期間の定めが無い。** 第4条：

> Unless expressly agreed to by Tripo in writing elsewhere, Tripo has no obligation to
> store any of Inputs and Outputs that you make available on Tripo Properties.

つまり**保管義務が無く、消える可能性がある**（＝こちらで速やかに保存する必要がある。
現在の実装は成功検知と同時にダウンロードして保存するので、通常運転では問題にならない）。
一方で第10.5条は、**「公開」領域に出したものは永久に保持されうる**と定めている。

> any of Your Inputs and Outputs that you previously make available in any "public"
> areas of the Service may be retained in perpetuity.

#### 1.6.5 公開範囲の既定（新規：U-17）

第4条に、見過ごせない既定値の規定がある。

> Certain Services may enable you to specify the level at which such Services restrict
> access to Your Inputs and Outputs. You are solely responsible for applying the
> appropriate level of access to Your Inputs and Outputs. **If you do not choose, the
> system may default to its most permissive setting.**

**選ばなければ最も公開度の高い設定になりうる。**
API 経由で作成した資産の既定の公開範囲を確認できていない（U-17）。
生徒の作品を扱う以上、A3.5 の前に実物で確認する必要がある。

#### 1.6.6 年齢に関する制約（運用条件）

第3.2条は、**13歳未満（または各国の同意年齢未満）の個人データを、
保護者・後見人の適切な同意なく送信すること**を禁じている。
第1条も利用者自身が13歳以上であることを求めている。
**教室の運用では、この同意の取得が前提条件になる。**
本システムの利用同意（`consent_status`）はこの要件に対応する仕組みだが、
「何歳の生徒か」を本システムは持っていない（U-18）。

#### 1.6.7 そのほか記録しておく点

- **APIキーの共有は禁止**（第2.1(b)条）。第三者への売買・譲渡・貸与も禁止（第3.2条）。
  本システムは環境変数で保持し、ログにも画面にも出さないので要件を満たす。
- 料金は**すべてUSD建て・返金不可**（第6.3条）。支払遅延には**1日あたり5%**の遅延損害金。
- 決済は Stripe。
- 会社の賠償責任の上限は「直近12か月の支払額」「$500」「法定の救済額」のうち大きい額
  （第4条）。

---

### 1.7 APIキーの発行（2026-09-09 確認）

`https://developers.tripo3d.ai/ja/keys` を利用者が開いた画面で確認した。

- **無料アカウントでも「新しい秘密鍵を作成する」が使える。**
  有料プランへの案内で覆われてはいなかった。
- **キーは作成時に1回だけ表示される。**
  画面の記載：「APIキーは、作成時に1回だけ表示されます。」
  控え損ねたら作り直しになるので、作成したらすぐ `.env` に書く。
- **IP Restriction（IP制限）がある。** キーを使えるIPを限定できる。
  限定公開のVPSから使う運用なら、**そのVPSのIPに絞っておくのが安全**である
  （`docs/decisions.md` S-7）。
- 事業者自身が「環境変数でAPIキーを保存し、バージョン管理にコミットしないこと」を
  ベストプラクティスとして案内している。**本システムの方針（キーは環境変数のみ・
  画面から保存しない。S-6）と一致する。**

**まだ確認できていないこと：この画面にクレジット残高の表示が無い。**
無料アカウントにクレジットが付与されるかは、キーを設定したうえで
管理画面の接続テスト（`get_balance()`）を実行すれば分かる（U-23 の残り）。

---

### 1.8 実際に接続して分かったこと（2026-09-09）

利用者の環境（Windows 11 + Docker Desktop）で
`app.cli check-provider tripo --connect`（公式SDKの `get_balance()`）を実行した。
**生成は行っていないので課金は発生していない。**

| 項目 | 結果 |
| --- | --- |
| APIキーの形式 | `tsk_` で始まる。SDKの検査を通った |
| 認証 | **通った**（`GET /user/balance` が成功） |
| クレジット残高 | **0**（凍結分 0） |

**つまり、無料アカウントでは認証はできても生成はできない。**
採用プリセットは1件30credits なので、A3.5（5件）には 150credits が要る。

**これで「無料のままでは A3.5 をどちらの事業者でも実施できない」ことが確定した。**

- Tripo：キーは発行できるが**残高0**（本節）
- Meshy：**キーそのものを発行できない**（第2.8節）

判断は `docs/decisions.md` T-14 に記録した。

なお、この確認と同時に次も動作を確認できた。

- `docker compose build`（E-1 解消）
- `docker compose run` によるコマンド実行
- Alembic の移行4本が新規DBに順に適用されること
  （`8ea2e151cb68` → `ed4948291cb6` → `ddcc479bce83` → `7695b59925d0`）
- プリセット13件の投入。価格は投入時点で入るため
  `apply_confirmed_prices` の更新件数は0（想定どおり）

---

### 1.9 クレジットの購入（2026-09-09 確認・U-25 解消）

`https://developers.tripo3d.ai/ja/billing` の「クレジットを追加する」を
利用者が開いた画面で確認した。

| 項目 | 内容 |
| --- | --- |
| 課金方式 | 従量課金制のクレジットモデル（残高を補充して使う） |
| **最小購入額** | **$1** |
| 購入単位 | **1〜100000 の整数 USD**（任意額を入力できる） |
| 既定の選択肢 | $50 / $100 / $250 の3つが「クイック金額」として並ぶ |
| 決済 | Stripe Checkout |
| 反映まで | **支払い後、残高の同期に最大5分程度かかる場合がある**（画面の注記） |
| 確認時の残高 | 0.00。取引履歴は空（0件） |

**A3.5 に必要な額：$2**

- 採用プリセットは1件30credits、5件で150credits
- 1credit = $0.01 なので150credits＝$1.50相当。しかし**整数USDしか指定できない**
- **$1 では100credits＝3件しか作れない。$2（200credits）が必要**
- $2 なら5件（150credits）に加えて50credits（1件強）の余裕が残る

**注意：既定で $50 が選ばれている。**
画面下の大きなボタンは「$50.00 を支払う」と表示されている。
そのまま押すと **$50 の支払いになる**。
**「カスタム金額」に `2` と入力してから**ボタンの表示が
「$2.00 を支払う」に変わったことを確かめて押すこと。

#### 1.9.1 実取引で確定した単価（2026-09-09）

利用者が実際に購入した。**これで単価が価格ページの記載ではなく実績で確定した。**

| 項目 | 内容 |
| --- | --- |
| 支払額 | **$5** |
| 付与されたクレジット | **500.00** |
| **単価** | **$0.01 / credit**（$5 ÷ 500） |
| 取引 | 2026-09-09 12:26、Payment、成功。凍結分 0 |

**価格ページの記載（1credit = $0.01）と一致した。**
したがって `tripo-standard` の見積 **$0.30/件（300,000 micro-USD）はそのまま有効**である。
第1.4節の根拠を「価格ページの記載」から「**実取引で確認**」に格上げする。

**A3.5 に使える件数：** 500credits ÷ 30credits = **16件**。
A3.5 は5件（150credits）なので、作り直しを含めても十分な余裕がある。
残りは 350credits（約11件）。

**なお、購入額は取引履歴の表に出ない。** 表の列は
「クレジット」「クレジットが追加されました」で、USD額の列が無い。
支払額は Stripe の領収書で確かめる必要がある。

### 1.10 多視点入力（`multiview_to_model`）— SDKには在るが未採用（2026-09-09）

利用者から「正面と背面を1枚に並べた画像が多い」との申告があった
（`docs/decisions.md` T-15）。この入力に対応するAPIは公式SDKに実在する。

**SDKのソースで確認できたこと（`tripo3d` 0.4.2）：**

```
multiview_to_model(
    images: List[str],
    model_version: Literal["P1-20260311", "v3.1-20260211", "v3.0-20250812",
                           "v2.5-20250123", "v2.0-20240919"] = "v2.5-20250123",
    ...
) -> str
```

| 確認できたこと | 内容 |
| --- | --- |
| タスク種別 | `type: "multiview_to_model"`、`files` に画像トークンの配列を渡す |
| 画像の渡し方 | **位置が意味を持つ配列**。`None` を混ぜられる（要素は空の辞書として送られる） |
| 画像1枚ずつの扱い | `image_to_model` と同じ経路でアップロードされる（第1.3節の拡張子の件がそのまま当てはまる） |
| オプション | `model_version` 以下は `image_to_model` と同じ顔ぶれ |

**確認できていないこと（実装しない理由）：**

- **1件あたりの価格（U-26）。** 価格ページ（第1.4節）で確認したのは
  Image to 3D の 30credits であり、多視点がこれと同じか、枚数で増えるかは分からない。
- **各位置がどの向きに対応するか**（front / left / back / right の順序）。
  SDKのソースには順序の説明が無く、docstring も "List of images" としか書いていない。

CLAUDE.md 第2章により、**価格と引数の意味が確認できないものは実装しない**。
`docs/decisions.md` T-15 のとおり、A3.5 では**正面1枚に切り出して**送る。

---

### 1.11 メタバース用途に関わるSDK機能（2026-09-09・**関数の存在のみ確認。価格は未確認**）

利用者が切り口を「作った3Dがメタバースで使える」に変えた（`docs/decisions.md` T-17）。
これに関わる関数が公式SDK 0.4.2 に**実在することをソースで確認した**。

**確認できたのは「関数が在ること」と「引数の並び」だけである。**
**1件あたりの価格・所要時間・成功率はいずれも未確認で、実装していない。**

| 関数 | 用途 | 出力形式 |
| --- | --- | --- |
| `convert_model` | 形式変換と配置向けの整形 | GLTF / USDZ / FBX / OBJ / STL / 3MF |
| `rig_model` | リグ付け | glb / fbx |
| `check_riggable` | リグ付け可否の事前判定 | — |
| `smart_lowpoly` | ポリゴン削減 | — |
| `refine_model` | 精細化 | — |
| `texture_model` | 再テクスチャ | — |
| `stylize_model` | 様式変換 | — |
| `mesh_segmentation` | メッシュ分割 | — |
| `mesh_completion` | 穴埋め | — |

#### 1.11.1 `convert_model` の引数（配置物にするための機能が揃っている）

| 引数 | 既定 | 意味 |
| --- | --- | --- |
| `format` | （必須） | `GLTF` / `USDZ` / `FBX` / `OBJ` / `STL` / `3MF` |
| `face_limit` | `None` | 三角形数の上限 |
| `texture_size` | **`4096`** | テクスチャ解像度。**多くのプラットフォームには大きすぎる既定値** |
| `texture_format` | `JPEG` | BMP / DPX / HDR / JPEG / OPEN_EXR / PNG / TARGA / TIFF / WEBP |
| `scale_factor` | `1.0` | スケール |
| **`pivot_to_center_bottom`** | `False` | **原点を底面中央に置く**（配置物に必須） |
| **`flatten_bottom`** | `False` | **底面を平らにする**（地面に接地させる） |
| `flatten_bottom_threshold` | `0.01` | 平坦化のしきい値 |
| `export_orientation` | `+x` | 書き出し時の向き（`+x` / `+y` / `-x` / `-y`） |
| `fbx_preset` | `blender` | `blender` / `mixamo` / `3dsmax` |
| `with_animation` | `True` | アニメーションを同梱するか |
| `animate_in_place` | `False` | 原地でのアニメーション |
| `quad` / `pack_uv` / `bake` / `force_symmetry` / `export_vertex_colors` / `part_names` | — | — |

#### 1.11.2 `rig_model` の引数と、判明した制約

| 引数 | 値 |
| --- | --- |
| `model_version` | `v1.0-20240301`（既定） / `v2.0-20250506` |
| `out_format` | `glb`（既定） / `fbx` |
| `rig_type` | `biped`（既定） / `quadruped` / `hexapod` / `octopod` / `avian` / `serpentine` / `aquatic` / `others` |
| `spec` | **`tripo`（既定） / `mixamo`** |

**制約：`spec` に VRM が無い。** リグの仕様は `tripo` と `mixamo` の2種類だけで、
**cluster・VRChat 系のアバター規格（VRM）へ直接書き出す手段が無い。**
Unity・Unreal・Blender で使うぶんには `mixamo` 仕様と FBX で足りるが、
**VRM を要求するプラットフォームへ出すには別途変換が必要になる。**

したがって**アバターより「置物・オブジェクト」のほうが現実的**である
（`docs/decisions.md` T-17）。

---

### 1.12 画像生成に関わるSDK機能（2026-09-09・**関数の存在のみ確認。価格は未確認**）

利用者から「画像生成を本システムに取り込む」案が出た（`docs/decisions.md` T-21）。
関連する関数が公式SDK 0.4.2 に**実在することをソースで確認した**。

| 関数 | 引数 | 備考 |
| --- | --- | --- |
| `text_to_image` | `prompt`, `negative_prompt` | **`negative_prompt` があるため「背景・影・見切れ」を除外する固定文が書ける** |
| `generate_image` | `prompt`, `model_version`, `file`, `files`, `template`, **`t_pose`**, **`sketch_to_render`** | **`t_pose` と `sketch_to_render` の挙動は型（bool）しか分からない**（U-34） |
| `text_to_model` | `prompt`, `negative_prompt`, `image_seed`, ほか `image_to_model` と同じ3D引数 | **テキストから直接3D。** `image_seed` を持つことから内部で画像を作っている構造と**推測される（未確認）** |
| `generate_multiview_image` | `image` | **1枚から多視点画像を作る** |

**確認できたのは「関数が在ること」と「引数の並び」だけである。**
**価格・品質・`template` の取りうる値・`t_pose` と `sketch_to_render` の実際の挙動は
いずれも未確認で、実装していない**（U-33〜U-36）。

---

### 1.13 成果物の配信ホストと待ち時間（2026-09-10・**実測**）

A3.5 の1件目（壺）を実際に生成して確認した。

#### 1.13.1 配信ホスト（U-7 解消）

```
tripo-data.rg1.data.tripo3d.com
```

`docs/early-check-plan.md` 第3.3節の手順どおり、**許可ホスト未設定のまま1件生成し、
拒否メッセージからホスト名を得た**（`docs/decisions.md` A-45）。

**注意：`rg1` は地域識別子と見られる。** 別の地域に割り当てられた場合、
`rg2` 等の別ホストになる可能性がある。**これは推測であり確認していない。**

`app/services/download_guard.py` の `_host_allowed` は
**ドメイン境界での後方一致**（`host == entry or host.endswith("." + entry)`）なので、
必要になれば `data.tripo3d.com` と指定して地域をまたいで許可できる。
**ただし、まず確認できた値をそのまま設定する**（CLAUDE.md 第2章）。
別ホストが出たら拒否メッセージにその名前が出るので、そのとき足せばよい
（**保存だけ再試行は追加課金なし**）。

#### 1.13.2 待ち時間（1件のみの実測）

worker のログから読み取れた時刻。

| 時刻 | 出来事 |
| --- | --- |
| 02:10:55 | 送信（`submitted: 1`） |
| 02:11:05 | 状態確認。まだ未完成 |
| 02:11:30 | 状態確認。まだ未完成 |
| 02:12:21 | **成果物の取得を試行**（＝この時点で完成していた） |

**したがって、生成の完了は送信から 36秒〜86秒の間である。**
**これは1件のみの実測であり、平均でも上限でもない。** 題材や混雑で変わる。

`v2.5-20250123` / `texture_quality=standard` / `geometry_quality=standard` の場合。

---

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

### 2.7 データの取扱いと権利（2026-09-08 確認）

**根拠：Meshy「Terms of Service」（Last Updated: March 7, 2026）。
運営会社は Meshy LLC（カリフォルニア州サニーベール）。準拠法はカリフォルニア州法
（ただし日本法の強行規定と衝突する範囲では日本法が優先すると明記）。
紛争解決は AAA/ICDR 仲裁、サンフランシスコ郡。**
利用者が本文を提示した（この環境からは `www.meshy.ai` へ接続できない）。

#### 2.7.1 最重要：Enterprise 以外は学習利用の対象になる

**第2.9条（Training on User Content）**：

> Meshy may use Customer Inputs and Customer Outputs from **non Enterprise Customers**,
> (collectively, "User Content"), to train, validate, test, or improve Services
> **unless otherwise agreed to in the Order**.

**Free だけでなく Pro / Premium / Ultra も「non Enterprise」に含まれる。**
つまり**有料プランを契約しても、送信した画像と生成物が学習に使われうる。**
これを外す手段として書かれているのは「Order（個別契約）で別途合意すること」だけで、
画面上の opt-out スイッチのような仕組みは規約に記載がない。

**Tripo との違いがここで決定的になる。**

| | 学習利用 | opt-out |
| --- | --- | --- |
| **Tripo（有料）** | **使わないと明記**（第5.2.2条） | 既定で使わない |
| Tripo（無料） | 妨げる条項が無い。権利自体を会社が保持 | なし |
| **Meshy（Enterprise 以外＝Free/Pro/Premium/Ultra）** | **使われうると明記**（第2.9条） | **Order（個別契約）のみ** |
| Meshy（Enterprise） | 対象外 | 既定で対象外 |

#### 2.7.1.1 「学習利用」の文言を両社で突き合わせる（重要）

**両社とも「学習利用（training use）」という語を定義していない。**
定義条項が無く、条文の動詞と目的語がそのまま範囲を決めている。
使われている動詞は両社とも同じ4つ（`train` / `validate` / `test` / `improve`）だが、
**目的語と限定句が違う。**

| | Meshy 第2.9条 | Tripo 第5.2.2条 |
| --- | --- | --- |
| 対象 | Customer Inputs と Customer Outputs | Inputs と Outputs |
| 動詞 | train, validate, test, or improve | train, validate, test, or improve |
| **目的語** | **`Services`**（役務そのもの） | **`any AI Technology`**（AI技術） |
| **限定句** | なし | **`as training data`（学習データとして）** |
| 向き | 「使うことがある」（許諾） | 「使わない」（不使用の約束） |

原文：

> **Meshy 第2.9条**：Meshy may use Customer Inputs and Customer Outputs from non
> Enterprise Customers … to train, validate, test, or improve **Services** unless
> otherwise agreed to in the Order.

> **Tripo 第5.2.2条**：Company will not use Inputs and Outputs **as training data** to
> train, validate, test, or improve **any AI Technology**.

**この差から読み取れること：**

1. **Meshy の許諾のほうが広い。** 目的語が `Services`（規約前文で
   「AIプラットフォーム、API、ギャラリー、プラグインを含む」と定義されている）であり、
   `as training data` のような限定が無い。**AIモデルの学習に限らず、
   役務の検証・試験・改善のために Input / Output を使うことが許されている**と読める。
   人手による内容の確認や品質評価も `test` / `improve` に含まれうる。
2. **Tripo の不使用の約束は狭いが明確。** 「**学習データとして** AI技術を
   train / validate / test / improve すること」に使わない、と限定されている。
   裏を返せば、**学習データ以外の用途での利用までは否定していない。**
   実際 Tripo は同じ第5.2.2条で、有料利用者から
   「役務提供に必要な範囲での利用・表示」の永続的・取消不能な許諾を得ている。
   第3.1条では Inputs の監視・確認を行いうるとも定めている。

#### 2.7.1.2 Meshy の第2.8条は第2.9条とは別の話

第2.8条（Aggregated Statistics）は、利用状況のデータを集計・匿名化して
「including to **improve our models**」に使うと定めているが、

> Aggregated Statistics **does not include Customer Input or Customer Output**.

と明記されており、**送信した画像や生成物そのものは含まれない。**
第2.9条（Input / Output そのものを使う話）とは別の条項として読む必要がある。

#### 2.7.2 第2.9条と第3.2条の食い違い（U-20）

第3.2条には、有料利用者について次の記載がある。

> Customers on a paid Meshy plan have the option to keep their User Content private and
> your User Content will not be used for any purpose **other than as outlined here**.

「as outlined here」が指す範囲に第2.9条（学習利用）が含まれるなら、
有料でも学習利用の対象のままである。含まないなら、有料は学習利用から外れる。
**この2条の関係を規約本文だけでは確定できない。**
生徒作品を送るかどうかの判断に直結するため、
**Meshy に直接照会して書面で回答を得る必要がある**（U-20）。
それまでは、より安全側の読み方（＝第2.9条どおり Enterprise 以外は学習利用の対象）を採る。

#### 2.7.3 送信する画像に個人を特定できる情報を含められない（重要な運用条件）

**第2.2条（Customer Input）**：

> You agree that you will **not include any personally identifiable information** about
> yourself or any third party in your Customer Input, including but not limited to
> financial information, Social Security numbers, physical address or **any other data
> that could identify a specific person**.

第2.6条(x) も「sensitive personal data」の送信を禁じている。
**生徒の作品画像に、顔・氏名・学校名などが写り込んでいる場合、規約上そもそも送信できない。**
題材の選定と送信前の確認が運用の前提になる（T-5）。

#### 2.7.4 年齢（第1.1条）

> You must be at least **14 years old** to use the Service. （中略）
> If you are under 18, you represent that you have your parent or guardian's permission.

Meshy は **14歳以上**（Tripo は13歳以上）。サービスを操作するのは運営・講師であり
生徒ではないが、年齢要件が両社で異なることは記録しておく。

#### 2.7.5 生成物の権利（U-4c の Meshy 分）

**第3.2条**：

- **無料プラン**：

  > Provider owns all right, title, and interest, including all intellectual property
  > rights, in and to the AI Customer Output

  **生成物の権利は Meshy が保有し**、利用者には CC BY 4.0 の利用許諾が与えられる
  （帰属表示をすれば商用利用も可）。
- **有料プラン**：利用者は User Content を非公開に保つ選択ができ、
  Meshy に対して役務提供に必要な範囲の非独占・無償・世界的な利用許諾を与える。
  なお**有料利用者が生成物を「所有する」とは明記されていない**（Tripo の第5.2.2条とは書き方が違う）。

**第3.3条（Community License）**：Meshy の Community ページに公開した生成物は
**CC0（パブリックドメイン提供）**になる。**生徒作品を Community に出してはならない。**

#### 2.7.6 保持期間（U-4a の Meshy 分）

**第2.5条**が、公式ドキュメントの記載を規約として裏づけている。

> Customer Output generated by Customers using the APIs, other than Enterprise
> Customers, will be **deleted three (3) days after it is generated**.

- Enterprise は既定で無期限保持、1〜30日の保持期間を設定することも可能。
- Webapp 利用分には保存容量の上限があり、超過分や休眠アカウントのものは削除されうる。
- **送信した画像（Customer Input）の保持期間は規約に記載がない**（U-4a に残る。
  プライバシーポリシー側の確認が要る）。

#### 2.7.7 クレジットの繰越と税（U-14 解消）

**第2.10条**：

- **月次クレジットは翌月に繰り越されない。**「once expired, unused Features have no
  value and cannot be reinstated」
- **追加購入したクレジット（Additional Credits）は購入日から1年間有効。**

**第4条**：料金は USD 建て、**税別**（"Fees are exclusive of taxes"）。
延滞利息は月1.5%。返金不可。

#### 2.7.8 そのほか記録しておく点

- **1つのアカウントを複数人で使うことは禁止**（第2.6条(vi)）。ただし第1.2条の
  「Authorized Users」（利用者の従業員・業務委託先・代理人）は認められている。
  教室の運営・講師はこれに当たると読める。
- 生成物を使って **Meshy と競合するAIモデルを学習・開発・改善すること**は禁止（第2.6条(xi)）。
- 第2.8条の Aggregated Statistics は「to improve our models」を含むが、
  **Customer Input / Customer Output は含まないと明記**されている（第2.9条とは別の話）。
- 賠償責任の上限は「直近12か月に実際に支払った額」（第9条）。
- 規約の変更は7日前（利用者に不利益・重要な変更は30日前）に通知される（前文）。

### 2.8 APIキーの発行（2026-09-09 確認・U-15 解消）

`https://meshy.ai/settings/api` を利用者が開いた画面で確認した。

**無料プランでは APIキーを発行できない。**

画面は「API」タブの内容が伏せられ、有料プランへの案内が重ねて表示されていた。

> Unlock Powerful APIs for Your Workflow
> - Generate 3D assets programmatically using secure, production-ready API keys.
> - Automate workflows with webhooks and real-time callbacks.
> - Test, debug, and iterate faster using the interactive API Playground.
> - Keep track of your credit usage and enable automatic refills once it's depleted.
>
> `Upgrade Now` / `Try the Playground` / `Learn More`

キーの一覧も発行操作も使えない状態だった。
これは購入画面のプラン表（第2.4.2節）で API 利用が Pro 以上の特典として
挙げられていたことと一致する。

**この結論の重さ：**

本システムは**API経由でしか動かない**（画面の自動操作は行わない）。
したがって **Meshy を A3.5 に参加させるには Pro（$20/月）以上の契約が要る。**
当面は無料で進めるという判断（`docs/decisions.md` T-11）と正面から衝突する。

**あわせて無効になった案：** 無料枠の不足（月100クレジット＝3件）を
**$10 の追加クレジットパックで補う案は成立しない。**
クレジットを買っても API が使えるようにはならないためである。

---

## 3. 未確認の項目（`UNVERIFIED`）

**これらが確認できるまで、両社とも実生成に選べない。**

**学習利用については 2026-09-08 に運営が判断済み**
（対象から外す必要はない。`docs/decisions.md` T-9）。
U-4b・U-20・U-22 は解消または関門から外れた。

**A3.5 は無料アカウントで実施する**と決まった（`docs/decisions.md` T-11）。
これにより U-15 の保留が解け、U-23・U-24 が新たに関門になった。
**Meshy の無料枠は月100クレジット（＝採用プリセットで3件）であり、
A3.5 の5題材には足りない**（第2.4.1節・規約第2.10条）。

**この環境からは事業者のサイトへ接続できない**（egress proxy が CONNECT に 403 を返す。
`www.meshy.ai` / `www.tripo3d.ai` / `docs.meshy.ai` / `developers.tripo3d.ai` で確認済み）。
残りの項目は、**利用者が画面を開いて内容を提示する**以外に確認手段がない。

| ID | 内容 | 確認先 | 影響 |
| --- | --- | --- | --- |
| ~~U-3a~~ | ~~Tripo：画像→3Dの1件あたりのクレジット数、オプションの加算、1クレジットのUSD単価~~ → **確認済み（2026-09-08）／実取引で確定（2026-09-09）**：30 credits／$0.01 per credit ＝ **$0.30/件**。$5 の購入で 500credits が付与されたことで単価が裏づけられた。第1.4節・第1.9.1節を参照 | `https://developers.tripo3d.ai/en/pricing`、実際の購入 | 解消 |
| U-12 | Tripo：価格表の「H Series / P Series / Splat Series」のうち、`model_version = v2.5-20250123` がどれに当たるか。確認した 30 credits は H Series タブの値 | 価格ページの他タブ、または各シリーズの対象モデル一覧 | 3シリーズで Image to 3D の価格が同じなら影響しない。異なる場合は見積額の見直しが要る |
| ~~U-3b~~ | ~~Meshy：クレジットのUSD単価~~ → **確認済み（2026-09-08）**：USD表示の購入画面で確認。上限側を採り **$0.04/credit × 30credits ＝ $1.20/件**。第2.4.1節・第2.4.3節・第2.4.4節を参照 | `https://meshy.ai/settings/subscription`（USD表示） | 解消 |
| ~~U-13~~ | ~~Meshy：追加クレジットパックの価格~~ → **確認済み（2026-09-08）**：$10/250、$32/1,000、$84/3,000。第2.4.3節を参照 | 同上 | 解消 |
| ~~U-14~~ | ~~Meshy：月次クレジットの繰越可否、および税込か税別か~~ → **確認済み（2026-09-08、規約第2.10条・第4条）**：**月次クレジットは繰越不可**。追加購入クレジットは購入から1年有効。料金は USD 建ての**税別** | 規約 | 解消 |
| ~~U-15~~ | ~~Meshy：Free プランでAPIキーを発行できるか~~ → **確認済み（2026-09-09）：できない。** 設定の「API」タブは有料プランへの案内（`Unlock Powerful APIs for Your Workflow` / `Upgrade Now`）で覆われており、キーの一覧も発行操作も使えない状態だった。第2.8節を参照 | `https://meshy.ai/settings/api`（利用者が確認） | **解消。ただし結論は重い**：本システムはAPI経由でしか動かないため、**Meshy を A3.5 に参加させるには Pro（$20/月）以上の契約が要る** |
| ~~U-23~~ | ~~Tripo：無料アカウントでクレジットが付与されるか、APIキーを発行できるか~~ → **確認済み（2026-09-09）**：APIキーは発行できる。**認証も通る。しかしクレジット残高は 0（凍結分 0）**。第1.8節を参照 | 利用者の環境で `app.cli check-provider tripo --connect`（公式SDKの `get_balance()`）を実行 | **解消。結論は「無料では生成できない」。** クレジットの購入が要る（`docs/decisions.md` T-14） |
| ~~U-25~~ | ~~Tripo：クレジットの最小購入額と購入単位~~ → **確認済み（2026-09-09）**：**最小 $1、1〜100000 の整数 USD**。任意額を入力できる。第1.9節を参照 | `https://developers.tripo3d.ai/ja/billing` の「クレジットを追加する」 | 解消。**A3.5 に必要なのは $2**（整数のみなので $1.50 は指定できない） |
| U-24 | Meshy：追加クレジットパックを購入した場合、規約第3.2条の「paid Meshy plan」に該当するか。**U-15 の解消により、A3.5 の選択肢としては意味を失った**（クレジットを買ってもAPIが使えないため）。将来 Pro 以上を契約したうえで追加購入する場合に、あらためて確認する | Meshy への照会、または購入後の表示 | A3.5 の関門ではない |
| U-16 | Meshy：**Meshy Education Plan** の割引内容と対象条件 | 購入画面の `Apply Now` | 教室での採用なら費用が変わる可能性がある |
| U-17 | Tripo：**API経由で作成した資産の既定の公開範囲**。規約は「選ばなければ最も公開度の高い設定になりうる」と定めている（第1.6.5節） | 実物の資産の公開設定、または公式資料 | **生徒の作品が既定で公開される可能性がある。A3.5 の前に実物で確認が要る** |
| U-18 | Tripo：**「Paid User」になる条件**。定額契約が要るのか、クレジットの単品購入で足りるのか。無料と有料で権利が正反対のため境目が重要（第1.6.1節） | 規約の補足、または事業者への問い合わせ | 生徒作品を送れるかを直接左右する |
| U-19 | 教室の運用：**13歳未満の生徒について保護者・後見人の同意をどう取り、どこに記録するか**。Tripo の規約が明示的に求めている（第1.6.6節） | 運営の運用設計 | 本システムは生徒の年齢を持っていない。仕様の利用同意（`consent_status`）で足りるかを運営が判断する |
| U-20 | Meshy：規約第2.9条と第3.2条の関係（第2.7.2節）。**2026-09-08、運営が「学習利用の対象から外す必要はない」と判断したため（`docs/decisions.md` T-9）、A3.5 の関門ではなくなった。** 記録として残す | Meshy への書面での照会（`legal@meshy.ai`） | **関門ではない。** 将来「学習に使われません」と説明したくなった場合にだけ照会が要る |
| U-22 | 両社：「train / validate / test / improve」の具体的な範囲。どちらの規約にも定義条項が無い（第2.7.1.1節）。**2026-09-08、運営の判断（T-9）により A3.5 の関門ではなくなった。** 記録として残す | 両社への書面での照会 | **関門ではない。** ただし**利用同意の説明文言を書くとき**（T-10）に、どこまで起こりうるかを説明する材料として有用 |
| U-21 | 両社：**送信した画像（Input）の保持期間**。どちらの利用規約にも記載が無い | 各社のプライバシーポリシー | 生徒作品を送る判断に必要（U-4a の残り） |
| U-4a | 両社：送信した画像と生成物の保持期間。**生成物は両社とも確認済み**（Meshy はAPI経由なら3日で削除＝規約第2.5条で裏づけ／Tripo は保管義務も期間の定めも無く、公開領域のものは永久保持されうる）。**残るのは両社とも「送信した画像（Input）の保持期間」**で、どちらの規約にも記載が無い | 各社のプライバシーポリシー | 生徒作品を送る判断に必要 |
| ~~U-4b~~ | ~~両社：学習利用の可否と opt-out の手段~~ → **両社とも規約本文で確認済み（2026-09-08）**。**Tripo（有料）：学習に使わないと明記**（第1.6.2節）。**Meshy：Enterprise 以外（Free/Pro/Premium/Ultra）は学習利用の対象と明記**し、外す手段は Order（個別契約）のみ（第2.7.1節）。Tripo 無料は権利自体を会社が保持 | 各社の規約 | **解消。ただし結論が両社で正反対**。学習利用を避けるなら Tripo の有料。Meshy は Enterprise か Order が要る |
| U-4c | 両社：生成物の利用条件。**Tripo は確認済み（第1.6.3節）：有料なら商用・非商用とも可。Outputs で競合モデル・サービスを作ることのみ禁止**。**Meshy は Free = CC BY 4.0、Pro以上 = Private license for all assets と画面で確認（第2.4.2節）**。Meshy の条文そのものは未取得 | 各社の規約 | 教室採用の判断に必要。**両社とも無料プランでは成果物の扱いが教室向きでない** |
| ~~U-7~~ | ~~両社：成果物を配信するホスト名~~ → **両社とも確認済み**。**Meshy：`assets.meshy.ai`（第2.6節）**。**Tripo：`tripo-data.rg1.data.tripo3d.com`（2026-09-10、実物のURLで確認。第1.13節）** | 実物のURL（`docs/early-check-plan.md` 3.3 の手順で確認した） | **解消。** ただし Tripo のホスト名は **`rg1` という地域識別子を含む**ため、別の地域では変わる可能性がある（第1.13節） |
| U-8 | 両社：レート制限の具体的な数値。**Meshy は確認済み（第2.2.2節。最も低いプランでも 20 req/s・同時10件）**。Tripo は未確認 | 公式資料 | Meshy 側は上限を十分下回ることを確認済み。Tripo 側は未確認 |
| ~~U-9~~ | ~~Meshy：`DELETE /image-to-3d/{id}` が実行中タスクの取消になるか、課金はどうなるか~~ → **確認済み（2026-09-08）**：実行中タスクの取消。`PENDING` は返却、`IN_PROGRESS` は返却なし、終了済みは取消不可。第2.2.1節を参照 | `https://docs.meshy.ai/llms-full.txt` | 解消（`supports_cancel = True` にした。枠は保持する） |
| U-10 | Tripo：`model_version` をより新しい版（`v3.1-20260211` 等）にすべきか。品質と価格の差 | 公式資料 | 既定の `v2.5-20250123` を採用中 |
| ~~U-11~~ | ~~Tripo：送信時の形式固定申告が PNG/WebP 入力の結果に影響するか~~ → **原因を特定して修正した（2026-09-09）**。公式SDK 0.4.2 の `upload_file` は**ファイル名の拡張子**から申告形式を決める（`_EXT_TO_STS_FORMAT`。不明な拡張子は `"jpeg"`）。こちらの保存キーは拡張子を持たないため、**PNG/WebP でも "jpeg" と申告されていた**。正しい拡張子を付けた一時ファイル経由で渡すようにした（`docs/decisions.md` A-46） | 公式SDKのソース | 解消。**PNG/WebP をそのまま送れる** |
| U-26 | Tripo：**多視点入力（`multiview_to_model`）の1件あたりの価格**と、`images` の各位置がどの向きに対応するか。SDKに関数は実在するが、価格ページで確認したのは Image to 3D の 30credits のみ。第1.10節を参照 | 価格ページの他項目、または公式のAPIドキュメント | **A3.5 の関門ではない**（正面1枚に切り出して回避する。`docs/decisions.md` T-15）。教室の題材が「正面＋背面を並べた1枚」中心なら、本運用の前に確認が要る |
| U-27 | Tripo：**`convert_model` の1件あたりの価格**。形式変換・`face_limit`・`texture_size`・`pivot_to_center_bottom`・`flatten_bottom` はすべてこの関数で行う。第1.11.1節を参照 | 価格ページの他項目、または公式のAPIドキュメント | **メタバース用途の中心機能**（`docs/decisions.md` T-17）。生成1件のほかに変換1件分の費用がかかるなら、見積 $0.30/件 は不足する |
| U-28 | Tripo：**`rig_model` の1件あたりの価格**と、`check_riggable` が課金対象か。第1.11.2節を参照 | 同上 | アバター用途にするなら必須。置物用途なら不要 |
| U-29 | Tripo：**`smart_lowpoly` の価格**、および生成時の `smart_low_poly=True` と後処理の `smart_lowpoly` の違い | 同上 | プラットフォームの三角形数上限を満たす手段。生成時の引数で足りるなら追加費用は不要 |
| U-30 | Tripo：**変換・リグ後の成果物の配信ホスト**が生成物と同じか（U-7 と同じ問題）。および変換後タスクの成果物の保持期間 | 実物のURL | 異なる場合は `APP_TRIPO_DOWNLOAD_HOSTS` に追加が要る |
| U-31 | Tripo：**画像1枚から生成したメッシュが `rig_model` に耐えるか**。腕・脚が胴体に融合していればリグは失敗する見込み。`check_riggable` が事前に判定するが、**成功率と、どんな絵なら通るのか（T字ポーズが必要か）が未確認**。第1.11.2節を参照 | 実際に1件試す（`check_riggable` が無課金なら安価に確認できる可能性がある。それも未確認） | **アバター用途（`docs/decisions.md` T-18 案C）の成否を直接左右する**。置物用途なら不要 |
| U-32 | Tripo：**`convert_model` の `export_orientation` と `scale_factor` が Unity の左手系 Y-up・1unit=1m にどう対応するか**。SDKに説明が無い | 実物をUnityに取り込んで確認 | 向きと大きさが合わないと配置のたびに手直しが要る。**Unity 側での確認が必要で、本システムの範囲外**（T-18） |
| U-33 | Tripo：**`text_to_image` の1件あたりの価格**と、`negative_prompt` がどこまで効くか。第1.12節を参照 | 価格ページの他項目、または公式のAPIドキュメント | **画像生成を本システムに取り込む案の中心**（`docs/decisions.md` T-21）。3D生成より十分安くなければ「安い工程で絞る」設計が成り立たない |
| U-34 | Tripo：**`generate_image` の `template` が取る値**、**`t_pose` と `sketch_to_render` の実際の挙動・品質・価格**。SDKの型は bool だが、何が起きるかの説明が無い | 同上 | **`sketch_to_render` は T-21 の推奨案（生徒が描く→整える）の中心機能**。`t_pose` はアバター用途（T-18 案C）のリグ成功率を左右する |
| U-35 | Tripo：**`text_to_model` の価格**が `image_to_model` と同じか。`image_seed` を持つことから内部で画像を作っている構造と推測されるが**未確認** | 同上 | 同額なら画像工程を挟む意味は「確認できること」だけになる |
| U-36 | Tripo：**`generate_multiview_image` の価格**と、出力が `multiview_to_model` にそのまま渡せるか | 同上 | 1枚から多視点を作れれば U-26（多視点入力）の問題を裏返しに解決できる |

### 確認できたら行うこと

1. この文書の該当行を「確認済み」に書き換え、確認日と根拠URLを入れる
2. ~~`app/services/presets.py` の `price_max_micro_usd`・`price_version`・`price_checked_on`・
   `price_source_url` を埋め、`is_unverified=False` にする~~ → **完了（2026-09-08）**。
   `is_enabled=True` にするのは**データ取扱い条件（U-4a/U-4b/U-4c）が確認できてから**
3. `.env` の `APP_TRIPO_DOWNLOAD_HOSTS` / `APP_MESHY_DOWNLOAD_HOSTS` に配信ホストを設定する
   （Meshy は `assets.meshy.ai`。**Tripo は実物のURLで確かめる**。
   未設定のまま1件生成すると、保存失敗のメッセージにホスト名が出るので、
   それを設定して「保存だけ再試行」する＝追加課金なし。`docs/early-check-plan.md` 3.3）
4. セット上限額と全体上限額（`APP_GLOBAL_COST_CAP_USD`）を設定する
5. そのうえで A3.5（早期実感触、5題材×2社＝10件）に進む
