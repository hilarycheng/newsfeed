import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import vm from 'node:vm';
import { buildStatic, card, createCards, filterKeysForStory, isCurrent, prepareFeed, safeHttps, startReader, storyTimestamp } from './build-static.mjs';
import { UPDATED_AT, fixtureDeal, fixtureStory, hoursAgo, readerFixture } from './fixtures/news-ui.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const now = Date.parse(UPDATED_AT);

test('Steam freshness never falls back to checkedAt', () => {
  const deal = fixtureDeal('genuine', 30);
  assert.equal(storyTimestamp(deal), hoursAgo(30));
  deal.checkedAt = deal.deal.checkedAt = hoursAgo(-3);
  assert.equal(storyTimestamp(deal), hoursAgo(30));
  delete deal.deal.newsAt;
  delete deal.freshnessAt;
  deal.deal.priceChangedAt = hoursAgo(2);
  assert.equal(storyTimestamp(deal), hoursAgo(2));
  delete deal.deal.firstSeen;
  delete deal.deal.priceChangedAt;
  assert.equal(storyTimestamp(deal), null);
  assert.equal(isCurrent(deal, UPDATED_AT), false);
});

test('deal window removes stale, missing-time, future, expired and inactive offers', () => {
  const unknown = fixtureDeal('unknown');
  delete unknown.freshnessAt;
  delete unknown.deal.newsAt;
  delete unknown.deal.firstSeen;
  const feed = { ...readerFixture(), stories: [
    fixtureStory('news'), fixtureStory('archived-news', '科技', 73),
    fixtureDeal('inside-boundary', 71.999), fixtureDeal('boundary', 72), fixtureDeal('old', 72.001), fixtureDeal('future', -1), unknown,
    fixtureDeal('expired', 3, { saleEnd: hoursAgo(1) }), fixtureDeal('inactive', 3, { active: false }),
  ] };
  assert.deepEqual(prepareFeed(feed, now).stories.map(story => story.id), ['news', 'inside-boundary', 'archived-news']);
});

test('news and offers use one chronology without discount pins or artificial source boosts', () => {
  const feed = { ...readerFixture(), stories: [
    fixtureDeal('high-discount-old', 24, { discountPercent: 99 }),
    fixtureStory('middle-news', '科技', 12, { preferredByUser: true }),
    fixtureDeal('low-discount-new', 6, { discountPercent: 10 }),
    fixtureStory('latest-news', '科技', 1),
  ] };
  assert.deepEqual(prepareFeed(feed, now).stories.map(story => story.id), ['latest-news', 'low-discount-new', 'middle-news', 'high-discount-old']);
});

test('filters distinguish geography, Steam deals, technology, and structured CVE metadata', () => {
  assert.deepEqual(filterKeysForStory(fixtureDeal('deal')), ['all', 'steam']);
  assert.deepEqual(filterKeysForStory(fixtureStory('elixir', 'Elixir / Erlang')), ['all', 'tech']);
  assert.deepEqual(filterKeysForStory(fixtureStory('gaming', '單機遊戲')), ['all', 'tech']);
  assert.deepEqual(filterKeysForStory(fixtureStory('world', 'World / 國際')), ['all', 'world']);
  assert.deepEqual(filterKeysForStory(fixtureStory('hk', '香港')), ['all', 'hong-kong']);
  assert.deepEqual(filterKeysForStory(fixtureStory('mention', '資安', 1, { title: 'CVE-2026-12345', cveIds: ['CVE-2026-1'] })), ['all', 'tech']);
  assert.deepEqual(filterKeysForStory(fixtureStory('accepted', '資安', 1, { cveIds: ['CVE-2026-12345'] })), ['all', 'tech', 'cve']);
});

