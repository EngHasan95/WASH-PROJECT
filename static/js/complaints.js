(function () {
  "use strict";
  const form = document.getElementById("complaint-form");
  if (!form) return;
  const outbox = window.WashOutbox;
  const saveButton = document.getElementById("save-complaint");
  const feedback = document.getElementById("complaint-feedback");
  const syncFeedback = document.getElementById("complaint-sync-feedback");
  const photoInput = document.getElementById("complaint-photos");
  const fields = ["reporter_name", "phone", "subscription_number", "meter_number", "complaint_type", "other_type", "neighborhood", "other_neighborhood", "address", "landmark", "description", "latitude", "longitude"];
  let selectedPhotos = [], previewURLs = [], ledgerURLs = [], editingId = null, lastSavedId = null, user = null, boundOwner = null, rendering = 0, syncing = false, submitting = false;
  function setOtherFields() {
    for (const [choice, other, wrapper] of [["complaint_type", "other_type", "other-type-field"], ["neighborhood", "other_neighborhood", "other-neighborhood-field"]]) {
      const enabled = form.elements[choice].value === "other";
      document.getElementById(wrapper).hidden = !enabled;
      form.elements[other].required = enabled;
      if (!enabled) form.elements[other].value = "";
    }
  }
  form.elements.complaint_type.addEventListener("change", setOtherFields);
  form.elements.neighborhood.addEventListener("change", setOtherFields);
  function showPhotoPreview() {
    previewURLs.forEach(url => URL.revokeObjectURL(url)); previewURLs = [];
    const target = document.getElementById("photo-preview"); target.replaceChildren();
    selectedPhotos.forEach((photo, index) => {
      const article = document.createElement("div"); article.className = "preview-photo";
      const image = document.createElement("img"); image.alt = `الصورة التوضيحية ${index + 1}`;
      image.src = URL.createObjectURL(photo); previewURLs.push(image.src);
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "إزالة الصورة";
      remove.addEventListener("click", () => {selectedPhotos.splice(index, 1); showPhotoPreview();});
      article.append(image, remove); target.append(article);
    });
  }
  photoInput.addEventListener("change", () => {
    const incoming = [...photoInput.files]; photoInput.value = "";
    const message = document.getElementById("photo-feedback");
    if (selectedPhotos.length + incoming.length > 3 || incoming.some(photo => photo.size > 5 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(photo.type))) {
      message.textContent = "اختر حتى 3 صور JPEG أو PNG أو WebP، لا تتجاوز كل صورة 5 ميجابايت."; return;
    }
    selectedPhotos.push(...incoming); message.textContent = ""; showPhotoPreview();
  });
  const latitude = form.elements.latitude, longitude = form.elements.longitude;
  document.getElementById("capture-location").addEventListener("click", () => {
    const button = document.getElementById("capture-location"), message = document.getElementById("location-feedback");
    if (!navigator.geolocation) {message.textContent = "تحديد الموقع غير متاح. يمكنك تسجيل البلاغ بوصف العنوان والمعلم."; return;}
    button.disabled = true; message.textContent = "جارٍ تحديد الموقع…";
    navigator.geolocation.getCurrentPosition(position => {
      latitude.value = position.coords.latitude.toFixed(7); longitude.value = position.coords.longitude.toFixed(7);
      message.textContent = "أُرفق موقعك الحالي. تأكد أنك في موقع المشكلة.";
      document.getElementById("clear-location").hidden = false; button.disabled = false;
    }, () => {
      message.textContent = "تعذر تحديد الموقع أو لم يُسمح به. أكمل البلاغ بالعنوان والمعلم."; button.disabled = false;
    }, {enableHighAccuracy: true, timeout: 10000, maximumAge: 0});
  });
  document.getElementById("clear-location").addEventListener("click", () => {
    latitude.value = longitude.value = "";
    document.getElementById("location-feedback").textContent = ""; document.getElementById("clear-location").hidden = true;
  });
  function timestamp(target, value) {
    const time = document.createElement("time"); time.dateTime = value;
    time.textContent = new Intl.DateTimeFormat("ar-YE", {dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Aden"}).format(new Date(value));
    target.append(time);
  }
  function receiptCard(receipt) {
    const article = document.createElement("article"); article.className = "complaint-item"; article.dataset.state = "synced";
    const heading = document.createElement("h3"); heading.textContent = receipt.other_type || receipt.complaint_type;
    const reference = document.createElement("span"); reference.className = "receipt-reference"; reference.dir = "ltr"; reference.textContent = receipt.reference;
    const place = document.createElement("p"); place.textContent = receipt.other_neighborhood || receipt.neighborhood;
    const state = document.createElement("small"); state.textContent = receipt.status_label;
    article.append(heading, reference, place, state); timestamp(article, receipt.received_at);
    if (receipt.detail_url) {
      const link = document.createElement("a"); link.href = receipt.detail_url;
      link.className = "back-link"; link.textContent = "تفاصيل البلاغ ←"; article.append(link);
    }
    return article;
  }
  async function editReview(clientId) {
    const identity = await outbox.identity();
    if (!identity || identity.id !== user?.id) return;
    const item = (await outbox.listComplaints(identity.id)).find(entry => entry.client_id === clientId && entry.status === "review");
    if (!item) return;
    for (const name of fields) form.elements[name].value = item.data[name] || "";
    selectedPhotos = item.photos.map(photo => new File([photo.blob], photo.name, {type: photo.blob.type}));
    editingId = clientId; lastSavedId = null; setOtherFields(); showPhotoPreview();
    document.getElementById("clear-location").hidden = !latitude.value;
    feedback.textContent = "أعدنا البيانات والصور إلى النموذج. صححها ثم احفظ البلاغ مجددًا.";
    form.scrollIntoView({behavior: "auto", block: "start"});
  }
  async function renderLocal() {
    const generation = ++rendering, identity = await outbox.identity();
    const entries = identity?.role === "citizen" ? await outbox.listComplaints(identity.id) : [];
    if (generation !== rendering) return;
    if (boundOwner !== null && identity?.id !== boundOwner) {
      saveButton.disabled = true; user = null;
      feedback.textContent = "تغير الحساب المفتوح. أعد فتح مساحتك قبل تسجيل بلاغ جديد.";
      return;
    }
    user = identity?.role === "citizen" ? identity : null;
    if (user) boundOwner = user.id;
    for (const section of document.querySelectorAll("[data-beneficiary-workspace]")) section.hidden = !user;
    saveButton.disabled = !user || submitting;
    if (user) {
      if (!form.elements.reporter_name.value) form.elements.reporter_name.value = user.name || "";
      if (!form.elements.phone.value) form.elements.phone.value = user.phone || "";
    }
    ledgerURLs.forEach(url => URL.revokeObjectURL(url)); ledgerURLs = [];
    const target = document.getElementById("complaint-list"); target.replaceChildren();
    const connected = navigator.onLine && document.getElementById("connection-status").dataset.state === "connected";
    const displayed = connected ? entries.filter(item => item.status !== "synced") : entries;
    document.getElementById("complaint-count").textContent = new Intl.NumberFormat("ar-YE").format(displayed.length);
    if (!displayed.length) target.textContent = entries.length ? "وصلت بلاغات هذا الجهاز. تجد أرقامها في إيصالات الاستلام أدناه." : "بلاغاتك المحفوظة على هذا الجهاز ستظهر هنا.";
    const last = entries.find(item => item.client_id === lastSavedId);
    if (last?.status === "synced") feedback.textContent = `تم استلام بلاغك في النظام. رقم البلاغ: ${last.receipt.reference}.`;
    else if (last?.status === "pending" && !connected) feedback.textContent = "حُفظ البلاغ وصوره على الجهاز. سيُرسل عند توفر الاتصال وفتح النظام.";
    for (const item of displayed) {
      let article;
      if (item.status === "synced" && item.receipt) article = receiptCard(item.receipt);
      else {
        article = document.createElement("article"); article.className = "complaint-item"; article.dataset.state = item.status;
        const heading = document.createElement("h3"); heading.textContent = item.data.other_type || [...form.elements.complaint_type.options].find(option => option.value === item.data.complaint_type)?.textContent || "بلاغ محفوظ";
        const description = document.createElement("p"); description.textContent = item.data.description;
        const state = document.createElement("small"); state.textContent = item.status === "review" ? "محفوظ على الجهاز · يحتاج تصحيحًا" : "محفوظ على الجهاز · بانتظار الإرسال، لم يصدر رقم بلاغ بعد";
        article.append(heading, description, state); timestamp(article, item.created_at);
        if (item.status === "review") {
          const error = document.createElement("p"); error.textContent = item.error; article.append(error);
          const edit = document.createElement("button"); edit.type = "button"; edit.className = "quiet-button"; edit.textContent = "تصحيح البيانات";
          edit.addEventListener("click", () => editReview(item.client_id).catch(showError)); article.append(edit);
        }
      }
      const images = document.createElement("div"); images.className = "receipt-photos";
      for (const [index, photo] of item.photos.entries()) {
        const image = document.createElement("img"); image.alt = `صورة البلاغ ${index + 1}`;
        image.src = URL.createObjectURL(photo.blob); ledgerURLs.push(image.src); images.append(image);
      }
      if (images.childElementCount) article.append(images); target.append(article);
    }
  }
  async function renderReceipts() {
    const target = document.getElementById("complaint-receipts");
    if (!user) {target.replaceChildren(); return;}
    if (!navigator.onLine) {target.textContent = "إيصالات الجهاز محفوظة. يعود عرض إيصالات النظام عند الاتصال."; return;}
    try {
      const expectedOwner = user.id;
      const response = await fetch(`/api/complaints/?owner_id=${expectedOwner}`, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) {target.textContent = "سجّل الدخول عند توفر الإنترنت لعرض إيصالاتك."; return;}
      const data = await response.json();
      if (!user || user.id !== expectedOwner) return;
      target.replaceChildren();
      if (!data.complaints.length) target.textContent = "لا توجد إيصالات استلام لهذا الحساب بعد.";
      for (const receipt of data.complaints) {
        const article = receiptCard(receipt);
        const images = document.createElement("div"); images.className = "receipt-photos";
        for (const [index, photo] of receipt.photos.entries()) {
          const link = document.createElement("a"); link.href = photo.url; link.target = "_blank"; link.rel = "noopener";
          const image = document.createElement("img"); image.src = photo.url; image.alt = `عرض صورة البلاغ ${index + 1}`; image.loading = "lazy";
          link.append(image); images.append(link);
        }
        if (images.childElementCount) article.append(images); target.append(article);
      }
    } catch (_) {target.textContent = "تعذر تحميل إيصالات النظام. بلاغات جهازك وصورها باقية.";}
  }
  function showError(error) {feedback.textContent = error.message || "تعذر الحفظ. أبقِ النموذج مفتوحًا وأعد المحاولة.";}
  async function refresh() {await renderLocal(); await renderReceipts();}
  async function syncNow() {
    if (syncing) return; syncing = true;
    try {
      const result = await outbox.sync();
      const messages = {complete: "تم التحقق من الإرسال. يظهر رقم البلاغ فقط بعد تأكيد استلامه.", offline: "الاتصال منقطع. تبقى البيانات والصور على الجهاز حتى عودة الاتصال.", login: "يلزم تسجيل الدخول عند توفر الإنترنت لإرسال بلاغاتك المحفوظة.", "different-account": "تغير الحساب. تبقى البلاغات محفوظة لأصحابها ولا ترسل باسم حساب آخر.", "server-error": "تعذر الحفظ في النظام. لم نحذف البيانات أو الصور من الجهاز."};
      if (messages[result.state]) syncFeedback.textContent = messages[result.state];
      if (result.state !== "busy") {
        const connection = document.getElementById("connection-status");
        const offline = ["offline", "server-error"].includes(result.state);
        connection.dataset.state = offline ? "offline" : "connected";
        connection.textContent = offline ? "الاتصال منقطع · البيانات والصور تحفظ على الجهاز" : "متصل بالنظام";
      }
      await refresh();
    } finally {syncing = false;}
  }
  // Attach before awaiting session preparation, preventing a native submit/URL leak.
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submitting) return;
    if (!user) {feedback.textContent = "يلزم أول دخول بوجود الإنترنت لتجهيز حساب المستفيد."; return;}
    if (!form.reportValidity()) return;
    const expectedOwner = user.id, data = Object.fromEntries(fields.map(name => [name, form.elements[name].value.trim()]));
    submitting = true; saveButton.disabled = true;
    try {
      const saved = await outbox.saveComplaint(data, selectedPhotos, editingId, expectedOwner);
      lastSavedId = saved.client_id;
      if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
      form.reset(); selectedPhotos = []; editingId = null; setOtherFields(); showPhotoPreview();
      document.getElementById("location-feedback").textContent = ""; document.getElementById("clear-location").hidden = true;
      feedback.textContent = "حُفظ البلاغ وصوره على الجهاز. جارٍ محاولة إرساله إلى النظام.";
      await renderLocal();
      if (window.washRegistration?.sync) await window.washRegistration.sync.register("wash-sync").catch(() => {});
      await syncNow();
    } catch (error) {showError(error);}
    finally {submitting = false; saveButton.disabled = !user;}
  });
  document.getElementById("sync-complaints").addEventListener("click", () => syncNow().catch(showError));
  window.addEventListener("wash-sync-complete", () => refresh().catch(showError));
  window.addEventListener("online", () => syncNow().catch(showError));
  window.addEventListener("offline", () => refresh().catch(showError));
  document.addEventListener("visibilitychange", () => {if (document.visibilityState === "visible" && user) syncNow().catch(showError);});
  navigator.serviceWorker?.addEventListener("message", () => refresh().catch(showError));
  setInterval(() => {if (document.visibilityState === "visible" && user && navigator.onLine) syncNow().catch(showError);}, 30000);
  setOtherFields();
  if (window.washAppReady) refresh().catch(showError);
  else window.addEventListener("wash-ready", () => refresh().catch(showError), {once: true});
})();
