const CACHE_VERSION = "toolhub-static-v1";
const ASSET_DESTINATIONS = new Set(["style", "script", "image", "font"]);

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key)));
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  if (url.pathname.startsWith("/api")) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE_VERSION);
        try {
          const response = await fetch(request);
          if (response.ok) {
            void cache.put(request, response.clone());
          }
          return response;
        } catch {
          return (await cache.match(request)) || (await cache.match("/chat")) || Response.error();
        }
      })()
    );
    return;
  }

  if (!ASSET_DESTINATIONS.has(request.destination)) {
    return;
  }

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE_VERSION);
      const cached = await cache.match(request);

      const networkFetch = fetch(request)
        .then((response) => {
          if (response.ok) {
            void cache.put(request, response.clone());
          }
          return response;
        })
        .catch(() => undefined);

      return cached || (await networkFetch) || Response.error();
    })()
  );
});
