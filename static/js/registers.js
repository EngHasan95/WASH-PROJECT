(function () {
  "use strict";
  const root = document.getElementById("local-register");
  if (!root || document.body.dataset.shell !== "true") return;
  const path = location.pathname;
  if (!/^\/(workspace|staff)\/complaints\/(?:\d+\/)?$/.test(path)) return;
  document.querySelector("[data-intake-shell]").hidden = true;
  root.hidden = false;
  const internal = path.startsWith("/staff/");
  const isDetail = /\/\d+\/$/.test(path);
  const title = document.getElementById("local-register-title");
  const message = document.getElementById("local-register-message");
  const target = document.getElementById("local-register-records");
  const search = document.getElementById("local-search");
  document.getElementById("local-register-search").hidden = internal || isDetail;
  title.textContent = internal ? "سجل البلاغات الداخلي" : isDetail ? "تفاصيل البلاغ" : "بلاغاتي";
  document.title = `${title.textContent} — مياه مأرب`;
  let urls = [], generation = 0;
  function textElement(tag, value, className) {
    const node = document.createElement(tag); node.textContent = value;
    if (className) node.className = className;
    return node;
  }
  function label(field, code) {
    return [...document.querySelectorAll(`#complaint-form [name="${field}"] option`)].find(option => option.value === code)?.textContent || code;
  }
  function detailURL(item) {
    // Older saved receipts predate the detail URL; their own reference identifies their detail.
    if (item.receipt?.detail_url) return item.receipt.detail_url;
    const match = /^MRB-(\d+)$/.exec(item.receipt?.reference || "");
    return match ? `/workspace/complaints/${Number(match[1])}/` : null;
  }
  function card(item) {
    const article = document.createElement("article"); article.className = "detail-panel";
    const type = item.data.other_type || label("complaint_type", item.data.complaint_type);
    article.append(textElement("h2", type));
    if (item.receipt) {
      const reference = textElement("span", item.receipt.reference, "receipt-reference"); reference.dir = "ltr";
      article.append(reference);
    }
    article.append(textElement("p", item.data.other_neighborhood || label("neighborhood", item.data.neighborhood)));
    article.append(textElement("small", item.status === "synced" ? "آخر تأكيد محفوظ: تم الاستلام · يلزم الاتصال لمعرفة الحالة الحالية" : item.status === "review" ? "محفوظ على الجهاز · يحتاج تصحيحًا في مساحتك" : "محفوظ على الجهاز · بانتظار الإرسال، لم يصدر رقم بلاغ بعد", "local-record-state"));
    if (isDetail) {
      article.append(textElement("p", item.data.description, "report-description"));
      const fields = document.createElement("dl"); fields.className = "detail-fields";
      for (const [name, value] of [["العنوان", item.data.address], ["أقرب معلم", item.data.landmark], ["مقدم البلاغ", item.data.reporter_name], ["رقم الهاتف", item.data.phone], ["رقم الاشتراك", item.data.subscription_number || "لم يُذكر"], ["رقم العداد", item.data.meter_number || "لم يُذكر"], ["خط العرض", item.data.latitude || "لم يُرفق"], ["خط الطول", item.data.longitude || "لم يُرفق"]]) {
        const row = document.createElement("div"); row.append(textElement("dt", name), textElement("dd", value)); fields.append(row);
      }
      article.append(fields);
      const gallery = document.createElement("div"); gallery.className = "detail-gallery";
      for (const [index, photo] of item.photos.entries()) {
        const link = document.createElement("a"); link.href = URL.createObjectURL(photo.blob); urls.push(link.href);
        link.target = "_blank"; link.rel = "noopener";
        const img = document.createElement("img"); img.src = link.href; img.alt = `صورة البلاغ ${index + 1}`;
        link.append(img, textElement("span", `فتح الصورة ${index + 1} بحجم كامل`)); gallery.append(link);
      }
      article.append(gallery);
      const back = document.createElement("a"); back.href = "/workspace/complaints/"; back.textContent = "← بلاغاتي"; back.className = "back-link"; article.append(back);
    } else {
      const link = document.createElement("a"); link.className = "back-link";
      link.href = detailURL(item) || "/workspace/";
      link.textContent = detailURL(item) ? "تفاصيل البلاغ ←" : "فتح مساحتي ←"; article.append(link);
    }
    return article;
  }
  async function render() {
    const current = ++generation;
    const user = await WashOutbox.identity();
    const entries = user?.role === "citizen" && !internal ? await WashOutbox.listComplaints(user.id) : [];
    if (current !== generation) return;
    urls.forEach(url => URL.revokeObjectURL(url)); urls = []; target.replaceChildren();
    if (internal) {
      message.textContent = "تعذر الوصول إلى النظام. السجل الداخلي لا يُحفظ على الجهاز؛ أعد تحميل الصفحة عند توفر الاتصال.";
      return;
    }
    if (!user || user.role !== "citizen") {
      message.textContent = "تعذر الوصول إلى النظام. يلزم تجهيز حساب المستفيد على الجهاز أولًا بوجود الإنترنت.";
      return;
    }
    message.textContent = "تعذر الوصول إلى النظام. المعروض نسخة بلاغات هذا الحساب المحفوظة على هذا الجهاز، وقد تكون الحالة قد تغيرت. يعود السجل الكامل بعد الاتصال وتحديث الصفحة.";
    const query = search.value.trim().toLocaleLowerCase().replace(/[٠-٩۰-۹]/g, digit => String("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹".indexOf(digit) % 10));
    const displayed = entries.filter(item => isDetail ? detailURL(item) === path : [item.receipt?.reference, item.data.other_type || label("complaint_type", item.data.complaint_type), item.data.other_neighborhood || label("neighborhood", item.data.neighborhood)].join(" ").toLocaleLowerCase().includes(query));
    if (!displayed.length) target.append(textElement("p", isDetail ? "تفاصيل هذا البلاغ غير محفوظة لهذا الحساب على الجهاز. افتحها عند توفر الاتصال." : query ? "لا توجد بلاغات تطابق البحث على هذا الجهاز." : "لا توجد بلاغات محفوظة لهذا الحساب على الجهاز."));
    displayed.forEach(item => target.append(card(item)));
  }
  search.addEventListener("input", () => render().catch(() => {message.textContent = "تعذر قراءة تخزين الجهاز. أعد المحاولة دون حذف بيانات المتصفح.";}));
  for (const event of ["wash-ready", "wash-sync-complete"]) window.addEventListener(event, () => render().catch(() => {message.textContent = "تعذر قراءة تخزين الجهاز. أعد المحاولة دون حذف بيانات المتصفح.";}));
  if (window.washAppReady) render().catch(() => {});
})();
