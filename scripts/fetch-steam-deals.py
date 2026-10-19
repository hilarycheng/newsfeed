#!/usr/bin/env python3
"""Read-only Steam price checks; persist freshness, never publish or purchase."""
import concurrent.futures
import datetime as dt
import fcntl
import json
import pathlib
import urllib.parse
import urllib.request

from source_control import controls
from source_gateway import open_source
from steam_lifecycle import load_history, reconcile_observations, save_history, select_visible_deals, stamp

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISTORY_FILE = 'steam-deal-history.json'


def fetch(item, now=None):
    when = stamp(now or dt.datetime.now(dt.timezone.utc))
    app = str(item['appId'])
    region, currency = item.get('storeRegion', 'HK').upper(), item.get('currency', 'HKD').upper()
    result_row = {'appId': app, 'storeRegion': region, 'currency': currency, 'lastAttemptAt': when}
    try:
        query = urllib.parse.urlencode({'appids': app, 'cc': region.lower(), 'l': 'english'})
        with open_source(ROOT, 'steam', app, 'https://store.steampowered.com/api/appdetails?' + query, headers={'User-Agent': 'NewsFeed/1.0'}, timeout=25) as response:
            raw = json.load(response)
        result = raw.get(app, {})
        if result.get('success') is not True:
            raise ValueError('App data unavailable')
        data = result['data']
        overview = data.get('price_overview')
        if not isinstance(overview, dict) or overview.get('currency') != currency:
            return dict(result_row, status='no_hkd_price' if currency == 'HKD' else 'no_local_price')
        final, initial, discount = (overview.get(field) for field in ('final', 'initial', 'discount_percent'))
        if any(type(value) is not int for value in (final, initial, discount)) or not (0 <= final <= initial and 0 <= discount <= 100):
            raise ValueError('Incomplete or inconsistent price response')
        price = {'finalMinor': final, 'initialMinor': initial, 'discountPercent': discount}
        if not discount:
            return dict(result_row, status='not_on_sale', price=price, checkedAt=when)
        if not any(category.get('id') == 2 for category in data.get('categories', [])):
            raise ValueError('Single-player category not confirmed')
        if not overview.get('final_formatted'):
            raise ValueError('Formatted local price unavailable')
        card = {
            'id': 'steam-' + region.lower() + '-' + currency.lower() + '-' + app,
            'kind': 'deal', 'category': '單機遊戲',
            'title': item['name'] + ' 港區特價 -' + str(discount) + '%',
            'summary': item['summary'], 'source': 'Steam 香港商店',
            'url': 'https://store.steampowered.com/app/' + app + '/?cc=' + region.lower(),
            'publishedAt': None, 'checkedAt': when,
            'imageUrl': data.get('header_image'), 'imageLabel': item['name'] + ' 官方商店封面',
            'deal': {
                'appId': app, 'discountPercent': discount,
                'priceText': overview['final_formatted'], 'originalPriceText': overview.get('initial_formatted'),
                'currency': currency, 'storeRegion': region, 'checkedAt': when,
                'finalMinor': final, 'initialMinor': initial,
            },
        }
        return dict(result_row, status='deal', price=price, checkedAt=when, card=card,
                    notice='API does not provide a verified sale end; check the store page before adding expiry.')
    except Exception as error:
        return dict(result_row, status='failed', error=str(error)[:200])


def main():
    if controls(ROOT).get('networkRefreshHold'):
        raise RuntimeError('Network intake held pending denied-source identification')
    runtime = ROOT / '.sites-runtime'
    runtime.mkdir(exist_ok=True)
    watch = json.loads((ROOT / 'config/steam-deals.json').read_text())
    history_path = ROOT / 'data' / HISTORY_FILE
    # Serialize the read/check/write sequence, avoiding overlapping scheduled
    # refreshes overwriting a newer price observation with older history.
    with (runtime / 'steam-history.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = dt.datetime.now(dt.timezone.utc)
        legacy = json.loads((ROOT / 'data/feed.json').read_text()).get('stories', []) if not history_path.exists() else []
        history = load_history(history_path, now, legacy)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            observations = list(pool.map(lambda item: fetch(item, now), watch))
        candidates = reconcile_observations(history, observations, now)
        # State commits before output so a failed edition cannot erase genuine
        # first observations or turn unchanged prices into new offers later.
        save_history(history_path, history)
        candidate_path = runtime / 'steam-deal-candidates.json'
        save_history(candidate_path, candidates)
    print(json.dumps({
        'candidateFile': str(candidate_path), 'historyFile': str(history_path),
        'deals': sum(row['status'] == 'deal' for row in candidates),
        'visibleDeals': len(select_visible_deals(candidates, now)),
        'checked': len(candidates), 'failed': sum(row['status'] == 'failed' for row in candidates),
    }))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
