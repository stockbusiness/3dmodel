/* 3D表示。model-viewer は固定版をローカル配信する（仕様第3章）。
   読込エラーと長時間待ちを表示し、操作不能・永久ローディングにしない（仕様第13章 試験14）。 */

import "/static/vendor/model-viewer-4.3.1.min.js";

const LOAD_WARN_MS = 15000;

// 圧縮GLBが来ても外部（gstatic）へ取りに行かせない（仕様第12章）。
// デコーダー本体の同梱は A3 で判断する。app/static/vendor/README.md を参照。
customElements.whenDefined("model-viewer").then(() => {
  const ModelViewerElement = customElements.get("model-viewer");
  if (!ModelViewerElement) return;
  ModelViewerElement.dracoDecoderLocation = "/static/vendor/draco/";
  ModelViewerElement.ktx2TranscoderLocation = "/static/vendor/ktx2/";
});

function setStatus(host, text, kind) {
  const box = host.querySelector("[data-viewer-status]");
  if (!box) return;
  box.textContent = text;
  box.className = "viewer-status notice " + (kind || "");
  box.hidden = !text;
}

export function setupViewer(host) {
  const viewer = host.querySelector("model-viewer");
  if (!viewer) return;

  // この画面はモデルが1件だけなので loading="eager" で即座に読み込む。
  // 2件を並べる比較画面（A4）では、重いGLBを同時に読まないため遅延読込にする（仕様第6.5章）。
  // 警告は転送が進み始めてから数え、まだ始まっていない要素で誤って出さない（仕様第13章 試験14）。
  let settled = false;
  let slowTimer = null;

  setStatus(host, "モデルを読み込んでいます。", "");

  const armSlowTimer = () => {
    if (slowTimer !== null || settled) return;
    slowTimer = window.setTimeout(() => {
      if (!settled) {
        setStatus(
          host,
          "読み込みに時間がかかっています。通信状況をご確認ください。ファイルの再取得は下のボタンから行えます（追加の生成は行いません）。",
          "warn"
        );
      }
    }, LOAD_WARN_MS);
  };

  const finish = (text, kind) => {
    settled = true;
    if (slowTimer !== null) window.clearTimeout(slowTimer);
    setStatus(host, text, kind);
  };

  // totalProgress が 0 のあいだは、まだ転送が始まっていない
  viewer.addEventListener("progress", (event) => {
    const progress = event && event.detail ? event.detail.totalProgress : 0;
    if (progress > 0) armSlowTimer();
  });

  viewer.addEventListener("load", () => finish("", ""));

  viewer.addEventListener("error", (event) => {
    const detail = event && event.detail ? event.detail.type : "";
    finish(
      "モデルを表示できませんでした（" + (detail || "読込エラー") +
        "）。これはファイルの表示の問題であり、生成のやり直しではありません。",
      "danger"
    );
  });

  host.querySelectorAll("[data-orbit]").forEach((button) => {
    button.addEventListener("click", () => {
      viewer.cameraOrbit = button.dataset.orbit;
      viewer.jumpCameraToGoal();
    });
  });

  const reset = host.querySelector("[data-viewer-reset]");
  if (reset) {
    reset.addEventListener("click", () => {
      viewer.cameraOrbit = viewer.dataset.baseOrbit || "0deg 75deg auto";
      viewer.fieldOfView = "auto";
      viewer.jumpCameraToGoal();
    });
  }

  host.querySelectorAll("[data-bg]").forEach((button) => {
    button.addEventListener("click", () => {
      viewer.style.backgroundColor = button.dataset.bg;
    });
  });
}

document.querySelectorAll("[data-viewer-host]").forEach(setupViewer);
