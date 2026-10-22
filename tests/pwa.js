(async () => {
  const frame = document.getElementById('reader');
  const step = name => fetch('/progress?step=' + encodeURIComponent(name));
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const until = async check => {
    const end = Date.now() + 10000;
    while (Date.now() < end) {
      if (await check()) return;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    throw new Error('Timed out: ' + check.toString());
  };
  const navigate = path => new Promise(resolve => { frame.onload = resolve; frame.src = path; });
  const mode = (state, version = 1) => fetch('/control?mode=' + state + '&version=' + version);
  const edition = version => assert(frame.contentDocument.body.textContent.includes('PWA fixture ' + version), 'Wrong edition ' + version);
  const appCaches = async () => (await caches.keys()).filter(key => key.startsWith('newsfeed:' + location.origin + '/newsfeed/:'));
  try {
    await step('initial navigation');
    await caches.open('another-app:keep');
    await navigate('/newsfeed/');
    edition(1);
    await step('service worker ready');
    await frame.contentWindow.navigator.serviceWorker.ready;
    // Keep the registration in the harness realm; iframe navigation destroys its old realm.
    const registration = await navigator.serviceWorker.getRegistration('/newsfeed/');
    await until(() => frame.contentWindow.navigator.serviceWorker.controller);
    assert(registration.scope === location.origin + '/newsfeed/', 'Wrong Pages scope');
    const manifestLink = frame.contentDocument.querySelector('link[rel=manifest]');
    const manifest = await (await fetch(manifestLink.href)).json();
    assert(manifest.id === './' && manifest.scope === './' && manifest.start_url === './', 'Non-portable manifest');
    assert(manifest.display === 'standalone', 'Not standalone');
    for (const [name, size] of [['icon-192.png', 192], ['icon-512.png', 512], ['apple-touch-icon.png', 180]]) {
      const icon = new Image();
      icon.src = '/newsfeed/' + name;
      await icon.decode();
      assert(icon.naturalWidth === size && icon.naturalHeight === size, 'Wrong icon dimensions');
    }
    const initialCache = (await appCaches())[0];
    assert((await (await caches.open(initialCache)).keys()).length === 5, 'Unexpected cached assets');
    const install = frame.contentDocument.getElementById('install-app');
    const prompt = new frame.contentWindow.Event('beforeinstallprompt', { cancelable: true });
    let prompted = false;
    prompt.prompt = async () => { prompted = true; };
    frame.contentWindow.dispatchEvent(prompt);
    assert(!install.hidden && prompt.defaultPrevented, 'Install control not shown');
    install.click();
    assert(prompted && install.hidden, 'Install control did not invoke browser prompt');

    await step('offline navigation');
    await mode('offline');
    await navigate('/newsfeed/index.html?offline=1');
    edition(1);
    frame.contentDocument.querySelector('[data-feed-filter=cve]').click();
    assert(frame.contentDocument.querySelectorAll('article.story').length > 0, 'Offline filters failed');
    assert((await (await caches.open(initialCache)).keys()).length === 5, 'Query strings grew the cache');

    await step('HTTP error fallback');
    await mode('error');
    await navigate('/newsfeed/?server-error=1');
    edition(1);
    assert((await frame.contentWindow.fetch('/outside.html')).status === 503, 'Worker intercepted another route');

    await step('truncated response fallback');
    await mode('truncated');
    await navigate('/newsfeed/?partial=1');
    edition(1);

    await step('online update');
    await mode('online', 2);
    await navigate('/newsfeed/');
    edition(2);
    await step('worker update');
    await registration.update();
    await until(async () => {
      const names = await appCaches();
      return names.length === 1 && names[0] !== initialCache;
    });
    assert((await caches.keys()).includes('another-app:keep'), 'Deleted another app cache');
    await step('updated offline navigation');
    await mode('offline', 2);
    await navigate('/newsfeed/');
    edition(2);
    await fetch('/report', { method: 'POST', body: 'passed' });
  } catch (error) {
    await fetch('/report', { method: 'POST', body: error.stack || String(error) });
  }
})();
