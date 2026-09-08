/* 品質比較画面（仕様第6.5章）。

   - A・Bの視点操作をまとめて行う
   - 講師が「いまの向き」を正面の基準として覚えられる（この端末のこの比較にだけ効く）
   - スマホ幅では重いGLBを同時に読み込まず、切り替えて表示する
   - 開示（理由必須） */

const MOBILE_MAX_WIDTH = 700;

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)art3d_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

const panes = Array.from(document.querySelectorAll("[data-compare-pane]"));
const viewers = () => Array.from(document.querySelectorAll("model-viewer"));

// --- 基準角度 ---------------------------------------------------------------

const storageKey = "art3d-front-orbit:" + window.location.pathname;

function storedFrontOrbit() {
  try {
    return window.localStorage.getItem(storageKey) || null;
  } catch (error) {
    return null; // プライベートモード等で読めなくても壊さない
  }
}

function frontOrbit() {
  return storedFrontOrbit() || "0deg 75deg auto";
}

function applyOrbit(orbit) {
  viewers().forEach((viewer) => {
    viewer.cameraOrbit = orbit;
    viewer.jumpCameraToGoal();
  });
}

document.querySelectorAll("[data-all-orbit]").forEach((button) => {
  button.addEventListener("click", () => {
    const requested = button.dataset.allOrbit;
    // 「正面」だけは講師が決めた基準角度を使う
    applyOrbit(requested.startsWith("0deg 75deg") ? frontOrbit() : requested);
  });
});

const resetButton = document.querySelector("[data-all-reset]");
if (resetButton) {
  resetButton.addEventListener("click", () => {
    viewers().forEach((viewer) => {
      viewer.cameraOrbit = frontOrbit();
      viewer.fieldOfView = "auto";
      viewer.jumpCameraToGoal();
    });
  });
}

document.querySelectorAll("[data-all-bg]").forEach((button) => {
  button.addEventListener("click", () => {
    viewers().forEach((viewer) => {
      viewer.style.backgroundColor = button.dataset.allBg;
    });
  });
});

const setFront = document.querySelector("[data-set-front]");
if (setFront) {
  setFront.addEventListener("click", () => {
    const viewer = viewers().find((item) => item.offsetParent !== null) || viewers()[0];
    if (!viewer) return;
    const orbit = viewer.getCameraOrbit();
    const value = `${orbit.theta}rad ${orbit.phi}rad auto`;
    try {
      window.localStorage.setItem(storageKey, value);
    } catch (error) {
      // 保存できなくても、この場の表示は変わらない
    }
    applyOrbit(value);
    setFront.textContent = "この向きを正面として覚えました";
    window.setTimeout(() => {
      setFront.textContent = "いまの向きを「正面」にする";
    }, 2500);
  });
}

const clearFront = document.querySelector("[data-clear-front]");
if (clearFront) {
  clearFront.addEventListener("click", () => {
    try {
      window.localStorage.removeItem(storageKey);
    } catch (error) {
      // 何もしない
    }
    applyOrbit("0deg 75deg auto");
  });
}

// --- スマホでの切り替え -----------------------------------------------------

const switcher = document.querySelector("[data-mobile-switch]");

function showOnly(label) {
  panes.forEach((pane) => {
    pane.hidden = pane.dataset.label !== label;
  });
}

function applyLayout() {
  const narrow = window.innerWidth <= MOBILE_MAX_WIDTH;
  if (switcher) switcher.hidden = !narrow;
  if (narrow) {
    const active = panes.find((pane) => !pane.hidden);
    showOnly(active ? active.dataset.label : (panes[0] && panes[0].dataset.label));
  } else {
    panes.forEach((pane) => {
      pane.hidden = false;
    });
  }
}

document.querySelectorAll("[data-show-label]").forEach((button) => {
  button.addEventListener("click", () => showOnly(button.dataset.showLabel));
});

window.addEventListener("resize", applyLayout);
applyLayout();

// --- 開示 -------------------------------------------------------------------

const revealForm = document.querySelector("[data-reveal]");
if (revealForm) {
  const result = document.querySelector("[data-reveal-result]");
  const button = document.querySelector("[data-reveal-submit]");
  button.addEventListener("click", async () => {
    const reason = revealForm.querySelector("[name=reason]").value.trim();
    if (!reason) {
      result.textContent = "開示の理由を入力してください。";
      result.className = "notice warn";
      result.hidden = false;
      return;
    }
    button.disabled = true;
    try {
      const response = await fetch(`/api/comparisons/${revealForm.dataset.reveal}/reveal`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ reason }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "開示できませんでした");
      window.location.reload();
    } catch (error) {
      result.textContent = error.message;
      result.className = "notice danger";
      result.hidden = false;
    } finally {
      button.disabled = false;
    }
  });
}
