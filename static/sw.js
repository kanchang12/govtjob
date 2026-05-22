const CACHE = 'govtprep-v2';
const STATIC = ['/', '/static/css/style.css', '/manifest.json'];

self.addEventListener('install', e =>
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting()))
);

self.addEventListener('activate', e =>
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()))
);

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/api/')) return; // never cache API calls
  e.respondWith(
    fetch(e.request)
      .then(r => {
        const cl = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, cl));
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
