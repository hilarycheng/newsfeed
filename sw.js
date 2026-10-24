const ROOT = new URL(self.registration.scope);
const PREFIX = 'newsfeed:' + ROOT.href + ':';
const CACHE = PREFIX + 'bb04c193065e120b254c34bdfdd0b0b1c1c0b0405b209c36a71ad1de4c0be303';
const INDEX = new URL('index.html', ROOT).href;
const ASSETS = ['index.html', 'manifest.webmanifest', 'icon-192.png', 'icon-512.png', 'apple-touch-icon.png'].map(path => new URL(path, ROOT).href);

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll(ASSETS.map(url => new Request(url, { cache: 'reload' })));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    await Promise.all((await caches.keys()).filter(key => key.startsWith(PREFIX) && key !== CACHE).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

async function readEdition(request) {
  const cache = await caches.open(CACHE);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(request, { cache: 'no-store', signal: controller.signal });
    if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) throw new Error('Edition unavailable');
    // A dropped body must fall back before it can replace the last complete edition.
    await response.clone().arrayBuffer();
    clearTimeout(timer);
    // Storage limits must not prevent reading a successful network response.
    await cache.put(INDEX, response.clone()).catch(() => {});
    return response;
  } catch {
    return await cache.match(INDEX) || Response.error();
  } finally {
    clearTimeout(timer);
  }
}

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== ROOT.origin) return;
  if (request.mode === 'navigate' && [ROOT.pathname, new URL(INDEX).pathname].includes(url.pathname)) {
    event.respondWith(readEdition(request));
  } else if (ASSETS.slice(1).includes(url.href)) {
    event.respondWith(caches.open(CACHE).then(async cache => await cache.match(request) || fetch(request)));
  }
});
