/* W5: minimal service worker — installability + resilience for static assets.
   Network-first for everything (no stale listings); only successful same-origin
   GET responses populate the runtime cache. API and cross-origin: untouched. */
var CACHE = "ev-static-v1";
self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return c.addAll(["/", "/style.css", "/app.js", "/theme.js", "/favicon.svg", "/logo-512.png"]);
  }));
  self.skipWaiting();
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }));
  self.clients.claim();
});
self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(e.request).then(function (resp) {
      if (resp && resp.ok && resp.type === "basic") {
        var copy = resp.clone();
        caches.open(CACHE).then(function (c) { c.put(e.request, copy); });
      }
      return resp;
    }).catch(function () { return caches.match(e.request); })
  );
});
