const CACHE_NAME = 'duka-v9';
const OFFLINE_URL = '/offline/';

const PRECACHE_URLS = [
  // '/' intentionally omitted — it's login-gated and can't be safely precached
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/offline/',
];

// Helper: only cache successful (2xx) non-redirected responses.
// Never cache a redirected response — if the server redirected to /accounts/login/,
// caching the login page at the original URL would corrupt the cache.
function cacheOkResponse(cache, request, response) {
  if (response.ok && !response.redirected) {
    cache.put(request, response.clone());
  }
  return response;
}

// Install: precache core shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// Activate: clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// Fetch: network-first for navigations, cache-first for static assets
self.addEventListener('fetch', event => {
  const { request } = event;

  // Skip non-GET and cross-origin
  if (request.method !== 'GET') return;

  // Static assets: cache-first
  if (request.url.includes('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(c => c.put(request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // HTML navigations: network-first with offline fallback.
  // Never cache if the response was redirected (e.g. auth redirect to login page
  // would get stored at the original URL, corrupting the cache).
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(response => {
          if (response.ok && !response.redirected) {
            const cachePromise = caches.open(CACHE_NAME).then(cache => {
              cache.put(request, response.clone());
            });
            event.waitUntil(cachePromise);
          }
          return response;
        })
        .catch(() => caches.match(request).then(c => c || caches.match(OFFLINE_URL)))
    );
    return;
  }

  // API/JSON + dynamic app endpoints: network-first, no caching
  if (
    request.url.includes('/api/') ||
    request.url.includes('/bar/tabs/') ||
    request.url.includes('/bar/shift/') ||
    request.url.includes('/kitchen/shift/') ||
    request.url.includes('/kitchen/consumable/') ||
    request.url.includes('/stock/bar/board/') ||
    request.url.includes('/stock/produce/board/') ||
    request.url.includes('/notifications/') ||
    request.url.includes('/r/') ||         // live receipt status endpoint
    request.headers.get('accept')?.includes('application/json')
  ) {
    event.respondWith(
      fetch(request).catch(() => caches.match(request))
    );
    return;
  }

  // Everything else: stale-while-revalidate.
  // Guard: don't cache redirected responses (would store login page at wrong URL).
  event.respondWith(
    caches.match(request).then(cached => {
      const fetched = fetch(request).then(response => {
        if (response.ok && !response.redirected) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(c => c.put(request, clone));
        }
        return response;
      }).catch(() => cached);
      return cached || fetched;
    })
  );
});