test('card preserves publisher text, escapes markup and labels verification separately', () => {
  const story = fixtureStory('publisher', '科技', 1, { title: 'Uncut title '.repeat(30), summary: 'Long excerpt <tag> & text '.repeat(35) });
  const html = card(story, 0);
  assert.ok(html.includes(story.title));
  assert.ok(html.includes(story.summary.replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('& text', '&amp; text')));
  const offer = fixtureDeal('failure', 30, { lastCheckStatus: 'failed', firstSeenBasis: 'migration_baseline' });
  const offerHtml = card(offer, 0);
  assert.match(offerHtml, /價格核對於 <time/);
  assert.match(offerHtml, /最近一次價格核對未成功/);
  assert.match(offerHtml, /首次記錄（原始日期未明）/);
  assert.ok(offerHtml.includes(`datetime="${hoursAgo(30)}"`));
  const credited = card({ ...story, originalPublisher: 'Original Publisher', originalPublisherUrl: 'https://fixture.invalid/original', attributionUrl: 'https://fixture.invalid/feed', sourceBrandImage: 'https://fixture.invalid/logo.svg', contentLicense: 'Apache-2.0', publisherAuthor: 'Fixture author', publisherRights: 'Fixture copyright' }, 0);
  for (const credit of ['Original Publisher', 'source-brand', '/licenses/Apache-2.0.txt', '來源摘錄', 'Fixture author', 'Fixture copyright']) assert.ok(credited.includes(credit));
});

test('complete card payload retains filtered items beyond first page', () => {
  const cards = createCards(prepareFeed(readerFixture(), now));
  assert.equal(cards.slice(0, 16).filter(entry => entry.filters.includes('world')).length, 0);
  assert.equal(cards.filter(entry => entry.filters.includes('world')).length, 21);
  assert.equal(cards.filter(entry => entry.filters.includes('steam')).length, 20);
  assert.equal(cards.filter(entry => entry.filters.includes('cve')).length, 1);
});

