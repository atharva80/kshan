// Makes the page installable ("Install app" / "Add to Home screen"). It caches nothing and
// passes every request straight to the network, so it can never serve stale data or get in
// the way of uploads. Its scope is /vsearch/, so Immich's own pages are untouched.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
