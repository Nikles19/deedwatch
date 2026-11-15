/* Deedwatch service worker: makes the site installable, opens fast, and shows the last data when offline. */
const CACHE = "deedwatch-v1";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png", "./icons/icon-512.png"];
const LIBS = ["unpkg.com", "fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET") return;
  if (LIBS.includes(url.hostname)) {           // versioned libraries and fonts: cache first
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(r => {
      if (r.ok || r.type === "opaque") { const copy = r.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return r;
    })));
    return;
  }
  if (url.origin !== self.location.origin) return;  // map tiles and county data go straight to the network
  // the app and its data: network first so it's always current, cached copy when offline
  e.respondWith(fetch(req).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
    return r;
  }).catch(() => caches.match(req, {ignoreSearch: req.mode === "navigate"}).then(hit => hit || caches.match("./index.html"))));
});