test('stylesheet contract keeps mobile geometry and declares desktop 4/3/2 columns', async () => {
  const css = await fs.readFile(path.join(root, 'app/globals.css'), 'utf8');
  assert.match(css, /main\{width:min\(100%,680px\);margin:auto;padding:0 20px 32px\}/);
  assert.match(css, /@media\(max-width:400px\)\{main\{padding-left:14px;padding-right:14px\}/);
  assert.match(css, /\.story-content\{padding:18px 18px 9px\}\.story-image\{height:166px\}/);
  assert.match(css, /@media\(min-width:600px\)\{\.story-image\{height:245px\}\}/);
  assert.match(css, /@media\(min-width:960px\)\{main\{width:min\(100%,1912px\)/);
  assert.match(css, /grid-template-columns:repeat\(2,minmax\(0,448px\)\)/);
  assert.match(css, /@media\(min-width:1400px\)\{\.feed\{grid-template-columns:repeat\(3,minmax\(0,448px\)\)/);
  assert.match(css, /@media\(min-width:1880px\)\{\.feed\{grid-template-columns:repeat\(4,minmax\(0,448px\)\)/);
  assert.match(css, /\.archive-heading,\.feed>\.empty\{grid-column:1\/-1\}/);
});

// A deliberately small DOM harness executes the real serialized reader. These
// checks cover state transitions without pretending to validate browser layout.
function simulateReader(input = readerFixture()) {
  const cards = createCards(prepareFeed(input, now));
  class Element {
    constructor() { this.children = []; this.hidden = false; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.textContent = ''; this.html = ''; }
    addEventListener(event, callback) { this.listeners[event] = callback; }
    setAttribute(key, value) { this.attributes[key] = value; }
    append(child) { this.children.push(child); }
    replaceChildren() { this.children = []; this.html = ''; }
    insertAdjacentHTML(_where, html) { this.html += html; }
    querySelectorAll() { return []; }
    click() { this.listeners.click?.(); }
  }
  const ids = ['cards', 'feed', 'more', 'load-more', 'end', 'filter-status', 'end-count', 'edition-count', 'volume-count', 'shortfall', 'volume-target', 'stale'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  const keys = ['all', 'tech', 'world', 'hong-kong', 'steam', 'cve'];
  const labels = ['All', 'Tech', 'World', 'Hong Kong', 'Steam', 'CVE'];
  const filters = keys.map((key, index) => ({ key, label: labels[index] }));
  const controls = keys.map(key => { const el = new Element(); el.dataset.feedFilter = key; return el; });
  elements.cards.textContent = JSON.stringify({ cards, filters, pageSize: 16, archiveHeading: '<div class="archive-heading"></div>', updatedAt: UPDATED_AT });
  const observers = [];
  class Observer {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe() {}
    disconnect() { this.disconnected = true; }
  }
  const timers = new Map();
  let timerId = 0;
  let clock = now;
  const context = {
    document: {
      getElementById: id => elements[id],
      querySelectorAll: selector => selector === '[data-feed-filter]' ? controls : [],
      createElement: () => new Element(),
    },
    window: { IntersectionObserver: Observer, setTimeout: callback => { timers.set(++timerId, callback); return timerId; }, clearTimeout: id => timers.delete(id) },
    IntersectionObserver: Observer,
    Date: { now: () => clock, parse: Date.parse },
  };
  vm.runInNewContext(`(${startReader.toString()})();`, context);
  return {
    elements, controls, observers, timers,
    select: key => controls.find(control => control.dataset.feedFilter === key).click(),
    articleIds: () => [...elements.feed.html.matchAll(/data-story-id="([^"]+)"/g)].map(match => match[1]),
    advance(hours) { clock = now + hours * 3600000; for (const callback of [...timers.values()]) callback(); },
  };
}

test('reader simulation filters the complete dataset before paging and resets selection state', () => {
  const ui = simulateReader();
  assert.equal(ui.articleIds().length, 16);
  ui.elements['load-more'].click();
  assert.equal(ui.articleIds().length, 32);
  ui.select('world');
  assert.equal(ui.articleIds().length, 16);
  assert.ok(ui.articleIds().every(id => id.startsWith('world-')));
  assert.equal(ui.elements['edition-count'].textContent, '21 則');
  assert.equal(ui.elements['load-more'].textContent, '載入更多 · 還有 5 則');
  const previousObserver = ui.observers[0];
  assert.equal(previousObserver.disconnected, true);
  previousObserver.callback([{ isIntersecting: true }]);
  assert.equal(ui.articleIds().length, 16);
  ui.observers.at(-1).callback([{ isIntersecting: true }]);
  assert.equal(ui.articleIds().length, 21);
  assert.equal(ui.elements.more.hidden, true);
  assert.equal(ui.elements.end.hidden, false);
  assert.equal((ui.elements.feed.html.match(/archive-heading/g) || []).length, 1);
  ui.select('steam');
  assert.equal(ui.articleIds().length, 16);
  assert.ok(ui.articleIds().every(id => id.startsWith('deal-')));
  assert.equal(ui.elements['edition-count'].textContent, '20 則');
  assert.equal(ui.elements['volume-count'].textContent, '近三日 · 72 小時 · 0 則 · 20 個優惠');
  ui.elements['load-more'].click();
  assert.equal(ui.articleIds().length, 20);
  ui.select('cve');
  assert.deepEqual(ui.articleIds(), ['validated-cve']);
  ui.select('hong-kong');
  assert.equal(ui.articleIds().length, 6);
  ui.select('tech');
  assert.equal(ui.articleIds().length, 16);
  assert.ok(ui.articleIds().includes('tech-0'));
  ui.select('all');
  assert.equal(ui.articleIds().length, 16);
  const observerCount = ui.observers.length;
  ui.select('all');
  assert.equal(ui.observers.length, observerCount);
  assert.equal(ui.controls.filter(control => control.attributes['aria-pressed'] === 'true').length, 1);
});

test('reader simulation shows an empty category and removes offers at their true expiry', () => {
  const input = { ...readerFixture(), stories: [fixtureStory('news'), fixtureDeal('expiring', 71)] };
  const ui = simulateReader(input);
  ui.select('hong-kong');
  assert.equal(ui.articleIds().length, 0);
  assert.equal(ui.elements.feed.children[0].className, 'empty');
  assert.equal(ui.elements['filter-status'].textContent, 'Hong Kong · 顯示 0 / 0 則');
  assert.equal(ui.elements.more.hidden, true);
  assert.equal(ui.elements.end.hidden, true);
  ui.select('steam');
  assert.deepEqual(ui.articleIds(), ['expiring']);
  ui.advance(1);
  assert.deepEqual(ui.articleIds(), []);
  assert.equal(ui.elements['edition-count'].textContent, '0 則');
  ui.select('all');
  assert.deepEqual(ui.articleIds(), ['news']);
});

test('rebuilding an old edition excludes offers against actual build time', () => {
  const input = { ...readerFixture(), stories: [fixtureStory('news'), fixtureDeal('old-edition-offer', 30)] };
  assert.equal(prepareFeed(input, now).stories.length, 2);
  assert.deepEqual(prepareFeed(input, now + 48 * 3600000).stories.map(story => story.id), ['news']);
});

async function playwrightModule() {
  try { const mod = await import('playwright'); return mod.default || mod; }
  catch {
    try { const mod = await import(process.env.PLAYWRIGHT_MODULE || '/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js'); return mod.default || mod; }
    catch { return null; }
  }
}

async function browserFixture(t) {
  const playwright = await playwrightModule();
  if (!playwright) { t.skip('Playwright is not installed; set PLAYWRIGHT_MODULE for browser fixture QA.'); return null; }
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'news-reader-fixtures-'));
  t.after(() => fs.rm(tmp, { recursive: true, force: true }));
  await Promise.all(['data', 'app', 'public'].map(dir => fs.mkdir(path.join(tmp, dir))));
  await fs.copyFile(path.join(root, 'app/globals.css'), path.join(tmp, 'app/globals.css'));
  await fs.copyFile(path.join(root, 'public/favicon.svg'), path.join(tmp, 'public/favicon.svg'));
  await fs.writeFile(path.join(tmp, 'data/feed.json'), JSON.stringify(readerFixture()));
  await buildStatic({ rootDir: tmp, now });
  const html = await fs.readFile(path.join(tmp, 'dist/index.html'), 'utf8');
  assert.ok(!html.includes('<script>window.__unsafe'));
  const server = http.createServer(async (req, res) => {
    const file = req.url === '/' ? 'index.html' : req.url.slice(1);
    try {
      const data = await fs.readFile(path.join(tmp, 'dist', file));
      res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html');
      res.end(data);
    } catch { res.statusCode = 404; res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await playwright.chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] });
  t.after(() => browser.close());
  const context = await browser.newContext();
  await context.addInitScript(time => { window.__fixtureNow = time; Date.now = () => window.__fixtureNow; }, now);
  await context.route('**/*', route => {
    if (route.request().url().startsWith('http://127.0.0.1:')) return route.continue();
    if (route.request().url().endsWith('/logo.svg')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="20"><rect width="80" height="20" fill="#234"/></svg>' });
    return route.abort();
  });
  return { browser, context, base: `http://127.0.0.1:${server.address().port()}`, tmp };
}

async function pauseAutoLoad(page) {
  await page.addInitScript(() => {
    window.__observers = [];
    window.IntersectionObserver = class {
      constructor(callback) { this.callback = callback; window.__observers.push(this); }
      observe() {}
      disconnect() { this.disconnected = true; }
    };
  });
}

const cardIds = page => page.locator('#feed article').evaluateAll(articles => articles.map(article => article.dataset.storyId));

test('Chromium fixtures: full-data filtering, page resets, empty states, accessibility and observer races', async t => {
  const fixture = await browserFixture(t);
  if (!fixture) return;
  const page = await fixture.context.newPage();
  await pauseAutoLoad(page);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(fixture.base);
  await page.waitForFunction(() => !document.querySelector('[data-feed-filter="all"]').disabled);
  assert.equal(await page.locator('#feed article').count(), 16);
  assert.equal(await page.locator('[aria-pressed="true"]').textContent(), 'All');
  assert.equal(await page.locator('[data-story-id="tech-0"] .story-image img').count(), 0);
  assert.equal(await page.locator('.source-brand').count(), 1);
  assert.match(await page.locator('.publisher-attribution').textContent(), /Fixture Original Publisher/);
  assert.equal(await page.evaluate(() => window.__unsafe), undefined);
  assert.equal(await page.locator('[data-story-id="publisher-preserved"] .summary').textContent(), readerFixture().stories[1].summary);
  await page.locator('#load-more').click();
  assert.equal(await page.locator('#feed article').count(), 32);
  await page.locator('[data-feed-filter="world"]').click();
  assert.equal(await page.locator('#feed article').count(), 16);
  assert.ok((await cardIds(page)).every(id => id.startsWith('world-')));
  assert.equal(await page.locator('#edition-count').textContent(), '21 則');
  assert.equal(await page.locator('#load-more').textContent(), '載入更多 · 還有 5 則');
  await page.evaluate(() => window.__observers[0].callback([{ isIntersecting: true }]));
  assert.equal(await page.locator('#feed article').count(), 16, 'stale All observer cannot append after switching');
  await page.evaluate(() => window.__observers.at(-1).callback([{ isIntersecting: true }]));
  assert.equal(await page.locator('#feed article').count(), 21);
  assert.equal(await page.locator('.archive-heading').count(), 1);
  assert.equal(await page.locator('#more').isHidden(), true);
  assert.equal(await page.locator('#end-count').textContent(), '已讀完 21 則');
  await page.locator('[data-feed-filter="steam"]').click();
  assert.equal(await page.locator('#feed article').count(), 16);
  assert.ok((await cardIds(page)).every(id => id.startsWith('deal-')));
  assert.equal(await page.locator('#edition-count').textContent(), '20 則');
  assert.equal(await page.locator('.archive-heading').count(), 0);
  assert.equal(await page.locator('.price-check-failed').count(), 1);
  assert.equal(await page.locator('[data-story-id="deal-1"] .price-stale').isVisible(), true);
  await page.locator('#load-more').click();
  assert.equal(await page.locator('#feed article').count(), 20);
  await page.locator('[data-feed-filter="cve"]').focus();
  await page.keyboard.press('Enter');
  assert.deepEqual(await cardIds(page), ['validated-cve']);
  assert.equal(await page.locator('[data-feed-filter="cve"]').getAttribute('aria-pressed'), 'true');
  await page.locator('[data-feed-filter="hong-kong"]').click();
  assert.equal(await page.locator('#feed article').count(), 6);
  await page.locator('[data-feed-filter="tech"]').click();
  assert.equal(await page.locator('#feed article').count(), 16);
  assert.ok((await cardIds(page)).includes('tech-0'));
  await page.locator('[data-feed-filter="all"]').click();
  assert.equal(await page.locator('#feed article').count(), 16);
  assert.equal(await page.locator('.number').first().textContent(), '01');
  // Re-selecting the same button does not duplicate cards or create an observer.
  const observers = await page.evaluate(() => window.__observers.length);
  await page.locator('[data-feed-filter="all"]').click();
  assert.equal(await page.evaluate(() => window.__observers.length), observers);
  // Exercise a category with no matching cards using a synthetic payload variant.
  const emptyPage = await fixture.context.newPage();
  await pauseAutoLoad(emptyPage);
  await emptyPage.route('**/', async route => {
    const response = await route.fetch();
    const html = await response.text();
    const payload = html.match(/<script type="application\/json" id="cards">(.*?)<\/script>/s);
    const parsed = JSON.parse(payload[1]);
    parsed.cards.forEach(entry => { entry.filters = entry.filters.filter(key => key !== 'hong-kong'); });
    await route.fulfill({ response, body: html.replace(payload[1], JSON.stringify(parsed).replaceAll('<', '\\u003c')) });
  });
  await emptyPage.goto(fixture.base);
  await emptyPage.locator('[data-feed-filter="hong-kong"]').click();
  assert.equal(await emptyPage.locator('#feed article').count(), 0);
  assert.equal(await emptyPage.locator('#feed .empty').count(), 1);
  assert.equal(await emptyPage.locator('#more').isHidden(), true);
  assert.equal(await emptyPage.locator('#filter-status').textContent(), 'Hong Kong · 顯示 0 / 0 則');
  assert.deepEqual(errors, []);
});

test('Chromium fixtures: desktop 4/3/2 columns, archive span and unchanged mobile cards', async t => {
  const fixture = await browserFixture(t);
  if (!fixture) return;
  const page = await fixture.context.newPage();
  await pauseAutoLoad(page);
  for (const [width, columns] of [[1920, 4], [1440, 3], [1024, 2]]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(fixture.base);
    const layout = await page.locator('#feed').evaluate(feed => ({
      columns: getComputedStyle(feed).gridTemplateColumns.split(' ').length,
      cardWidth: feed.querySelector('article').getBoundingClientRect().width,
      overflow: document.documentElement.scrollWidth > innerWidth,
    }));
    assert.equal(layout.columns, columns);
    assert.ok(Math.abs(layout.cardWidth - 448) < 1);
    assert.equal(layout.overflow, false);
    await page.locator('[data-feed-filter="world"]').click();
    await page.locator('#load-more').click();
    const span = await page.locator('.archive-heading').evaluate(heading => ({
      width: heading.getBoundingClientRect().width,
      first: getComputedStyle(heading).gridColumnStart,
      last: getComputedStyle(heading).gridColumnEnd,
    }));
    assert.equal(span.first, '1');
    assert.equal(span.last, '-1');
    assert.ok(span.width >= columns * 448);
  }
  for (const width of [390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    const measure = () => page.locator('#feed article').first().evaluate(article => ({
      width: article.getBoundingClientRect().width,
      image: article.querySelector('.story-image').getBoundingClientRect().height,
      padding: getComputedStyle(article.querySelector('.story-content')).padding,
      heading: getComputedStyle(article.querySelector('h2')).fontSize,
      columns: getComputedStyle(article.parentElement).gridTemplateColumns.split(' ').length,
      overflow: document.documentElement.scrollWidth > innerWidth,
    }));
    await page.goto(fixture.base);
    // Baseline values from the existing mobile stylesheet, kept independent of
    // HEAD so this regression remains meaningful after the change is committed.
    const expected = width === 390
      ? { width: 362, image: 166, padding: '18px 18px 9px', heading: '21px', columns: 1, overflow: false }
      : { width: 640, image: 245, padding: '21px 22px 12px', heading: '22px', columns: 1, overflow: false };
    assert.deepEqual(await measure(), expected);
  }
});

test('Chromium fixtures: actual infinite scroll loads the next filtered page', async t => {
  const fixture = await browserFixture(t);
  if (!fixture) return;
  const page = await fixture.context.newPage();
  await page.setViewportSize({ width: 1024, height: 700 });
  await page.goto(fixture.base);
  await page.locator('[data-feed-filter="world"]').click();
  assert.equal(await page.locator('#feed article').count(), 16);
  await page.locator('#more').scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelectorAll('#feed article').length === 21);
  assert.equal(await page.locator('.archive-heading').count(), 1);
  assert.equal(await page.locator('#more').isHidden(), true);
});


test('CVE cards preserve supplied rating and metrics, escape text, and reject unsafe related links', () => {
  const story = { ...fixtureStory('synthetic-cve', 2), category: '資安',
    cveRating: '<img onerror=alert(1)>',
    cvss: [{ version: '3.1', score: 9.8, source: '<script>', vector: 'CVSS:3.1/<script>' }],
    relatedArticles: [
      { url: 'javascript:alert(1)', title: 'Unsafe' },
      { url: 'https://user:password@example.com/', title: 'Credentials' },
      { url: 'https://example.com/story', title: '<script>', source: 'Fixture' },
    ],
  };
  const html = card(story, 0);
  assert.ok(html.includes('&lt;img onerror=alert(1)&gt;'));
  assert.ok(html.includes('CVSS 3.1 · 9.8'));
  assert.ok(html.includes('CVSS:3.1/&lt;script&gt;'));
  assert.ok(html.includes('https://example.com/story'));
  assert.ok(!html.includes('javascript:'));
  assert.ok(!html.includes('user:password'));
  assert.ok(!html.includes('<script>'));
  assert.equal(safeHttps('https://example.com/\nfoo'), false);
  assert.throws(() => card({ ...story, url: 'javascript:alert(1)' }, 0));
  assert.ok(!card({ ...story, cvss: undefined, cveRating: undefined, relatedArticles: undefined }, 0).includes('cve-metrics'));
});
