// Service worker: makes the app installable and shows a friendly page when there is no connection.
//
// Network first, always: pages, scripts and styles come from the server whenever it can be reached,
// so a newly deployed version is used straight away. Copies are kept only as a fallback for when the
// connection drops. The API (live school data) is never cached.
const CACHE = "school-shell-v3";
const OFFLINE = new URL("static/offline.html", self.registration.scope).href;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.add(OFFLINE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin || url.pathname.includes("/api/")) return;
  event.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (res.ok && url.pathname.includes("/static/")) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    } catch (err) {
      // A page without its live data is no use, so a failed page load shows the offline screen.
      if (req.mode === "navigate") return caches.match(OFFLINE);
      const cached = await caches.match(req);
      if (cached) return cached;
      throw err;
    }
  })());
});
