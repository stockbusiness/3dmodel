/* 一覧の更新（仕様第6.4章：一覧は15〜30秒程度のポーリング）。

   詳細画面より間隔を長くする。通信できないときは静かに次回へ回す。 */

const panel = document.querySelector("[data-experiment]");
const comparisonPanel = document.querySelector("[data-comparisons]");

const STATUS_LABELS = {
  queued: "受付済み",
  submitting: "送信中",
  running: "生成中",
  downloading: "保存中",
  ready_for_review: "評価待ち",
  submission_unknown: "受付結果不明",
  provider_failed: "事業者側で失敗",
  download_failed: "保存失敗",
  validation_failed: "検査で不合格",
  monitoring_paused: "確認を一時停止",
  cancelled: "中止",
};
const VERDICT_LABELS = {
  unreviewed: "未評価",
  pass: "合格",
  retry_recommended: "再生成候補",
  unsuitable: "題材不適",
};

function jstNow() {
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date());
}

async function refreshAssets() {
  if (!panel) return;
  const id = panel.dataset.experiment;
  try {
    const response = await fetch(`/api/experiments/${id}/generations`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return;
    const rows = await response.json();

    // 題材ごとに、いちばん新しい生成の状態を出す
    const byAsset = new Map();
    rows.forEach((row) => {
      if (!byAsset.has(row.asset_id)) byAsset.set(row.asset_id, row);
    });
    document.querySelectorAll("[data-asset-status]").forEach((cell) => {
      const row = byAsset.get(cell.dataset.assetStatus);
      if (!row) {
        cell.textContent = "—";
        return;
      }
      const status = STATUS_LABELS[row.tech_status] || row.tech_status;
      const verdict = VERDICT_LABELS[row.verdict] || row.verdict;
      cell.textContent = `${status}／${verdict}`;
    });

    const updated = document.querySelector("[data-list-updated]");
    if (updated) updated.textContent = `最終更新 ${jstNow()}（自動更新中）`;
  } catch (error) {
    // 通信できないだけでは何も壊さない
  }
}

async function refreshComparisons() {
  if (!comparisonPanel) return;
  const id = comparisonPanel.dataset.comparisons;
  const list = comparisonPanel.querySelector("[data-comparison-list]");
  try {
    const response = await fetch(`/experiments/${id}/comparisons`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return;
    const rows = await response.json();
    if (!rows.length) {
      list.innerHTML = "<p>まだ比較はありません。</p>";
      return;
    }
    list.innerHTML = "";
    rows.forEach((row) => {
      const line = document.createElement("p");
      const link = document.createElement("a");
      link.href = `/comparisons/${row.id}`;
      link.textContent = "比較を開く";
      const badge = document.createElement("span");
      badge.className = "badge " + (row.revealed ? "ok" : "warn");
      badge.textContent = row.revealed ? "開示済み" : "ブラインド";
      line.appendChild(link);
      line.appendChild(document.createTextNode(" "));
      line.appendChild(badge);
      list.appendChild(line);
    });
  } catch (error) {
    // 何もしない
  }
}

function tick() {
  refreshAssets();
  refreshComparisons();
}

const seconds = Number(panel && panel.dataset.pollSeconds) || 20;
tick();
window.setInterval(tick, Math.max(15, Math.min(30, seconds)) * 1000);
