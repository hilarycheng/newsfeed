import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const WINDOW_MS = 72 * 60 * 60 * 1000;
const PAGE_SIZE = 16;
const TECH_CATEGORIES = new Set([
  'Java / Kotlin', 'Rust / Node.js', 'Linux', '資安', 'AI', '單機遊戲',
  'Android / 嵌入式', '科技', 'Elixir / Erlang',
]);
const CATEGORIES = [...TECH_CATEGORIES, 'World / 國際', '香港'];
export const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'tech', label: 'Tech' },
  { key: 'world', label: 'World' },
  { key: 'hong-kong', label: 'Hong Kong' },
  { key: 'steam', label: 'Steam' },
  { key: 'cve', label: 'CVE' },
];

export function safeHttps(value) {
  if (typeof value !== 'string' || /[\s<>"\x00-\x1f]/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !!url.hostname && !url.username && !url.password;
  } catch { return false; }
}

const validTimestamp = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
// A price check is not a new offer. Only the feeder's discovery/price-change
// lifecycle can supply a deal's position in the news timeline.
export function storyTimestamp(story) {
  if (story.kind !== 'deal') return story.publishedAt;
  if (validTimestamp(story.deal?.newsAt)) return story.deal.newsAt;
  if (validTimestamp(story.freshnessAt)) return story.freshnessAt;
  const dates = [story.deal?.firstSeen, story.deal?.firstSeenAt, story.deal?.priceChangedAt].filter(validTimestamp);
  return dates.sort((a, b) => Date.parse(b) - Date.parse(a))[0] || null;
}

export function isCurrent(story, updatedAt) {
  const age = Date.parse(updatedAt) - Date.parse(storyTimestamp(story));
  return Number.isFinite(age) && age >= 0 && (story.kind === 'deal' ? age < WINDOW_MS : age <= WINDOW_MS);
}

export function filterKeysForStory(story) {
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

export function prepareFeed(input, now = Date.now()) {
  if (!Array.isArray(input.stories) || !input.stories.length || input.stories.length > 1200 || !validTimestamp(input.updatedAt)) {
    throw new Error('Invalid feed');
  }
  const stories = input.stories.filter(story => {
    if (story.kind !== 'deal') return true;
    const expired = /^\d{4}-\d{2}-\d{2}T/.test(story.deal?.saleEnd || '') && Date.parse(story.deal.saleEnd) <= now;
    return story.deal?.active !== false && !expired && isCurrent(story, new Date(now).toISOString());
  });
  const ids = new Set();
  const urls = new Set();
  for (const story of stories) {
    if (!story.id || ids.has(story.id) || urls.has(story.url) || !safeHttps(story.url) ||
        !validTimestamp(storyTimestamp(story)) || !story.title || !story.summary) {
      throw new Error('Invalid or duplicate story');
    }
    ids.add(story.id);
    urls.add(story.url);
  }
  // All current cards share one chronology, including Steam. Neither discounts,
  // periodic checks nor source preference can manufacture a newer news date.
  stories.sort((a, b) => Number(isCurrent(b, input.updatedAt)) - Number(isCurrent(a, input.updatedAt)) ||
    Date.parse(storyTimestamp(b)) - Date.parse(storyTimestamp(a)) || String(a.id).localeCompare(String(b.id)));
  return { ...input, stories };
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

export function card(story, index) {
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
  const license = story.contentLicense === 'Apache-2.0' ? '<span class="publisher-attribution">來源摘錄 · <a href="/licenses/Apache-2.0.txt" target="_blank" rel="noopener noreferrer">Apache-2.0</a></span>' : '';
  const rights = [story.publisherAuthor, story.publisherRights].filter(value => typeof value === 'string' && value).map(value => `<span class="publisher-attribution">${escape(value)}</span>`).join('');
  const brand = safeHttps(story.sourceBrandImage) ? `<img class="source-brand" src="${escape(story.sourceBrandImage)}" alt="${escape(story.source)}" loading="lazy" referrerpolicy="no-referrer">` : '';
  const sourceName = safeHttps(story.attributionUrl) ? `<a href="${escape(story.attributionUrl)}" target="_blank" rel="noopener noreferrer">${escape(story.source)}</a>` : escape(story.source);
  const cveRating = typeof story.cveRating === 'string' && story.cveRating ? `<p class="cve-rating"><strong>${escape(story.cveRating)}</strong></p>` : '';
  const cvss = (Array.isArray(story.cvss) ? story.cvss : []).filter(metric => metric && typeof metric === 'object').map(metric => `<li>CVSS ${escape(metric.version)} · ${escape(metric.score)}${metric.source ? ` · ${escape(metric.source)}` : ''}${metric.vector ? `<br><span class="cvss-vector">${escape(metric.vector)}</span>` : ''}</li>`).join('');
  const relatedArticles = (Array.isArray(story.relatedArticles) ? story.relatedArticles : []).filter(article => article && safeHttps(article.url)).map(article => `<li><a href="${escape(article.url)}" target="_blank" rel="noopener noreferrer">${escape(article.title || article.url)}</a>${article.source ? ` · ${escape(article.source)}` : ''}</li>`).join('');
  const cveDetails = cveRating + (cvss ? `<ul class="cve-metrics">${cvss}</ul>` : '') + (relatedArticles ? `<details class="cve-related"><summary>相關報道</summary><ul>${relatedArticles}</ul></details>` : '');
  return `<article class="story cat-${CATEGORIES.indexOf(story.category)}" data-story-id="${escape(story.id)}"${deal ? ` data-price-checked="${escape(checkedAt || '')}"` : ''}><div class="story-image">${safeHttps(story.imageUrl) ? `<img src="${escape(story.imageUrl)}" alt="${escape(story.imageLabel || '來源配圖')}" loading="lazy" referrerpolicy="no-referrer">` : art(story)}${story.kind === 'video' ? '<span class="kind-badge">▶ YouTube</span>' : deal ? '<span class="kind-badge sale-badge">Steam 港區優惠</span>' : ''}<span class="image-label">${escape(story.imageUrl ? story.imageLabel || '來源配圖' : '分類示意圖')}</span></div><div class="story-content"><div class="story-top"><span class="category">${icon}${escape(story.category)}</span><span class="number">${String(index + 1).padStart(2, '0')}</span></div><a class="story-link" href="${escape(story.url)}" target="_blank" rel="noopener noreferrer"><h2>${escape(story.title)}</h2></a>${price}<p class="summary">${escape(story.summary)}</p>${cveDetails}${language}<footer><span class="source">${brand}${sourceName}${attribution}${license}${rights}<span class="dot">·</span>${timelineLabel}<time datetime="${escape(timestamp)}">${escape(date(timestamp, !deal && story.datePrecision === 'day'))}</time></span><a class="original" href="${escape(story.url)}" target="_blank" rel="noopener noreferrer" aria-label="${escape((deal ? '查看優惠：' : story.kind === 'video' ? '觀看影片：' : '閱讀原文：') + story.title)}">${deal ? '查看優惠' : story.kind === 'video' ? '觀看影片' : '原文'}${ext}</a></footer></div></article>`;
}

export function createCards(feed) {
  return feed.stories.map((story, index) => ({
    html: card(story, index),
    current: isCurrent(story, feed.updatedAt),
    deal: story.kind === 'deal',
    expiresAt: story.kind === 'deal' ? Date.parse(storyTimestamp(story)) + WINDOW_MS : null,
    filters: filterKeysForStory(story),
  }));
}

function firstPage(cards) {
  let archiveShown = false;
  return cards.slice(0, PAGE_SIZE).map(entry => {
    const heading = !entry.current && !archiveShown ? archiveHeading : '';
    if (!entry.current) archiveShown = true;
    return heading + entry.html;
  }).join('');
}

// This standalone function is serialized into the static bundle. Keep it free
// from build-time dependencies so fixture tests can exercise the real reader.
export function startReader() {
  'use strict';
  const payload = JSON.parse(document.getElementById('cards').textContent);
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
    const html = next.map(entry => {
      const heading = !entry.current && !archiveShown ? payload.archiveHeading : '';
      if (!entry.current) archiveShown = true;
      return heading + entry.html;
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
  select('all');
}

export async function buildStatic({ rootDir = process.cwd(), now = Date.now() } = {}) {
  const feed = prepareFeed(JSON.parse(await fs.readFile(path.join(rootDir, 'data/feed.json'), 'utf8')), now);
  const cards = createCards(feed);
  const failures = (feed.sourceStatus || []).filter(source => source.status !== 'ok');
  const dealCount = feed.stories.filter(story => story.kind === 'deal').length;
  const count = feed.stories.filter(story => story.kind !== 'deal' && isCurrent(story, feed.updatedAt)).length;
  const css = (await fs.readFile(path.join(rootDir, 'app/globals.css'), 'utf8')).replace(/@import[^;]+;/g, '') + '\n[hidden]{display:none!important}.category-art svg{width:60px;height:60px}';
  const dist = path.join(rootDir, 'dist');
  await fs.rm(dist, { recursive: true, force: true });
  await fs.mkdir(dist, { recursive: true });
  await fs.writeFile(path.join(dist, 'styles.css'), css);
  await fs.copyFile(path.join(rootDir, 'public/favicon.svg'), path.join(dist, 'favicon.svg'));
  try {
    await fs.cp(path.join(rootDir, 'public/licenses'), path.join(dist, 'licenses'), { recursive: true });
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    if (feed.stories.some(story => story.contentLicense === 'Apache-2.0')) throw new Error('Missing required publisher license');
  }
  await fs.writeFile(path.join(dist, 'feed.json'), JSON.stringify(feed));
  await fs.writeFile(path.join(dist, 'update-plan.json'), JSON.stringify({ version: 8, scheduleStatus: 'managed-by-existing-automation', excludeMedium: true, mode: 'local-generation-static-publication', project_id: 'appgprj_6ac19d4fd8088191be90132df7a658e0', read: '/feed.json', sourceInstructions: 'Open same Site source with standard Sites workflow. Run python scripts/run-feeder.py. Read its single JSON status only; on success package/build and publish the exact static source. On failure preserve publication. Do not perform article review, rewriting, translation, or model calls. README.md has troubleshooting.', timezone: 'Asia/Hong_Kong', windowHours: 72, minimumTarget: 200, preferredTarget: 300, hardLimit: 1200, noWebsiteWriteAPI: true }));
  const filters = `<fieldset class="feed-filters"><legend>分類篩選</legend><div class="filter-options">${FILTERS.map(filter => `<button type="button" data-feed-filter="${filter.key}" aria-pressed="${filter.key === 'all'}" aria-controls="feed" disabled>${filter.label}</button>`).join('')}</div></fieldset>`;
  const payload = JSON.stringify({ cards, filters: FILTERS, pageSize: PAGE_SIZE, archiveHeading, updatedAt: feed.updatedAt }).replace(/</g, '\\u003c');
  const html = `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>News Feed</title><meta name="description" content="最近三日科技、國際、香港與單機遊戲新聞。"><link rel="icon" href="/favicon.svg"><link rel="stylesheet" href="/styles.css"></head><body><main><header><div class="brand"><span class="brand-icon">${icon}</span><h1>News Feed</h1></div></header><div class="edition"><span>${escape(new Intl.DateTimeFormat('zh-Hant-HK', { timeZone: 'Asia/Hong_Kong', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date(feed.editionDate + 'T04:00:00Z')))}</span><span id="edition-count">${feed.stories.length} 則</span></div><p class="updated">上次整理 ${escape(date(feed.updatedAt))} · 香港時間</p><div class="volume"><strong id="volume-count">近三日 · 72 小時 · ${count} 則${dealCount ? ` · ${dealCount} 個優惠` : ''}</strong><span id="volume-target">目標三日 200–300+ 則</span></div><p id="shortfall" class="shortfall"${count >= 200 ? ' hidden' : ''}>近三日核實的合適新聞少於 200 則；不以重複、舊聞或不相關內容湊數。</p><aside id="stale" class="notice" hidden>本期超過 36 小時未更新，以下保留上次成功整理的內容。</aside>${feed.note ? `<aside class="notice">${escape(feed.note)}</aside>` : ''}${filters}<p class="filter-status" id="filter-status" role="status" aria-live="polite" aria-atomic="true">All · 顯示 ${Math.min(PAGE_SIZE, cards.length)} / ${cards.length} 則</p><section class="feed" id="feed" aria-label="新聞內容">${firstPage(cards)}</section><div id="more" class="load-more"><button id="load-more" type="button" aria-controls="feed">載入更多</button></div><div id="end" class="feed-end" hidden><span aria-hidden="true"></span><b id="end-count">已讀完 ${feed.stories.length} 則</b><span aria-hidden="true"></span></div>${failures.length ? `<details class="source-status"><summary>來源狀態 · ${failures.length} 個來源受限</summary><ul>${failures.map(source => `<li><strong>${escape(source.name)}</strong>：${escape(source.detail || '來源暫時無法讀取')}</li>`).join('')}</ul></details>` : ''}<div class="footnote">標題與短摘錄直接取自來源，未經 AI 改寫，完整內容請閱讀原文。<br>新聞與優惠動態時間均以香港時間顯示；價格核對時間另列。</div><noscript><p>啟用 JavaScript 後可篩選分類及繼續載入其餘新聞。</p></noscript></main><script type="application/json" id="cards">${payload}</script><script src="/reader.js" defer></script></body></html>`;
  await fs.writeFile(path.join(dist, 'index.html'), html);
  await fs.writeFile(path.join(dist, 'reader.js'), `(${startReader.toString()})();\n`);
  await fs.writeFile(path.join(dist, '_headers'), '/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  X-Frame-Options: DENY\n  Cache-Control: public, max-age=300\n');
  return { static: true, stories: feed.stories.length, verified72h: count, writeEndpoints: 0 };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  console.log(JSON.stringify(await buildStatic()));
}
