/* 管理画面の操作（仕様第4章・第9章・第12章）。

   - 接続テスト：認証が通るかだけを確かめる。生成は行わないので課金は発生しない
   - 保存物の照合：DBの記録どおりのファイルが実在し、大きさとSHA256が一致するか
   - 保存領域の点検：欠落と孤立を報告するだけで、削除はしない

   APIキーの値はサーバーが返さないので、この画面が受け取ることもない。 */

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)art3d_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function postJson(url) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
    body: "{}",
  });
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;
  }
  return { ok: response.ok, status: response.status, body };
}

// --- 接続テスト -------------------------------------------------------------

function bindConnectionTests() {
  document.querySelectorAll("[data-connection-test]").forEach((button) => {
    button.addEventListener("click", async () => {
      const provider = button.dataset.connectionTest;
      const target = document.getElementById(`conn-result-${provider}`);
      if (!target) return;

      button.disabled = true;
      target.hidden = false;
      target.className = "notice";
      target.textContent = "確認しています…";

      const { ok, body } = await postJson(
        `/api/admin/providers/${encodeURIComponent(provider)}/connection-test`
      );

      if (!ok) {
        target.className = "notice danger";
        target.textContent = (body && body.detail) || "接続テストを実行できませんでした。";
      } else if (body && body.ok) {
        target.className = "notice ok";
        target.textContent = body.note ? `${body.detail}｜${body.note}` : body.detail;
      } else {
        target.className = "notice danger";
        target.textContent = (body && body.detail) || "認証が通りませんでした。";
      }
      button.disabled = false;
    });
  });
}

// --- 保存物の照合 -----------------------------------------------------------

function bindVerify() {
  document.querySelectorAll("[data-verify]").forEach((button) => {
    button.addEventListener("click", async () => {
      const artifactId = button.dataset.verify;
      const target = document.getElementById(`verify-${artifactId}`);
      if (!target) return;

      button.disabled = true;
      target.textContent = "照合中…";

      const { ok, body } = await postJson(
        `/api/admin/artifacts/${encodeURIComponent(artifactId)}/verify`
      );

      if (!ok || !body) {
        target.textContent = "照合できませんでした。";
      } else {
        target.textContent = body.ok ? `一致：${body.detail}` : `不一致：${body.detail}`;
      }
      button.disabled = false;
    });
  });
}

// --- 保存領域の点検 ---------------------------------------------------------

function renderStorage(result) {
  const lines = [
    `記録: ${result.known_count} 件／ディスク上: ${result.on_disk_count} 件`,
    `欠落（記録はあるが実物が無い）: ${result.missing_count} 件`,
    `孤立（実物はあるが参照されていない）: ${result.orphan_count} 件`,
  ];
  const box = document.createElement("div");
  box.className =
    result.missing_count === 0 && result.orphan_count === 0 ? "notice ok" : "notice warn";
  lines.forEach((line) => {
    const p = document.createElement("p");
    p.textContent = line;
    box.appendChild(p);
  });
  if (result.missing_count === 0 && result.orphan_count === 0) {
    const p = document.createElement("p");
    p.textContent = "記録と保存物は一致しています。";
    box.appendChild(p);
  } else {
    const p = document.createElement("p");
    p.className = "footnote";
    p.textContent = "この画面では削除しません。対処は運営が判断してください。";
    box.appendChild(p);
  }
  return box;
}

function bindStorageAudit() {
  const button = document.getElementById("storage-audit");
  const target = document.getElementById("storage-result");
  if (!button || !target) return;

  button.addEventListener("click", async () => {
    button.disabled = true;
    target.hidden = false;
    target.textContent = "点検しています…";

    const response = await fetch("/api/admin/storage");
    if (!response.ok) {
      target.textContent = "点検できませんでした。";
      button.disabled = false;
      return;
    }
    const result = await response.json();
    target.textContent = "";
    target.appendChild(renderStorage(result));
    button.disabled = false;
  });
}

bindConnectionTests();
bindVerify();
bindStorageAudit();
