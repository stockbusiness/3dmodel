/* 集計画面の設定変更（仕様第10章の校正設定）。 */

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)art3d_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

const form = document.querySelector("[data-calibration]");
if (form) {
  const result = document.querySelector("[data-calibration-result]");
  const button = document.querySelector("[data-calibration-submit]");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const response = await fetch(`/api/experiments/${form.dataset.calibration}/calibration`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({
          target_count: form.querySelector("[name=target_count]").value,
          reviewer_id: form.querySelector("[name=reviewer_id]").value || null,
          reason: "集計画面からの校正設定の変更",
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "設定を保存できませんでした");
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
