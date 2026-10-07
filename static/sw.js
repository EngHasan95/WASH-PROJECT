/* Cache only the public shell and local assets. Authenticated HTML/API/file responses never enter CacheStorage. */
importScripts("/static/js/outbox.js");
const SHELL_CACHE = "wash-shell-__SHELL_VERSION__";
const SHELL_FILES = ["/app-shell/", "/static/css/app.css", "/static/css/identity.css", "/static/js/outbox.js", "/static/js/app.js", "/static/js/complaints.js", "/static/js/registers.js", "/static/js/violations.js", "/static/js/work-results.js", "/static/brand/institution-logo.png", "/static/icons/water.svg", "/static/icons/ui.svg", "/static/icons/network.svg", "/static/icons/flow.svg", "/static/manifest.webmanifest", "/static/fonts/NotoSansArabic-Regular.ttf", "/static/fonts/NotoSansArabic-Bold.ttf", "/static/fonts/NotoKufiArabic-Bold.ttf"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(SHELL_CACHE).then(cache => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith("wash-shell-") && key !== SHELL_CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || event.request.method !== "GET") return;
  if (SHELL_FILES.includes(url.pathname)) {
    event.respondWith(caches.open(SHELL_CACHE).then(async cache => (await cache.match(url.pathname)) || fetch(event.request)));
  } else if (event.request.mode === "navigate") {
    // Keep the requested workspace URL. Offline is a state inside the usual page.
    event.respondWith((async () => {
      if (self.navigator.onLine === false) return caches.match("/app-shell/");
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2000);
      try {
        return await fetch(event.request, {signal: controller.signal});
      } catch (_) {
        return caches.match("/app-shell/");
      } finally {
        clearTimeout(timeout);
      }
    })());
  }
});
self.addEventListener("sync", event => {
  if (event.tag !== "wash-sync") return;
  event.waitUntil((async () => {
    const result = await self.WashOutbox.sync();
    for (const client of await self.clients.matchAll()) client.postMessage({type: "draft-sync", result});
    if (["offline", "server-error"].includes(result.state)) throw new Error("Connection not ready; keep drafts and retry.");
  })());
});
