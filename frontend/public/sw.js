/* EnglishFlow PWA service worker.
 *
 * Deliberately conservative: the only thing worth caching offline is the shell
 * and small JSON (transcripts, course lists, stats). Media is 15-47 MB per
 * lesson and should be cached explicitly by the app (offline download), never
 * silently by a service worker — one careless `cache.put` on an audio range
 * response can fill the device up.
 */

const SHELL = 'ef-shell-v1';
const DATA = 'ef-data-v1';
const SHELL_ASSETS = ['/', '/m', '/manifest.webmanifest', '/pwa-icon.svg'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(SHELL_ASSETS).catch(() => undefined)),
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== SHELL && k !== DATA).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

/** Endpoints whose JSON answers are worth keeping when the network is gone. */
const CACHEABLE_API = [/\/api\/(v1\/)?english\/(home|transcript\/\d+|lesson\/\d+|courses|filters|stats)/];

const isMedia = (url) => /\/api\/(v1\/)?(media|subtitle|stream)\//.test(url.pathname);

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Audio/video must reach the network; a cached 206 would break seeking.
  if (isMedia(url)) return;
  // Never cache anything that carries a credential in the query string.
  if (url.searchParams.has('token')) return;

  if (url.pathname.startsWith('/api/')) {
    if (!CACHEABLE_API.some((re) => re.test(url.pathname))) return;
    event.respondWith(networkFirstJson(request));
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(navigationFallback(request));
    return;
  }

  event.respondWith(staleWhileRevalidate(request));
});

async function networkFirstJson(request) {
  const cache = await caches.open(DATA);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(SHELL);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || network;
}

async function navigationFallback(request) {
  try {
    return await fetch(request);
  } catch (err) {
    const cache = await caches.open(SHELL);
    const cached = (await cache.match('/m')) || (await cache.match('/'));
    if (cached) return cached;
    throw err;
  }
}
