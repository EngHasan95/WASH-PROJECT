(function () {
  "use strict";
  const form = document.getElementById("violation-form");
  if (!form) return;
  const shell = document.body.dataset.shell === "true";
  if (shell && location.pathname !== "/staff/violations/new/") return;
  if (shell) {
    document.querySelector("[data-intake-shell]").hidden = true;
    document.title = "تسجيل مخالفة — مياه مأرب";
  }
  const feedback = document.getElementById("violation-feedback");
  const button = document.getElementById("violation-save");
  const target = document.getElementById("violation-local-list");
  let ownerId = null;
  button.disabled = true;
  function itemNode(item) {
    const card = document.createElement("article"); card.className = "draft-item";
    const heading = document.createElement("h3"); heading.textContent = `${item.data.person_name} · ${item.data.area}`;
    const status = document.createElement("p");
    status.textContent = item.status === "synced" ? `${item.receipt.reference} · وصلت إلى مسؤول النظام` : item.status === "review" ? `محفوظة على الجهاز · ${item.error}` : "محفوظة على الجهاز · بانتظار الإرسال";
    card.append(heading, status);
    if (item.receipt?.detail_url) {
      const link = document.createElement("a"); link.href = item.receipt.detail_url; link.textContent = "فتح ملف المخالفة ←"; card.append(link);
    }
    return card;
  }
  async function render() {
    const user = await WashOutbox.identity();
    ownerId = user?.role === "technician" ? user.id : null;
    if (shell) {
      document.querySelector("[data-violation-shell]").hidden = !ownerId;
      document.querySelector("[data-violation-denied]").hidden = !!ownerId;
    }
    button.disabled = !ownerId;
    target.replaceChildren();
    if (!ownerId) {target.textContent = "جهّز حساب الفني على هذا الجهاز بوجود الإنترنت أولًا."; return;}
    const items = await WashOutbox.listViolations(ownerId);
    if (!items.length) target.textContent = "لا توجد مخالفات محفوظة على هذا الجهاز.";
    items.forEach(item => target.append(itemNode(item)));
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!ownerId) {feedback.textContent = "يلزم دخول الفني أولًا بوجود الإنترنت."; return;}
    button.disabled = true;
    const data = Object.fromEntries(new FormData(form).entries());
    try {
      await WashOutbox.saveViolation(data, ownerId);
      form.reset();
      feedback.textContent = "حُفظت المخالفة على الجهاز. سيُحاول النظام رفعها عند عودة الاتصال.";
      if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
      await render();
      if (window.washRegistration?.sync) window.washRegistration.sync.register("wash-sync").catch(() => {});
      const result = await WashOutbox.sync();
      if (result.state === "complete") {await render(); feedback.textContent = "وصلت البيانات المحفوظة إلى النظام، أو بقيت للمراجعة على الجهاز.";}
    } catch (error) {feedback.textContent = error.message || "تعذر الحفظ على الجهاز. لا تغلق الصفحة.";}
    finally {button.disabled = !ownerId;}
  });
  for (const event of ["wash-ready", "wash-sync-complete", "online"]) window.addEventListener(event, () => render().catch(() => {feedback.textContent = "تعذر قراءة بيانات الجهاز.";}));
  if (window.washAppReady) render().catch(() => {});
})();
