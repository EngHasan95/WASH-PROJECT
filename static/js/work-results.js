/* Execution results use the regular task page. No authenticated HTML or tokens are cached. */
(function () {
  "use strict";
  const panel = document.querySelector("[data-work-result-panel]");
  if (!panel) return;
  const form = panel.querySelector("[data-work-result-form]");
  const save = panel.querySelector("[data-work-result-save]");
  const feedback = panel.querySelector("[data-work-result-feedback]");
  const list = panel.querySelector("[data-work-result-list]");
  let card = null, editing = null, retainedPhotos = null, ready = false;
  let previews = [];
  const outbox = window.WashOutbox;
  function text(message) { feedback.textContent = message; }
  async function render() {
    const user = await outbox.identity();
    const items = user && card && user.id === card.owner_id && user.role === card.owner_role ? await outbox.listWorkResults(user.id, card.complaint_id) : [];
    for (const url of previews) URL.revokeObjectURL(url); previews = [];
    list.replaceChildren();
    for (const item of items) {
      const article = document.createElement("article"); article.className = "draft-item"; article.dataset.state = item.status;
      const status = document.createElement("strong"); status.textContent = item.status === "synced" ? "وصلت النتيجة للمراجعة" : item.status === "review" ? "تحتاج مراجعة المسؤول" : "محفوظة على الجهاز · بانتظار الإرسال";
      const note = document.createElement("p"); note.textContent = item.note;
      const pictures = document.createElement("p"); pictures.textContent = `صور التنفيذ المحفوظة: ${item.photos.length}`;
      article.append(status, note, pictures);
      const gallery = document.createElement("div"); gallery.className = "detail-gallery";
      for (const [index, photo] of item.photos.entries()) {
        const url = URL.createObjectURL(photo.blob); previews.push(url);
        const image = document.createElement("img"); image.src = url; image.alt = `صورة تنفيذ محفوظة ${index + 1}`; image.loading = "lazy";
        gallery.append(image);
      }
      if (item.photos.length) article.append(gallery);
      if (item.error) { const error = document.createElement("p"); error.textContent = item.error; article.append(error); }
      if (item.status === "review") {
        const edit = document.createElement("button"); edit.type = "button"; edit.className = "quiet-button"; edit.textContent = "مراجعة النص مع الاحتفاظ بالصور";
        edit.addEventListener("click", () => { editing = item.client_id; retainedPhotos = item.photos.map(photo => photo.blob); form.elements.note.value = item.note; form.elements.photos.value = ""; text("الصور السابقة محفوظة. يلزم فتح المهمة عند الاتصال والتحقق من الإسناد قبل اعتماد نتيجة جديدة."); form.elements.note.focus(); });
        article.append(edit);
      }
      list.append(article);
    }
    // A pending or acknowledged result blocks a second result for this prepared task revision.
    const current = items.filter(item => item.assignment_event_id === card?.assignment_event_id);
    save.disabled = !ready || current.some(item => ["pending", "synced"].includes(item.status));
    if (current.some(item => item.status === "synced")) {
      const status = document.querySelector(".page-heading > .status-pill");
      if (status) status.textContent = "بانتظار مراجعة المدير";
    }
  }
  async function sync() {
    const result = await outbox.sync();
    const messages = {offline: "النص والصور محفوظة على الجهاز. ستُرسل عند عودة الاتصال.", login: "يلزم تسجيل الدخول بالحساب نفسه لإرسال المحفوظ.", "different-account": "تغير الحساب المفتوح؛ لن تُرسل النتيجة باسم حساب آخر.", "server-error": "تعذر الرفع حاليًا؛ النتيجة والصور محفوظة.", complete: "اكتملت محاولة الإرسال. راجع حالة النتيجة أدناه."};
    if (messages[result.state]) text(messages[result.state]);
    await render();
  }
  form.addEventListener("submit", async event => {
    event.preventDefault(); save.disabled = true;
    if (!ready || !card) { text("افتح المهمة أولًا بالحساب المسؤول عند توفر الاتصال لتجهيزها."); return; }
    try {
      const fresh = Array.from(form.elements.photos.files);
      await outbox.saveWorkResult(card, form.elements.note.value, fresh.length ? fresh : retainedPhotos || [], editing);
      editing = null; retainedPhotos = null; form.reset();
      text("حُفظت النتيجة والصور على الجهاز قبل الإرسال.");
      if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
      await render();
      if (window.washRegistration?.sync) await window.washRegistration.sync.register("wash-sync").catch(() => {});
      await sync();
    } catch (error) { text(error.message || "تعذر الحفظ المحلي؛ لا تغلق النموذج."); await render(); }
  });
  panel.querySelector("[data-work-result-sync]").addEventListener("click", () => sync().catch(error => text(error.message)));
  async function prepare() {
    try {
      const embedded = document.getElementById("work-task-card");
      if (embedded) { const candidate = JSON.parse(embedded.textContent); card = await outbox.prepareWorkTask(candidate); }
      else if (document.body.dataset.shell === "true") {
        const match = location.pathname.match(/^\/staff\/tasks\/(\d+)\/$/);
        if (!match) return;
        for (const element of document.querySelectorAll("[data-intake-shell], [data-violation-shell], #local-register")) element.hidden = true;
        const taskShell = document.querySelector("[data-task-shell]"); if (taskShell) taskShell.hidden = false;
        card = await outbox.workTask(Number(match[1])); panel.hidden = false;
      }
      if (!card) { text("هذه المهمة غير مجهزة للحساب المفتوح على هذا الجهاز. افتحها عند الاتصال أولًا؛ لم يُحفظ أي محتوى خاص في ذاكرة صفحات المتصفح."); return; }
      panel.hidden = false; ready = true;
      panel.querySelector("[data-work-result-task]").textContent = `${card.reference} · ${card.title} · ${card.address}`;
      text("بطاقة المهمة مجهزة لهذا الحساب. يمكن حفظ النتيجة وصورها عند انقطاع الاتصال."); await render();
      window.washWorkResultReady = true;
    } catch (error) { text(error.message || "تعذر تجهيز حفظ المهمة على الجهاز."); }
  }
  if (window.washAppReady) prepare(); else window.addEventListener("wash-ready", prepare, {once: true});
  window.addEventListener("online", () => { if (ready) sync().catch(error => text(error.message)); });
  window.addEventListener("wash-sync-complete", () => { if (ready) render().catch(error => text(error.message)); });
})();
