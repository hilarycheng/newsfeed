(async () => {
  const frame = document.getElementById('preview');
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  const equal = (a, b, message) => assert(JSON.stringify(a) === JSON.stringify(b), message + ': ' + JSON.stringify(a));
  const now = Date.parse('2026-10-13T00:00:00Z');
  const preload = `<script>Date.now=()=>${now};window.errors=[];window.addEventListener('error',e=>window.errors.push(e.message));window.observers=[];window.IntersectionObserver=class{constructor(callback){this.callback=callback;window.observers.push(this)}observe(){}disconnect(){}};<\/script>`;
  async function open(html, mock = true) {
    await new Promise(resolve => {
      frame.onload = resolve;
      frame.srcdoc = html.replace('<head>', '<head>' + (mock ? preload : `<script>Date.now=()=>${now};<\/script>`));
    });
    return frame.contentDocument;
  }
  try {
    let doc = await open(fixturePage);
    const select = key => doc.querySelector(`[data-feed-filter="${key}"]`).click();
    const language = key => doc.querySelector(`[data-language-filter="${key}"]`).click();
    const articles = () => [...doc.querySelectorAll('#feed article')];
    const ids = () => articles().map(a => a.dataset.storyId);
    const more = () => doc.getElementById('load-more').click();
    equal(articles().length, 16, 'initial page');
    equal(doc.querySelector('[aria-pressed=true]').textContent, 'All', 'default filter');
    assert(!frame.contentWindow.__unsafe, 'publisher script executed');
    equal(doc.querySelector('[data-story-id=publisher-preserved] .summary').textContent,
      'Exact publisher text & punctuation. '.repeat(20) + '</script><script>window.__unsafe=true</script>{{reader}}', 'publisher content unchanged');
    equal(doc.querySelectorAll('.source-brand').length, 1, 'source brand retained');
    assert(doc.querySelector('.publisher-attribution'), 'publisher credit missing');
    const broken = doc.querySelector('[data-story-id=tech-0] .story-image img');
    if (broken) broken.dispatchEvent(new frame.contentWindow.Event('error'));
    equal(doc.querySelectorAll('[data-story-id=tech-0] .story-image img').length, 0, 'image fallback');
    doc.querySelector('a[href="#apache-license"]').click();
    assert(doc.getElementById('apache-license').open, 'embedded license must open');
    more(); equal(articles().length, 32, 'next page');
    select('world'); equal(articles().length, 16, 'filter resets page');
    assert(ids().every(id => id.startsWith('world-')), 'world filter');
    equal(doc.getElementById('edition-count').textContent, '21 則', 'filter entire feed');
    equal(doc.getElementById('load-more').textContent, '載入更多 · 還有 5 則', 'remaining count');
    frame.contentWindow.observers[0].callback([{ isIntersecting: true }]);
    equal(articles().length, 16, 'old observer ignored');
    frame.contentWindow.observers.at(-1).callback([{ isIntersecting: true }]);
    equal(articles().length, 21, 'current observer loads');
    equal(doc.querySelectorAll('.archive-heading').length, 1, 'archive heading');
    assert(doc.getElementById('more').hidden, 'last page hides load control');
    select('steam'); equal(articles().length, 16, 'Steam first page');
    equal(doc.getElementById('edition-count').textContent, '20 則', 'Steam count');
    assert(ids().every(id => id.startsWith('deal-')), 'Steam deals only');
    equal(doc.querySelectorAll('.price-check-failed').length, 1, 'failed price notice');
    assert(!doc.querySelector('[data-story-id=deal-1] .price-stale').hidden, 'stale price notice');
    more(); equal(articles().length, 20, 'Steam next page');
    select('cve'); equal(ids(), ['validated-cve'], 'structured CVE filter');
    equal(doc.querySelector('.cve-rating').textContent, '<img onerror=alert(1)>', 'CVE rating escape');
    assert(doc.querySelector('.cve-metrics').textContent.includes('CVSS 3.1 · 9.8'), 'CVE metrics');
    equal(doc.querySelectorAll('.cve-related a').length, 1, 'unsafe related links rejected');
    assert(!doc.querySelector('.cve-rating img'), 'unsafe rating markup');
    select('hong-kong'); equal(articles().length, 6, 'Hong Kong filter');
    select('tech'); assert(ids().includes('tech-0'), 'Elixir is Tech');
    select('all'); equal(articles().length, 16, 'All reset');
    equal(doc.querySelector('.number').textContent, '01', 'number reset');
    const observers = frame.contentWindow.observers.length;
    select('all'); equal(frame.contentWindow.observers.length, observers, 'same filter no duplicate');
    assert(doc.querySelector('#filter-status[aria-live=polite]'), 'accessible status');
    assert(articles().every(a => a.querySelector('.original').getAttribute('aria-label')), 'accessible original links');
    for (const [width, columns] of [[1920, 4], [1440, 3], [1024, 2], [768, 1], [390, 1]]) {
      frame.style.width = width + 'px';
      const style = frame.contentWindow.getComputedStyle(doc.getElementById('feed'));
      equal(style.gridTemplateColumns.split(' ').length, columns, 'columns at ' + width);
      assert(doc.documentElement.scrollWidth <= width, 'horizontal overflow at ' + width);
      for (const control of doc.querySelectorAll('[data-language-filter], [data-feed-filter]')) {
        const rect = control.getBoundingClientRect();
        assert(rect.height >= 44 && rect.left >= 0 && rect.right <= width, 'language button fits at ' + width);
      }
      const first = articles()[0];
      const measure = selector => first.querySelector(selector).getBoundingClientRect();
      if (columns > 1) equal(Math.round(first.getBoundingClientRect().width), 448, 'desktop card width');
      else {
        equal(Math.round(first.getBoundingClientRect().width), width === 390 ? 362 : 640, 'mobile card width');
        equal(Math.round(measure('.story-image').height), width === 390 ? 166 : 245, 'mobile image height');
      }
      select('world'); more();
      equal(frame.contentWindow.getComputedStyle(doc.querySelector('.archive-heading')).gridColumnEnd, '-1', 'archive spans columns');
      select('all');
    }
    equal(frame.contentWindow.errors, [], 'browser errors');
    const mutate = change => fixturePage.replace(/(<script type="application\/json" id="feed-data">)([\s\S]*?)(<\/script>)/,
      (_, before, json, after) => { const data = JSON.parse(json); change(data); return before + JSON.stringify(data).replaceAll('<', '\\u003c') + after; });
    doc = await open(mutate(data => {
      data.stories.filter(s => s.id.startsWith('world-')).forEach(s => { s.sourceLanguage = 'zh-Hant'; });
      data.stories.find(s => s.id === 'hk-0').sourceLanguage = 'zh-HK';
      data.stories.find(s => s.id === 'tech-0').originalLanguage = 'zh-Hans';
      delete data.stories.find(s => s.id === 'tech-2').sourceLanguage;
      data.stories.find(s => s.id === 'tech-3').sourceLanguage = 'ja';
      data.stories.find(s => s.id === 'tech-4').sourceLanguage = 'en-US';
    }));
    equal(doc.querySelector('[data-language-filter][aria-pressed=true]').textContent, '全部語言', 'default language');
    language('zh');
    equal(articles().length, 16, 'Chinese first page');
    equal(doc.getElementById('edition-count').textContent, '22 則', 'Chinese includes traditional, Hong Kong and simplified');
    assert(ids().includes('tech-0'), 'original simplified language takes precedence');
    assert(doc.getElementById('shortfall').hidden && doc.getElementById('volume-target').hidden, 'global target hidden for language subset');
    const oldLanguageObserver = frame.contentWindow.observers.at(-1);
    more(); equal(articles().length, 22, 'Chinese next page');
    select('world');
    equal(ids(), Array.from({ length: 16 }, (_, i) => 'world-' + i), 'combined filter preserves order and resets page');
    equal(doc.getElementById('filter-status').textContent, 'World · 中文 · 顯示 16 / 20 則', 'combined status');
    oldLanguageObserver.callback([{ isIntersecting: true }]);
    equal(articles().length, 16, 'old language observer ignored');
    frame.contentWindow.observers.at(-1).callback([{ isIntersecting: true }]);
    equal(articles().length, 20, 'Chinese infinite scroll');
    select('tech'); equal(ids(), ['tech-0'], 'Tech and Chinese');
    select('hong-kong'); equal(ids(), ['hk-0'], 'Hong Kong and Chinese');
    select('steam'); equal(ids(), [], 'empty language and category combination');
    assert(doc.querySelector('#feed .empty') && doc.getElementById('more').hidden, 'empty combination controls');
    language('en'); equal(articles().length, 16, 'English retains Steam selection');
    equal(doc.getElementById('edition-count').textContent, '20 則', 'English Steam count');
    select('all');
    equal(doc.getElementById('edition-count').textContent, '67 則', 'English excludes Chinese, unknown and Japanese');
    assert(ids().includes('tech-4'), 'English regional tag');
    assert(!ids().some(id => ['tech-0', 'tech-2', 'tech-3'].includes(id)), 'English result languages');
    const languageObservers = frame.contentWindow.observers.length;
    language('en'); equal(frame.contentWindow.observers.length, languageObservers, 'same language no duplicate');
    language('all');
    equal(doc.getElementById('edition-count').textContent, '91 則', 'all languages restores entire feed');
    assert(ids().includes('tech-2') && ids().includes('tech-3'), 'unknown and other languages remain in All');
    equal(frame.contentWindow.errors, [], 'language browser errors');
    doc = await open(mutate(data => {
      const newsletter = { ...data.stories.find(s => s.id === 'tech-0'), category: 'Newsletter',
        source: 'Programming Digest', id: 'newsletter-boundary', url: 'https://example.com/newsletter-boundary',
        publishedAt: '2026-09-29T00:00:00Z' };
      const expiredNewsletter = { ...newsletter, id: 'newsletter-expired', url: 'https://example.com/newsletter-expired',
        publishedAt: '2026-09-28T23:59:59Z' };
      const ordinary = { ...newsletter, category: 'Java / Kotlin', source: 'Inside Java',
        id: 'ordinary-boundary', url: 'https://example.com/ordinary-boundary', publishedAt: '2026-10-10T00:00:00Z' };
      const expiredOrdinary = { ...ordinary, id: 'ordinary-expired', url: 'https://example.com/ordinary-expired',
        publishedAt: '2026-10-09T23:59:59Z' };
      data.stories = [newsletter, expiredNewsletter, ordinary, expiredOrdinary];
    }));
    select('newsletter');
    equal(ids(), ['newsletter-boundary', 'newsletter-expired'], 'newsletter category includes issues up to the archive boundary');
    equal(doc.getElementById('filter-status').textContent, 'Newsletters · 顯示 2 / 2 則', 'newsletter category filter');
    equal(doc.querySelector('.archive-heading').previousElementSibling.dataset.storyId, 'newsletter-boundary', '14-day issue remains current');
    select('tech');
    equal(ids(), ['ordinary-boundary', 'ordinary-expired'], 'ordinary news remains in the 72-hour category');
    equal(doc.querySelector('.archive-heading').previousElementSibling.dataset.storyId, 'ordinary-boundary', '72-hour boundary remains current');
    doc = await open(mutate(data => data.stories = data.stories.filter(s => s.category !== '香港')));
    select('hong-kong'); equal(articles().length, 0, 'empty category');
    assert(doc.querySelector('#feed .empty') && doc.getElementById('more').hidden, 'empty state controls');
    equal(doc.getElementById('filter-status').textContent, 'Hong Kong · 顯示 0 / 0 則', 'empty status');
    const expired = mutate(data => data.stories.filter(s => s.kind === 'deal').forEach(s => { s.deal.newsAt = '2026-10-10T00:00:00Z'; }));
    doc = await open(expired); select('steam'); equal(articles().length, 0, '72-hour expiry on opening');
    const sale = mutate(data => data.stories.filter(s => s.kind === 'deal').forEach(s => { s.deal.saleEnd = '2026-10-12T23:59:59Z'; }));
    doc = await open(sale); select('steam'); equal(articles().length, 0, 'sale end on opening');
    frame.style.width = '1024px'; frame.style.height = '700px';
    doc = await open(fixturePage, false);
    select('world');
    doc.getElementById('more').scrollIntoView();
    await new Promise(resolve => setTimeout(resolve, 500));
    equal(articles().length, 21, 'native infinite scroll');
    document.getElementById('result').textContent = 'passed';
  } catch (error) {
    document.getElementById('result').textContent = error.stack || String(error);
  }
})();
