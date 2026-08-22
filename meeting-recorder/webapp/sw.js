/* Service worker: caches the app shell so the recorder opens with no network.
 *
 * Deliberately narrow. It never touches /session, /chunk or /finish -- an upload
 * that quietly served from cache would report success for audio that never left
 * the phone. Retrying those is app.js's IndexedDB queue's job, not the cache's.
 */
const CACHE = 'asb-recorder-v1';
const SHELL = ['./', 'index.html', 'app.js', 'manifest.json', 'icon.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  const isShell = e.request.method === 'GET'
    && url.origin === location.origin
    && !['/session', '/chunk', '/finish', '/abort', '/health'].includes(url.pathname);
  if (!isShell) return;

  // Network first, so a new build lands as soon as the host is reachable;
  // cache is the fallback that makes the app open at all when it is not.
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match('index.html')))
  );
});
