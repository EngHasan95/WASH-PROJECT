/* IndexedDB is the source of truth for unsent drafts. No passwords or tokens are persisted. */
(function (scope) {
  "use strict";
  const DB_NAME = "wash-device-drafts";
  let running = false;
  function database() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 4);
      request.onupgradeneeded = () => {
        for (const [store, keyPath] of [["drafts", "client_id"], ["settings", "key"], ["complaints", "client_id"], ["violations", "client_id"], ["work_tasks", "key"], ["execution_results", "client_id"]]) {
          if (!request.result.objectStoreNames.contains(store)) request.result.createObjectStore(store, {keyPath});
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("أغلق نوافذ النظام الأخرى ثم حاول مجددًا."));
    });
  }
  async function read(store, key) {
    const db = await database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(store, "readonly");
      const request = key === undefined ? transaction.objectStore(store).getAll() : transaction.objectStore(store).get(key);
      let result;
      request.onsuccess = () => { result = request.result; };
      transaction.oncomplete = () => { db.close(); resolve(result); };
      transaction.onerror = () => { db.close(); reject(transaction.error); };
      transaction.onabort = () => { db.close(); reject(transaction.error); };
    });
  }
  async function write(store, value) {
    const db = await database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(store, "readwrite");
      transaction.objectStore(store).put(value);
      transaction.oncomplete = () => { db.close(); resolve(value); };
      transaction.onerror = () => { db.close(); reject(transaction.error); };
      transaction.onabort = () => { db.close(); reject(transaction.error); };
    });
  }
  async function identity() { return (await read("settings", "active-user"))?.value || null; }
  async function setIdentity(user) {
    const minimal = user ? {id: user.id, name: user.name, phone: user.phone || "", role: user.role} : null;
    await write("settings", {key: "active-user", value: minimal});
    return minimal;
  }
  async function list(userId) {
    return (await read("drafts")).filter(draft => draft.owner_id === userId).sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
  async function save(title, description) {
    const user = await identity();
    if (!user) throw new Error("سجل الدخول أولًا عند توفر الإنترنت لتجهيز حسابك على هذا الجهاز.");
    title = title.trim(); description = description.trim();
    if (!title || title.length > 160 || !description || description.length > 5000) throw new Error("أدخل عنوانًا ووصفًا ضمن الحدود المتاحة.");
    const draft = {
      client_id: crypto.randomUUID(), owner_id: user.id,
      kind: user.role === "citizen" ? "complaint" : "violation",
      title, description, created_at: new Date().toISOString(), status: "pending", error: "",
    };
    await write("drafts", draft);
    return draft;
  }
  async function listComplaints(userId) {
    return (await read("complaints")).filter(item => item.owner_id === userId).sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
  async function listViolations(userId) {
    return (await read("violations")).filter(item => item.owner_id === userId).sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
  async function saveViolation(data, expectedOwnerId) {
    const user = await identity();
    if (!user || user.role !== "technician" || user.id !== expectedOwnerId) throw new Error("يلزم حساب الفني الذي فُتحت به هذه المساحة.");
    const item = {client_id: crypto.randomUUID(), owner_id: user.id, data,
      created_at: new Date().toISOString(), status: "pending", error: ""};
    await write("violations", item);
    return item;
  }
  async function saveComplaint(data, files, replaceId = null, expectedOwnerId = null) {
    const user = await identity();
    if (!user || user.role !== "citizen") throw new Error("يلزم حساب مستفيد مجهز على الجهاز لتسجيل البلاغ.");
    if (expectedOwnerId !== null && user.id !== expectedOwnerId) throw new Error("تغير الحساب المفتوح. أعد فتح مساحتك قبل الحفظ.");
    if (files.length > 3 || files.some(file => file.size > 5 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(file.type))) {
      throw new Error("أرفق حتى 3 صور JPEG أو PNG أو WebP، كل صورة حتى 5 ميجابايت.");
    }
    const item = {client_id: crypto.randomUUID(), owner_id: user.id, data, photos: files.map(file => ({name: file.name || "photo", blob: file})),
                  created_at: new Date().toISOString(), status: "pending", error: ""};
    const db = await database();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction("complaints", "readwrite");
      const store = transaction.objectStore("complaints");
      store.put(item);
      if (replaceId) {
        const previous = store.get(replaceId);
        previous.onsuccess = () => {
          if (previous.result?.owner_id === user.id && previous.result.status === "review") store.delete(replaceId);
        };
      }
      transaction.oncomplete = () => {db.close(); resolve();};
      transaction.onerror = transaction.onabort = () => {db.close(); reject(transaction.error);};
    });
    return item;
  }
  async function prepareWorkTask(card) {
    const user = await identity();
    if (!user || user.id !== card.owner_id || user.role !== card.owner_role || !["technician", "finance"].includes(user.role)) throw new Error("تغير حساب المهمة. أعد فتحها بالحساب المسؤول.");
    return write("work_tasks", {...card, key: `${user.id}:${card.complaint_id}`, prepared_at: new Date().toISOString()});
  }
  async function workTask(complaintId) {
    const user = await identity();
    if (!user || !["technician", "finance"].includes(user.role)) return null;
    const card = await read("work_tasks", `${user.id}:${complaintId}`);
    return card?.owner_id === user.id && card.owner_role === user.role ? card : null;
  }
  async function listWorkResults(ownerId, complaintId = null) {
    return (await read("execution_results")).filter(item => item.owner_id === ownerId && (complaintId === null || item.complaint_id === complaintId)).sort((a, b) => b.created_at.localeCompare(a.created_at));
  }
  async function saveWorkResult(card, note, files, replaceId = null) {
    const user = await identity();
    if (!user || user.id !== card.owner_id || user.role !== card.owner_role || !["technician", "finance"].includes(user.role)) throw new Error("تغير الحساب. لا يمكن حفظ نتيجة باسم موظف آخر.");
    note = note.trim();
    if (!note || note.length > 3000) throw new Error("دوّن نتيجة المعالجة في حدود 3000 حرف.");
    if (files.length > 3 || files.some(file => file.size > 5 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(file.type))) throw new Error("أرفق حتى 3 صور JPEG أو PNG أو WebP، كل صورة حتى 5 ميجابايت.");
    const item = {client_id: crypto.randomUUID(), owner_id: user.id, owner_role: user.role, complaint_id: card.complaint_id,
      assignment_event_id: card.assignment_event_id, note, photos: files.map(file => ({name: file.name || "execution", blob: file})),
      created_at: new Date().toISOString(), status: "pending", error: ""};
    const db = await database();
    await new Promise((resolve, reject) => {
      const tx = db.transaction("execution_results", "readwrite"); const target = tx.objectStore("execution_results");
      target.put(item);
      if (replaceId) { const previous = target.get(replaceId); previous.onsuccess = () => {
        if (previous.result?.owner_id === user.id && previous.result.complaint_id === card.complaint_id && previous.result.status === "review") target.delete(replaceId);
      }; }
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onerror = tx.onabort = () => { db.close(); reject(tx.error); };
    });
    return item;
  }
  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      return await fetch(url, {...options, credentials: "same-origin", cache: "no-store", signal: controller.signal});
    } finally { clearTimeout(timeout); }
  }
  async function currentSession() {
    const response = await request("/api/session/");
    if (response.status === 401 || response.status === 403) return null;
    if (!response.ok) throw new Error("تعذر الاتصال بالنظام.");
    return response.json();
  }
  async function performSync() {
    const localUser = await identity();
    if (!localUser) return {state: "login", sent: 0};
    let session;
    try { session = await currentSession(); } catch (_) { return {state: "offline", sent: 0}; }
    if (!session) return {state: "login", sent: 0};
    if (session.id !== localUser.id || session.role !== localUser.role) return {state: "different-account", sent: 0};
    let sent = 0;
    for (const draft of await list(localUser.id)) {
      if (draft.status !== "pending") continue;
      // Recheck the active local identity between sends in case another tab logged out or switched accounts.
      const active = await identity();
      if (!active || active.id !== localUser.id) return {state: "different-account", sent};
      let response;
      try {
        response = await request("/api/drafts/sync/", {
          method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": session.csrf_token},
          body: JSON.stringify({owner_id: draft.owner_id, client_id: draft.client_id, kind: draft.kind, title: draft.title, description: draft.description}),
        });
      } catch (_) { return {state: "offline", sent}; }
      if (response.status === 401 || response.status === 403) return {state: "login", sent};
      if (response.status === 409) {
        const conflict = await response.json();
        if (conflict.error === "account_changed") return {state: "different-account", sent};
      }
      if (response.status >= 500) return {state: "server-error", sent};
      if (!response.ok) {
        draft.status = "review"; draft.error = "لم يقبل النظام المسودة. احتفظنا بها على الجهاز للمراجعة.";
        await write("drafts", draft);
        continue;
      }
      const acknowledgment = await response.json();
      if (acknowledgment.saved !== true || acknowledgment.client_id !== draft.client_id) return {state: "server-error", sent};
      draft.status = "synced"; draft.synced_at = new Date().toISOString();
      await write("drafts", draft); sent += 1;
    }
    if (localUser.role === "citizen") for (const item of await listComplaints(localUser.id)) {
      if (item.status !== "pending") continue;
      const active = await identity();
      if (!active || active.id !== localUser.id || active.role !== localUser.role) return {state: "different-account", sent};
      const body = new FormData();
      body.append("payload", JSON.stringify({...item.data, client_id: item.client_id, owner_id: item.owner_id}));
      for (const photo of item.photos) body.append("photos", photo.blob, photo.name);
      let response;
      try {
        response = await request("/api/complaints/sync/", {method: "POST", headers: {"X-CSRFToken": session.csrf_token}, body});
      } catch (_) {return {state: "offline", sent};}
      if (response.status === 401 || response.status === 403) return {state: "login", sent};
      if (response.status >= 500) return {state: "server-error", sent};
      let acknowledgment;
      try {acknowledgment = await response.json();} catch (_) {return {state: "server-error", sent};}
      if (response.status === 409 && acknowledgment.error === "account_changed") return {state: "different-account", sent};
      if (!response.ok) {
        item.status = "review";
        const fields = Object.values(acknowledgment.fields || {}).flat().map(error => error.message).join(" · ");
        item.error = acknowledgment.message || fields || "لم يقبل النظام البلاغ. راجع بياناته ثم احفظه مجددًا.";
        await write("complaints", item); continue;
      }
      if (acknowledgment.saved !== true || acknowledgment.client_id !== item.client_id || !/^MRB-\d+$/.test(acknowledgment.reference)) {
        return {state: "server-error", sent};
      }
      item.status = "synced"; item.receipt = acknowledgment; item.synced_at = new Date().toISOString();
      await write("complaints", item); sent += 1;
    }
    if (localUser.role === "technician") for (const item of await listViolations(localUser.id)) {
      if (item.status !== "pending") continue;
      const active = await identity();
      if (!active || active.id !== localUser.id || active.role !== localUser.role) return {state: "different-account", sent};
      let response;
      try {
        response = await request("/api/violations/sync/", {method: "POST",
          headers: {"Content-Type": "application/json", "X-CSRFToken": session.csrf_token},
          body: JSON.stringify({...item.data, owner_id: item.owner_id, client_id: item.client_id})});
      } catch (_) {return {state: "offline", sent};}
      if (response.status === 401 || response.status === 403) return {state: "login", sent};
      if (response.status >= 500) return {state: "server-error", sent};
      let acknowledgment;
      try {acknowledgment = await response.json();} catch (_) {return {state: "server-error", sent};}
      if (response.status === 409 && acknowledgment.error === "account_changed") return {state: "different-account", sent};
      if (!response.ok) {
        item.status = "review";
        item.error = Object.values(acknowledgment.fields || {}).flat().map(error => error.message).join(" · ") || "راجع بيانات المخالفة في النظام.";
        await write("violations", item); continue;
      }
      if (acknowledgment.saved !== true || acknowledgment.client_id !== item.client_id || !/^V-MRB-\d+$/.test(acknowledgment.reference)) return {state: "server-error", sent};
      item.status = "synced"; item.receipt = acknowledgment; item.synced_at = new Date().toISOString();
      await write("violations", item); sent += 1;
    }
    if (["technician", "finance"].includes(localUser.role)) for (const item of await listWorkResults(localUser.id)) {
      if (item.status !== "pending" || item.owner_role !== localUser.role) continue;
      const active = await identity();
      if (!active || active.id !== localUser.id || active.role !== localUser.role) return {state: "different-account", sent};
      const body = new FormData();
      body.append("payload", JSON.stringify({owner_id: item.owner_id, client_id: item.client_id, complaint_id: item.complaint_id,
        assignment_event_id: item.assignment_event_id, note: item.note}));
      for (const photo of item.photos) body.append("photos", photo.blob, photo.name);
      let response;
      try { response = await request("/api/work-results/sync/", {method: "POST", headers: {"X-CSRFToken": session.csrf_token}, body}); }
      catch (_) { return {state: "offline", sent}; }
      if (response.status === 401 || response.status === 403) return {state: "login", sent};
      if (response.status >= 500) return {state: "server-error", sent};
      let acknowledgment;
      try { acknowledgment = await response.json(); } catch (_) { return {state: "server-error", sent}; }
      if (response.status === 409 && acknowledgment.error === "account_changed") return {state: "different-account", sent};
      if (!response.ok) {
        item.status = "review"; item.error = acknowledgment.message || "تحتاج النتيجة إلى مراجعة. احتفظنا بالنص والصور على الجهاز.";
        await write("execution_results", item); continue;
      }
      if (acknowledgment.saved !== true || acknowledgment.client_id !== item.client_id || acknowledgment.complaint_id !== item.complaint_id) return {state: "server-error", sent};
      item.status = "synced"; item.receipt = acknowledgment; item.synced_at = new Date().toISOString();
      await write("execution_results", item); sent += 1;
    }
    return {state: "complete", sent};
  }
  async function sync() {
    if (running) return {state: "busy", sent: 0};
    running = true;
    try {
      if (scope.navigator?.locks) {
        return await scope.navigator.locks.request("wash-outbox-sync", performSync);
      }
      return await performSync();
    } finally { running = false; }
  }
  scope.WashOutbox = {identity, setIdentity, list, save, listComplaints, saveComplaint, listViolations, saveViolation, sync, currentSession, prepareWorkTask, workTask, listWorkResults, saveWorkResult};
})(globalThis);
