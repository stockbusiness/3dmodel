/* 生成結果画面の更新と復帰操作（仕様第6.4章・第7章）。

   - 詳細画面は5秒ごとに状態を取り直す
   - 「通信確認待ち」「受付結果不明」「保存失敗」を生成失敗と区別して表示する
   - 追加の課金が発生するのは再生成だけであることを明示する
   - 架空の進捗率は出さない。事業者が返さない場合は段階のみ */

const DETAIL_POLL_MS = 5000;

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

// 生成そのものの失敗と、通信・保存の問題を区別する（仕様第6.4章）
const NOT_A_GENERATION_FAILURE = {
  submission_unknown:
    "外部に届いたかどうかを確認できていません。生成の失敗とは限りません。自動での再送信は行いません。",
  download_failed:
    "生成は終わっていますが、ファイルの受け取りに失敗しました。保存だけ再試行できます（課金なし）。",
  validation_failed:
    "受け取ったファイルが検査に通りませんでした。まず保存だけ再試行してください（課金なし）。",
  monitoring_paused:
    "30分を過ぎても完了しません。外部の失敗とは断定していません。再確認で監視を再開できます。",
};

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)art3d_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

const panel = document.querySelector("[data-generation-id]");

function showResult(text, kind) {
  const box = panel && panel.querySelector("[data-action-result]");
  if (!box) return;
  box.textContent = text;
  box.className = "notice " + (kind || "");
  box.hidden = !text;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
    body: JSON.stringify(body || {}),
  });
  let detail = "";
  try {
    const data = await response.json();
    detail = data && data.detail ? data.detail : "";
  } catch (error) {
    detail = "";
  }
  if (!response.ok) throw new Error(detail || `失敗しました（${response.status}）`);
  return true;
}

function elapsedLabel(isoString) {
  if (!isoString) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(isoString)) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${Math.floor(minutes / 60)} 時間 ${minutes % 60} 分`;
}

function jst(isoString) {
  if (!isoString) return "—";
  const date = new Date(isoString.endsWith("Z") || isoString.includes("+") ? isoString : isoString + "Z");
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function applyStatus(data) {
  const badge = panel.querySelector("[data-status-badge]");
  const note = panel.querySelector("[data-status-note]");
  const progress = panel.querySelector("[data-progress]");
  const elapsed = panel.querySelector("[data-elapsed]");
  const lastChecked = panel.querySelector("[data-last-checked]");

  if (badge) badge.textContent = STATUS_LABELS[data.tech_status] || data.tech_status;
  if (note) note.textContent = NOT_A_GENERATION_FAILURE[data.tech_status] || "";
  if (progress) {
    progress.textContent =
      data.progress_percent === null || data.progress_percent === undefined
        ? "事業者が進捗を返さないため、段階のみ表示しています"
        : `${data.progress_percent}%`;
  }
  if (elapsed) elapsed.textContent = elapsedLabel(data.submitted_at || null);
  if (lastChecked) lastChecked.textContent = jst(data.last_checked_at);
}

let previousStatus = null;

async function poll() {
  if (!panel) return;
  const id = panel.dataset.generationId;
  try {
    const response = await fetch(`/api/generations/${id}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return;
    const data = await response.json();
    if (previousStatus === null) previousStatus = data.tech_status;
    applyStatus(data);
    if (data.tech_status !== previousStatus) {
      // 状態が変わったら、操作の選択肢と成果物を出し直すために読み込み直す
      window.location.reload();
      return;
    }
  } catch (error) {
    // 通信できないだけでは何も壊さない。次回に再試行する
  }
  window.setTimeout(poll, DETAIL_POLL_MS);
}

function bind() {
  if (!panel) return;

  panel.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = panel.dataset.generationId;
      const action = button.dataset.action;
      button.disabled = true;
      try {
        if (action === "retry") {
          const form = panel.querySelector("[data-retry]");
          const reason = form.querySelector("[name=reason]").value.trim();
          if (!reason) {
            showResult("再生成の理由を入力してください。", "warn");
            return;
          }
          const token = await fetch("/api/form-token").then((r) => r.json());
          const response = await fetch(`/api/generations/${id}/retry`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken(),
              "Idempotency-Key": token.idempotency_key,
            },
            body: JSON.stringify({ reason }),
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || "再生成を受け付けられませんでした");
          window.location.href = `/generations/${data.id}`;
          return;
        }
        await post(`/api/generations/${id}/${action}`);
        window.location.reload();
      } catch (error) {
        showResult(error.message, "danger");
      } finally {
        button.disabled = false;
      }
    });
  });

  panel.querySelectorAll("[data-resolve-outcome]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = panel.dataset.generationId;
      const outcome = button.dataset.resolveOutcome;
      const form = button.closest("form");
      const evidence = form.querySelector("[name=evidence]").value.trim();
      if (!evidence) {
        showResult("証跡・メモを入力してください。", "warn");
        return;
      }
      button.disabled = true;
      try {
        if (outcome === "artifact") {
          await post(`/api/generations/${id}/resolve-artifact`, { evidence });
        } else {
          const taskField = form.querySelector("[name=provider_task_id]");
          await post(`/api/generations/${id}/resolve-submission`, {
            outcome,
            evidence,
            provider_task_id: taskField ? taskField.value.trim() : null,
          });
        }
        window.location.reload();
      } catch (error) {
        showResult(error.message, "danger");
      } finally {
        button.disabled = false;
      }
    });
  });
}

bind();
if (panel && panel.dataset.inProgress === "1") {
  window.setTimeout(poll, DETAIL_POLL_MS);
}
