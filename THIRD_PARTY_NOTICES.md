# 第三者ソフトウェアの表示

本システムが利用・同梱するソフトウェアとライセンスを記録する（仕様第4章）。
ソースのライセンスと、API・生成物の利用条件は別物であることに注意する。

## 同梱している配布物（リポジトリに含まれるもの）

| 名称 | 版 | ライセンス | 用途 | 確認日 |
| --- | --- | --- | --- | --- |
| [Google model-viewer](https://github.com/google/model-viewer) | 4.3.1 | Apache-2.0 | 3Dモデルの表示。`app/static/vendor/model-viewer-4.3.1.min.js` | 2026-09-08 |

ライセンス全文：`app/static/vendor/model-viewer-LICENSE.txt`
配布物の SHA256：`283b0672384614b4847636c306fc93fe4b1fcadc76d668b4e47f0ca76bcf033b`

model-viewer の配布物には three.js（MIT）、lit（BSD-3-Clause）、fflate（MIT）等が
含まれる。各ライセンス表記は配布物の先頭コメントに保持されている。

## 実行時の依存（`uv.lock` で版を固定）

| 名称 | ライセンス | 用途 |
| --- | --- | --- |
| FastAPI | MIT | HTTPサーバー |
| Starlette | BSD-3-Clause | FastAPI の基盤 |
| Uvicorn | BSD-3-Clause | ASGIサーバー |
| Pydantic / pydantic-settings | MIT | 設定と入力の検証 |
| Jinja2 | BSD-3-Clause | 画面テンプレート |
| SQLAlchemy | MIT | DBアクセス |
| Alembic | MIT | マイグレーション |
| Pillow | MIT-CMU | 画像のデコード検査・EXIF除去 |
| argon2-cffi | MIT | パスワードハッシュ |
| python-multipart | Apache-2.0 | ファイルアップロードの解析 |
| httpx | BSD-3-Clause | HTTPクライアント（A3のMeshyアダプターで使用） |

正確な版は `uv.lock` を参照する。ライセンスの表記は各配布物の同梱ファイルによる。

## サンプルデータ

`fixtures/sample_cube.glb`、`fixtures/sample_pyramid.glb` は
`fixtures/make_samples.py` で生成した自作の単純形状であり、合成データである。
実人物の写真・有料素材は含まない（仕様第13章）。

## A3で追記する予定

Tripo公式SDK、Meshy APIクライアントの実装に伴う依存と、
各社の生成物の利用条件・学習利用の可否を `docs/provider-contracts.md` とあわせて記録する。
