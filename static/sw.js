self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open('inventory-app-v1').then(function(cache) {
      return cache.addAll([
        '/',
        '/static/manifest.json',
        // Add more URLs to cache as needed
      ]);
    })
  );
});

self.addEventListener('fetch', function(event) {
  event.respondWith(
    caches.match(event.request).then(function(response) {
      return response || fetch(event.request);
    })
  );
});
