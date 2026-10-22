(() => {
'use strict';
const WINDOW_MS = 72 * 60 * 60 * 1000;
const PAGE_SIZE = 16;
const TECH_CATEGORIES = new Set([
  'Java / Kotlin', 'Rust / Node.js', 'Linux', '資安', 'AI', '單機遊戲',
  'Android / 嵌入式', '科技', 'Elixir / Erlang',
]);
const CATEGORIES = [...TECH_CATEGORIES, 'World / 國際', '香港'];
const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'tech', label: 'Tech' },
  { key: 'world', label: 'World' },
  { key: 'hong-kong', label: 'Hong Kong' },
  { key: 'steam', label: 'Steam' },
  { key: 'cve', label: 'CVE' },
];

function safeHttps(value) {
  if (typeof value !== 'string' || /[\s<>"\x00-\x1f]/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !!url.hostname && !url.username && !url.password;
  } catch { return false; }
}

const validTimestamp = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
// A price check is not a new offer. Only the feeder's discovery/price-change
// lifecycle can supply a deal's position in the news timeline.
function storyTimestamp(story) {
  if (story.kind !== 'deal') return story.publishedAt;
  if (validTimestamp(story.deal?.newsAt)) return story.deal.newsAt;
  if (validTimestamp(story.freshnessAt)) return story.freshnessAt;
  const dates = [story.deal?.firstSeen, story.deal?.firstSeenAt, story.deal?.priceChangedAt].filter(validTimestamp);
  return dates.sort((a, b) => Date.parse(b) - Date.parse(a))[0] || null;
}

function isCurrent(story, updatedAt) {
  const age = Date.parse(updatedAt) - Date.parse(storyTimestamp(story));
  return Number.isFinite(age) && age >= 0 && (story.kind === 'deal' ? age < WINDOW_MS : age <= WINDOW_MS);
}

function filterKeysForStory(story) {
  if (story.kind === 'deal') return ['all', 'steam'];
  const keys = ['all'];
  if (['World / 國際', 'World', '國際'].includes(story.category)) keys.push('world');
  else if (['香港', 'Hong Kong / 香港', 'Hong Kong'].includes(story.category)) keys.push('hong-kong');
  else if (TECH_CATEGORIES.has(story.category)) keys.push('tech');
  // Do not infer eligibility from the headline. The feeder supplies these IDs
  // only after its existing CVE/security policy has accepted the article.
  if (Array.isArray(story.cveIds) && story.cveIds.some(id => /^CVE-\d{4}-\d{4,}$/.test(id))) keys.push('cve');
  return keys;
}

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const date = (value, day = false) => new Intl.DateTimeFormat('zh-Hant-HK', {
  timeZone: 'Asia/Hong_Kong', month: 'numeric', day: 'numeric',
  ...(day ? {} : { hour: '2-digit', minute: '2-digit', hour12: false }),
}).format(new Date(value));
const icon = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/></svg>';
const ext = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M14 3h7v7M21 3l-10 10M10 3H4v17h17v-6"/></svg>';
const art = story => `<div class="category-art">${icon}<span>${escape(story.category)}</span></div>`;
const archiveHeading = '<div class="archive-heading"><h2>其他已核實消息</h2><p>未能確認在近三日範圍的消息。</p></div>';

function card(story, index) {
  if (!safeHttps(story.url)) throw new Error('Unsafe story URL');
  const deal = story.kind === 'deal' ? story.deal : null;
  const checkedAt = deal?.checkedAt || story.checkedAt;
  const price = deal ? `<div class="deal-price"><span class="discount">-${escape(deal.discountPercent)}%</span><strong>${escape(deal.priceText)}</strong>${deal.originalPriceText ? `<del>${escape(deal.originalPriceText)}</del>` : ''}<span class="region">香港 · HKD</span></div><p class="deal-terms">${validTimestamp(checkedAt) ? `價格核對於 <time datetime="${escape(checkedAt)}">${escape(date(checkedAt))}</time>（香港時間）` : '價格核對時間未記錄'}${deal.saleEnd ? ` · 截至 ${escape(deal.saleEnd)}${String(deal.saleEndPrecision || '').includes('date') ? '，確實時間未列明' : ''}` : ''}<br>價格以 Steam 結帳頁為準。${deal.lastCheckStatus && deal.lastCheckStatus !== 'deal' ? '<span class="price-check-failed">最近一次價格核對未成功，以下保留上次成功價格。</span>' : ''}<span class="price-stale" hidden>價格超過 36 小時未核實。</span></p>` : '';
  const language = story.sourceLanguage === 'zh-Hans' || story.originalLanguage === 'zh-Hans' ? '<span class="language-label">原文：簡體中文</span>' : '';
  const timestamp = storyTimestamp(story);
  const timelineLabel = !deal ? '' : deal.priceChangedAt && Date.parse(deal.priceChangedAt) >= Date.parse(deal.firstSeen || 0)
    ? '價格變動 ' : deal.firstSeenBasis === 'migration_baseline' ? '首次記錄（原始日期未明） ' : '首次發現 ';
  const publisher = typeof story.originalPublisher === 'string' ? story.originalPublisher : story.originalPublisher?.name;
  const publisherName = safeHttps(story.originalPublisherUrl) ? `<a href="${escape(story.originalPublisherUrl)}" target="_blank" rel="noopener noreferrer">${escape(publisher)}</a>` : escape(publisher);
  const attribution = publisher && publisher !== story.source ? `<span class="publisher-attribution">原出版者：${publisherName}</span>` : '';
  const license = story.contentLicense === 'Apache-2.0' ? '<span class="publisher-attribution">來源摘錄 · <a href="#apache-license">Apache-2.0</a></span>' : '';
  const rights = [story.publisherAuthor, story.publisherRights].filter(value => typeof value === 'string' && value).map(value => `<span class="publisher-attribution">${escape(value)}</span>`).join('');
  const brand = safeHttps(story.sourceBrandImage) ? `<img class="source-brand" src="${escape(story.sourceBrandImage)}" alt="${escape(story.source)}" loading="lazy" referrerpolicy="no-referrer">` : '';
  const sourceName = safeHttps(story.attributionUrl) ? `<a href="${escape(story.attributionUrl)}" target="_blank" rel="noopener noreferrer">${escape(story.source)}</a>` : escape(story.source);
  const cveRating = typeof story.cveRating === 'string' && story.cveRating ? `<p class="cve-rating"><strong>${escape(story.cveRating)}</strong></p>` : '';
  const cvss = (Array.isArray(story.cvss) ? story.cvss : []).filter(metric => metric && typeof metric === 'object').map(metric => `<li>CVSS ${escape(metric.version)} · ${escape(metric.score)}${metric.source ? ` · ${escape(metric.source)}` : ''}${metric.vector ? `<br><span class="cvss-vector">${escape(metric.vector)}</span>` : ''}</li>`).join('');
  const relatedArticles = (Array.isArray(story.relatedArticles) ? story.relatedArticles : []).filter(article => article && safeHttps(article.url)).map(article => `<li><a href="${escape(article.url)}" target="_blank" rel="noopener noreferrer">${escape(article.title || article.url)}</a>${article.source ? ` · ${escape(article.source)}` : ''}</li>`).join('');
  const cveDetails = cveRating + (cvss ? `<ul class="cve-metrics">${cvss}</ul>` : '') + (relatedArticles ? `<details class="cve-related"><summary>相關報道</summary><ul>${relatedArticles}</ul></details>` : '');
  return `<article class="story cat-${CATEGORIES.indexOf(story.category)}" data-story-id="${escape(story.id)}"${deal ? ` data-price-checked="${escape(checkedAt || '')}"` : ''}><div class="story-image">${safeHttps(story.imageUrl) ? `<img src="${escape(story.imageUrl)}" alt="${escape(story.imageLabel || '來源配圖')}" loading="lazy" referrerpolicy="no-referrer">` : art(story)}${story.kind === 'video' ? '<span class="kind-badge">▶ YouTube</span>' : deal ? '<span class="kind-badge sale-badge">Steam 港區優惠</span>' : ''}<span class="image-label">${escape(story.imageUrl ? story.imageLabel || '來源配圖' : '分類示意圖')}</span></div><div class="story-content"><div class="story-top"><span class="category">${icon}${escape(story.category)}</span><span class="number">${String(index + 1).padStart(2, '0')}</span></div><a class="story-link" href="${escape(story.url)}" target="_blank" rel="noopener noreferrer"><h2>${escape(story.title)}</h2></a>${price}<p class="summary">${escape(story.summary)}</p>${cveDetails}${language}<footer><span class="source">${brand}${sourceName}${attribution}${license}${rights}<span class="dot">·</span>${timelineLabel}<time datetime="${escape(timestamp)}">${escape(date(timestamp, !deal && story.datePrecision === 'day'))}</time></span><a class="original" href="${escape(story.url)}" target="_blank" rel="noopener noreferrer" aria-label="${escape((deal ? '查看優惠：' : story.kind === 'video' ? '觀看影片：' : '閱讀原文：') + story.title)}">${deal ? '查看優惠' : story.kind === 'video' ? '觀看影片' : '原文'}${ext}</a></footer></div></article>`;
}

function createCards(feed) {
  return feed.stories.map(story => ({
    story,
    current: isCurrent(story, feed.updatedAt),
    deal: story.kind === 'deal',
    expiresAt: story.kind === 'deal' ? Math.min(Date.parse(storyTimestamp(story)) + WINDOW_MS, /^\d{4}-\d{2}-\d{2}T/.test(story.deal?.saleEnd || '') && validTimestamp(story.deal.saleEnd) ? Date.parse(story.deal.saleEnd) : Infinity) : null,
    filters: filterKeysForStory(story),
  }));
}

function startReader() {
  'use strict';
  const input = JSON.parse(document.getElementById('feed-data').textContent);
  const payload = { ...input, cards: createCards(input), filters: FILTERS, pageSize: PAGE_SIZE, archiveHeading };
  let cards = payload.cards;
  const pageSize = payload.pageSize;
  const feed = document.getElementById('feed');
  const more = document.getElementById('more');
  const button = document.getElementById('load-more');
  const end = document.getElementById('end');
  const controls = [...document.querySelectorAll('[data-feed-filter]')];
  const status = document.getElementById('filter-status');
  let active = 'all';
  let matching = cards;
  let shown = 0;
  let archiveShown = false;
  let observer;
  let generation = 0;
  let expiryTimer;

  function images() {
    feed.querySelectorAll('.story-image img:not([data-error-bound])').forEach(img => {
      img.dataset.errorBound = '1';
      img.addEventListener('error', () => {
        const category = img.closest('article').querySelector('.category').textContent;
        const art = document.createElement('div');
        art.className = 'category-art';
        const text = document.createElement('span');
        text.textContent = category;
        art.append(text);
        img.replaceWith(art);
        art.parentElement.querySelector('.image-label').textContent = '分類示意圖';
      });
    });
  }

  function state() {
    feed.querySelectorAll('[data-price-checked]').forEach(el => {
      const notice = el.querySelector('.price-stale');
      const checked = Date.parse(el.dataset.priceChecked);
      if (notice) notice.hidden = Number.isFinite(checked) && Date.now() - checked <= 129600000;
    });
    feed.querySelectorAll('.number').forEach((el, index) => { el.textContent = String(index + 1).padStart(2, '0'); });
    const label = payload.filters.find(filter => filter.key === active).label;
    const freshNews = matching.filter(entry => entry.current && !entry.deal).length;
    const deals = matching.filter(entry => entry.deal).length;
    button.textContent = '載入更多 · 還有 ' + Math.max(0, matching.length - shown) + ' 則';
    more.hidden = shown >= matching.length;
    end.hidden = shown < matching.length || !matching.length;
    document.getElementById('end-count').textContent = '已讀完 ' + matching.length + ' 則';
    document.getElementById('edition-count').textContent = matching.length + ' 則';
    document.getElementById('volume-count').textContent = '近三日 · 72 小時 · ' + freshNews + ' 則' + (deals ? ' · ' + deals + ' 個優惠' : '');
    document.getElementById('shortfall').hidden = active !== 'all' || freshNews >= 200;
    document.getElementById('volume-target').hidden = active !== 'all';
    status.textContent = label + ' · 顯示 ' + shown + ' / ' + matching.length + ' 則';
    images();
  }

  function load() {
    const next = matching.slice(shown, shown + pageSize);
    if (!next.length) return;
    const html = next.map((entry, index) => {
      const heading = !entry.current && !archiveShown ? payload.archiveHeading : '';
      if (!entry.current) archiveShown = true;
      return heading + card(entry.story, shown + index);
    }).join('');
    feed.insertAdjacentHTML('beforeend', html);
    shown += next.length;
    state();
  }

  function select(key) {
    generation += 1;
    if (observer) observer.disconnect();
    active = key;
    cards = payload.cards.filter(entry => !entry.deal || entry.expiresAt > Date.now());
    // Filter the entire edition before pagination, never only the loaded cards.
    matching = cards.filter(entry => entry.filters.includes(key));
    shown = 0;
    archiveShown = false;
    feed.replaceChildren();
    controls.forEach(control => control.setAttribute('aria-pressed', String(control.dataset.feedFilter === key)));
    if (matching.length) load();
    else {
      const empty = document.createElement('div');
      empty.className = 'empty';
      const message = document.createElement('p');
      message.textContent = '這個分類暫時沒有消息。';
      empty.append(message);
      feed.append(empty);
      state();
    }
    if ('IntersectionObserver' in window) {
      const selectedGeneration = generation;
      observer = new IntersectionObserver(entries => {
        // Ignore queued callbacks from a previous filter selection.
        if (selectedGeneration === generation && entries.some(entry => entry.isIntersecting)) load();
      }, { rootMargin: '600px' });
      observer.observe(more);
    }
    if (expiryTimer) window.clearTimeout(expiryTimer);
    const nextExpiry = Math.min(...cards.filter(entry => entry.deal).map(entry => entry.expiresAt));
    if (Number.isFinite(nextExpiry)) {
      expiryTimer = window.setTimeout(() => select(active), Math.min(2147483647, Math.max(1, nextExpiry - Date.now() + 20)));
    }
  }

  controls.forEach(control => {
    control.disabled = false;
    control.addEventListener('click', () => {
      if (control.dataset.feedFilter !== active) select(control.dataset.feedFilter);
    });
  });
  button.addEventListener('click', load);
  document.getElementById('stale').hidden = Date.now() - Date.parse(payload.updatedAt) <= 129600000;
  document.getElementById('edition-date').textContent = new Intl.DateTimeFormat('zh-Hant-HK', { timeZone: 'Asia/Hong_Kong', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date(input.editionDate + 'T04:00:00Z'));
  document.getElementById('updated-at').textContent = date(input.updatedAt);
  select('all');
  document.addEventListener('click', event => {
    if (event.target.closest('a[href="#apache-license"]')) document.getElementById('apache-license').open = true;
  });
  if (location.hash === '#apache-license' && document.getElementById('apache-license')) document.getElementById('apache-license').open = true;
}

startReader();
})();
