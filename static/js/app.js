(async function () {
  "use strict";
  const outbox = window.WashOutbox;
  const connection = document.getElementById("connection-status");
  const form = document.getElementById("draft-form");
  const saveButton = document.getElementById("save-draft");
  const workspaceOwner = document.getElementById("workspace-owner");
  const feedback = document.getElementById("draft-feedback");
  const syncFeedback = document.getElementById("sync-feedback");
  let usable = false;
  let connectivity = navigator.onLine ? "unknown" : "offline";
  function connectionText() {
    if (connection) {
      const beneficiary = !!document.getElementById("complaint-form") && !document.body.classList.contains("employee-layout");
      connection.textContent = connectivity === "connected" ? "متصل بالنظام" : connectivity === "offline" ? beneficiary ? "الاتصال منقطع · البلاغات غير المرسلة محفوظة على الجهاز" : "الاتصال منقطع · المسودات محفوظة على الجهاز" : "جارٍ التحقق من الاتصال…";
      connection.dataset.state = connectivity;
    }
  }
  function draftElement(draft, status) {
    const card = document.createElement("article"); card.className = "draft-item";
    card.dataset.state = draft.status || "synced";
    const title = document.createElement("h3"); title.textContent = draft.title;
    const description = document.createElement("p"); description.textContent = draft.description;
    const label = document.createElement("small"); label.textContent = status;
    card.append(title, description, label);
    const timestamp = draft.created_at || draft.received_at;
    if (timestamp) {
      const time = document.createElement("time"); time.dateTime = timestamp;
      time.textContent = new Intl.DateTimeFormat("ar-YE", {dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Aden"}).format(new Date(timestamp));
      card.append(time);
    }
    return card;
  }
  async function renderLocal() {
    const user = await outbox.identity();
    usable = !!user;
    if (saveButton) saveButton.disabled = !usable;
    if (workspaceOwner) workspaceOwner.textContent = user ? `${user.name} · مساحتك الخاصة للحفظ والمتابعة` : "سجل الدخول أول مرة بوجود إنترنت لتجهيز مساحتك على الجهاز.";
    if (document.body.dataset.shell === "true") {
      const employee = !!user && user.role !== "citizen";
      document.body.classList.toggle("employee-layout", employee);
      const legacy = document.querySelector("[data-legacy-drafts]");
      if (legacy) {legacy.open = employee; legacy.classList.toggle("employee-drafts", employee);}
      for (const element of document.querySelectorAll("[data-beneficiary-workspace]")) element.hidden = !user || employee;
      const rail = document.querySelector(".work-rail");
      if (rail) rail.hidden = !employee;
      for (const item of document.querySelectorAll("[data-director-nav]")) item.hidden = user?.role !== "director";
      for (const item of document.querySelectorAll("[data-violation-nav]")) item.hidden = !user || !["director", "technician", "system_manager", "secretariat", "followup", "finance"].includes(user.role);
      const roles = {citizen: "مستفيد", director: "مدير الشؤون الفنية", technician: "فني", employee: "موظف", system_manager: "مسؤول النظام", secretariat: "السكرتارية", followup: "قسم المتابعة", finance: "القسم المالي"};
      document.getElementById("workspace-title").textContent = employee ? "دفتر العمل الميداني" : "مسوداتي";
      document.getElementById("workspace-role").textContent = user ? roles[user.role] || "مساحة العمل" : "مساحة العمل";
      document.getElementById("draft-kind-title").textContent = employee ? "مسودة مخالفة ميدانية" : "مسودة بلاغ";
      for (const item of document.querySelectorAll("[data-account-name]")) item.textContent = user?.name || "";
      for (const item of document.querySelectorAll("[data-account-role]")) item.textContent = user ? roles[user.role] || "" : "";
      const nav = document.querySelector(".top-nav");
      if (nav && user) {
        nav.replaceChildren();
        const link = document.createElement("a"); link.href = "/workspace/"; link.textContent = user.role === "citizen" ? "تقديم بلاغ" : "مسوداتي";
        nav.append(link);
        if (user.role === "citizen") {const reports = document.createElement("a"); reports.href = "/workspace/complaints/"; reports.textContent = "بلاغاتي"; nav.append(reports);}
        const role = document.createElement("span"); role.className = "top-role"; role.textContent = roles[user.role] || "مساحة العمل";
        nav.append(role);
      }
    }
    const target = document.getElementById("draft-list");
    if (!target) return;
    target.replaceChildren();
    const drafts = user ? await outbox.list(user.id) : [];
    for (const [id, count] of [["draft-count", drafts.length], ["pending-count", drafts.filter(d => d.status !== "synced").length], ["synced-count", drafts.filter(d => d.status === "synced").length]]) {
      const element = document.getElementById(id);
      if (element) element.textContent = new Intl.NumberFormat("ar-YE").format(count);
    }
    if (!drafts.length) target.textContent = "لا توجد مسودات لهذا الحساب على الجهاز.";
    const labels = {pending: "محفوظة على الجهاز · بانتظار وصولها للنظام", synced: "تم حفظ المسودة في النظام · ليست بلاغًا رسميًا", review: "محفوظة على الجهاز · تحتاج مراجعة"};
    for (const draft of drafts) target.append(draftElement(draft, labels[draft.status] || draft.status));
  }
  async function renderServer() {
    const target = document.getElementById("server-drafts");
    if (!target) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch("/api/drafts/", {credentials: "same-origin", cache: "no-store", signal: controller.signal});
      if (!response.ok) return;
      const data = await response.json(); target.replaceChildren();
      const count = document.getElementById("server-count");
      if (count) count.textContent = new Intl.NumberFormat("ar-YE").format(data.drafts.length);
      if (!data.drafts.length) target.textContent = "لا توجد مسودات محفوظة في النظام بعد.";
      for (const draft of data.drafts) target.append(draftElement(draft, "محفوظة في النظام"));
    } catch (_) { target.textContent = "تعذر تحميل مسودات النظام. مسودات الجهاز محفوظة."; }
    finally {clearTimeout(timeout);}
  }
  async function syncNow() {
    const result = await outbox.sync();
    if (result.state !== "busy") connectivity = result.state === "offline" || result.state === "server-error" ? "offline" : "connected";
    connectionText();
    const messages = {
      complete: result.sent ? "وصلت البيانات المحفوظة إلى النظام." : "تم التحقق من المزامنة. المسودات غير المقبولة تبقى للمراجعة.",
      offline: "تعذر الوصول للنظام. تبقى المسودات محفوظة على الجهاز وسنحاول عند عودة الاتصال.",
      login: "يلزم تسجيل الدخول لإرسال المسودات. تبقى محفوظة على الجهاز.",
      "different-account": "الحساب المفتوح لا يطابق حساب المسودات. لن تُرسل باسم حساب آخر.",
      "server-error": "تعذر حفظ المسودات في النظام حاليًا. بقيت محفوظة على الجهاز.",
    };
    if (syncFeedback && messages[result.state]) syncFeedback.textContent = messages[result.state];
    await renderLocal();
    if (result.state === "complete") await renderServer();
    window.dispatchEvent(new Event("wash-sync-complete"));
  }
  function showError(error) {
    if (feedback) feedback.textContent = "تعذر الحفظ المحلي. تحقق من سماح المتصفح بالتخزين؛ لا تغلق النموذج قبل نجاح الحفظ.";
    if (syncFeedback) syncFeedback.textContent = error.message || "تعذر الوصول إلى تخزين هذا الجهاز.";
  }
  if (form) form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!usable) { if (feedback) feedback.textContent = "يلزم تجهيز الحساب أولًا بوجود إنترنت."; return; }
    saveButton.disabled = true;
    try {
      await outbox.save(form.elements.title.value, form.elements.description.value);
      if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
      form.reset(); feedback.textContent = "تم حفظ المسودة على الجهاز. ستتم محاولة إرسالها إلى النظام.";
      await renderLocal();
      if (window.washRegistration?.sync) await window.washRegistration.sync.register("wash-sync").catch(() => {});
      await syncNow();
    } catch (error) { showError(error); }
    finally { saveButton.disabled = !usable; }
  });
  document.getElementById("sync-now")?.addEventListener("click", () => syncNow().catch(showError));
  window.addEventListener("online", () => syncNow().catch(showError));
  window.addEventListener("offline", () => { connectivity = "offline"; connectionText(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && usable) syncNow().catch(showError); });
  setInterval(() => { if (document.visibilityState === "visible" && usable && navigator.onLine) syncNow().catch(showError); }, 30000);
  for (const logout of document.querySelectorAll("[data-logout], [data-session-entry]")) logout.addEventListener("submit", async event => {
    event.preventDefault();
    try { await outbox.setIdentity(null); } finally { HTMLFormElement.prototype.submit.call(logout); }
  });
  // Bind submit handlers before the first asynchronous operation. A fast click must never
  // trigger a native form submission or put draft contents in a URL.
  try {
    if (saveButton) saveButton.disabled = true;
    if ("serviceWorker" in navigator && window.isSecureContext) {
      try {
        const registration = await navigator.serviceWorker.register("/sw.js");
        navigator.serviceWorker.addEventListener("message", () => { syncNow().catch(showError); });
        window.washRegistration = registration;
      } catch (_) {
        if (feedback) feedback.textContent = "تعذر تجهيز فتح الواجهة دون اتصال. أعد فتح النظام بوجود إنترنت.";
      }
    }
    const session = await outbox.currentSession().catch(() => undefined);
    if (session) { await outbox.setIdentity(session); connectivity = "connected"; }
    else if (session === null) { connectivity = navigator.onLine ? "connected" : "offline"; }
    else { connectivity = "offline"; }
    await renderLocal(); connectionText();
    window.washAppReady = true; window.dispatchEvent(new Event("wash-ready"));
    await renderServer();
    if (session) await syncNow();
  } catch (error) { showError(error); }
})();
