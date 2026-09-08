# 同梱している外部配布物

仕様第3章・第12章により、model-viewer はバージョンを固定してローカル配信する。
CDNのlatestを動的に取得しない。

| ファイル | 内容 | 版 | 取得元 | 取得日 |
| --- | --- | --- | --- | --- |
| `model-viewer-4.3.1.min.js` | Google model-viewer（ESモジュール） | 4.3.1 | npm `@google/model-viewer` | 2026-09-08 |
| `model-viewer-LICENSE.txt` | 上記のライセンス（Apache-2.0） | 4.3.1 | 同上 | 2026-09-08 |

配布物の SHA256：

```
283b0672384614b4847636c306fc93fe4b1fcadc76d668b4e47f0ca76bcf033b  model-viewer-4.3.1.min.js
```

## draco/ と ktx2/ が空である理由

model-viewer は、GLBが Draco 圧縮（`KHR_draco_mesh_compression`）または
Basis/KTX2 テクスチャ（`KHR_texture_basisu`）を使っているときに限り、
既定で `https://www.gstatic.com/` からデコーダーを取得する。

本システムは仕様第12章により外部接続を限定するため、`app/static/viewer.js` で
`dracoDecoderLocation` と `ktx2TranscoderLocation` をこのディレクトリに向けている。
これにより、圧縮GLBが来ても外部への取得は発生しない。

A1時点で扱うGLB（`fixtures/` の自作サンプル）はどちらの圧縮も使っていないため、
デコーダー本体は同梱していない。

**A3の作業項目**：Tripo / Meshy が返すGLBが Draco / KTX2 を使うかを公式資料と実物で確認し、
使う場合はここにデコーダーを配置する（`docs/decisions.md` の UNVERIFIED U-6）。
配置するまでは、圧縮GLBはビューアーで読込エラーとして表示される（無限ローディングにはしない）。
