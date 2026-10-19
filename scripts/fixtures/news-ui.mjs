// Synthetic fixture content only. No publisher feed or article data is embedded.
export const UPDATED_AT = '2026-10-13T00:00:00Z';
export const hoursAgo = hours => new Date(Date.parse(UPDATED_AT) - hours * 3600000).toISOString();
export function fixtureStory(id, category = '科技', hours = 1, extra = {}) {
  return {
    id, category, source: 'Fixture Publisher', title: `Fixture article ${id}`,
    summary: `Unmodified publisher fixture excerpt ${id}.`,
    url: `https://fixture.invalid/articles/${id}`, publishedAt: hoursAgo(hours),
    sourceLanguage: 'en', ...extra,
  };
}
export function fixtureDeal(id, hours = 24, extra = {}) {
  return fixtureStory(id, '單機遊戲', hours, {
    kind: 'deal', source: 'Fixture Steam Store', publishedAt: null,
    checkedAt: UPDATED_AT, freshnessAt: hoursAgo(hours),
    deal: {
      newsAt: hoursAgo(hours), firstSeen: hoursAgo(hours), firstSeenBasis: 'observed',
      priceChangedAt: null, checkedAt: UPDATED_AT, active: true,
      lastCheckStatus: 'deal', discountPercent: 20, priceText: 'HK$ 80', originalPriceText: 'HK$ 100',
      ...extra,
    },
  });
}
export function readerFixture() {
  const tech = Array.from({ length: 40 }, (_, index) => fixtureStory(`tech-${index}`, index === 0 ? 'Elixir / Erlang' : '科技', index / 12));
  tech[0].imageUrl = 'https://fixture.invalid/broken-image.png';
  tech[1] = fixtureStory('publisher-preserved', '科技', 0.1, {
    title: 'Exact publisher title '.repeat(14),
    summary: 'Exact publisher text & punctuation. '.repeat(20) + '</script><script>window.__unsafe=true</script>',
    preservePublisherText: true, originalPublisher: 'Fixture Original Publisher',
    originalPublisherUrl: 'https://original.fixture.invalid/',
    attributionUrl: 'https://fixture.invalid/publisher', sourceBrandImage: 'https://fixture.invalid/logo.svg',
  });
  const world = Array.from({ length: 20 }, (_, index) => fixtureStory(`world-${index}`, 'World / 國際', 10 + index / 12));
  const hongKong = Array.from({ length: 6 }, (_, index) => fixtureStory(`hk-${index}`, '香港', 16 + index / 12));
  const deals = Array.from({ length: 20 }, (_, index) => fixtureDeal(`deal-${index}`, 24 + index / 12, {
    discountPercent: index + 10,
  }));
  deals[1].deal.lastCheckStatus = 'failed';
  deals[1].deal.checkedAt = hoursAgo(50);
  deals[2].deal.firstSeenBasis = 'migration_baseline';
  const extras = [
    fixtureStory('gaming-news', '單機遊戲', 8, { title: 'Fixture game update on Steam' }),
    fixtureStory('validated-cve', '資安', 13, { title: 'Fixture security notice', cveIds: ['CVE-2026-12345'] }),
    fixtureStory('unvalidated-cve', '科技', 13.1, { title: 'Fixture CVE-2026-98765 mention', cveIds: ['CVE-2026-12'] }),
    fixtureStory('archive-tech', '科技', 80),
    fixtureStory('archive-world', 'World / 國際', 81),
  ];
  return {
    updatedAt: UPDATED_AT, editionDate: '2026-10-13', stories: [...tech, ...world, ...hongKong, ...deals, ...extras],
    sourceStatus: [{ name: 'Fixture Publisher', status: 'ok' }],
  };
}
