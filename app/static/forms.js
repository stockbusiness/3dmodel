/* 連打防止（仕様第6.3章：UIとサーバーの両方で実装する）。
   サーバー側は冪等キーで1件に抑える。ここは利用者に押せなくなったことを見せるだけ。 */

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", () => {
    form.querySelectorAll("[data-submit-once]").forEach((button) => {
      button.disabled = true;
      button.textContent = "送信中…";
    });
  });
});
