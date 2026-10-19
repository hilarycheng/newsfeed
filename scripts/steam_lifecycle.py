"""Persistent Steam offer observations, independent of the visible 72-hour feed.

Keep data/steam-deal-history.json in source archives/commits. Deleting that file
would discard the evidence that prevents unchanged offers becoming new again.
A migration's unknown firstSeen is the migration time, explicitly labelled as a
baseline. Historical checkedAt values are price evidence, never invented news.
Only a numeric payable-price or discount-percentage change advances novelty.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import pathlib
import re
import tempfile

UTC = dt.timezone.utc
SCHEMA_VERSION = 1
MIGRATION_POLICY = (
    "Unknown firstSeen uses the migration baseline time, not a historical sale "
    "start. Existing checkedAt is retained only as price-check evidence. "
    "Unchanged checks never reset the 72-hour news window."
)


def parse_time(value):
    """Accept only timezone-aware timestamps, never date-only sale end guesses."""
    if not isinstance(value, (str, dt.datetime)):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def stamp(value):
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError('A timezone-aware observation time is required')
    return parsed.isoformat().replace('+00:00', 'Z')


def offer_key(app_id, region='HK', currency='HKD'):
    app_id = str(app_id)
    region, currency = str(region).upper(), str(currency).upper()
    if not app_id.isdigit() or int(app_id) <= 0 or not re.fullmatch(r'[A-Z]{2}', region) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('Invalid Steam app/region/currency identity')
    return f'{int(app_id)}:{region}:{currency}'


def _identity(row):
    return str(row['appId']), str(row.get('storeRegion', 'HK')).upper(), str(row.get('currency', 'HKD')).upper()


def _price(row):
    """An API integer is evidence; a localized display string is not."""
    price = row.get('price') or {}
    final, initial, discount = (price.get(field) for field in ('finalMinor', 'initialMinor', 'discountPercent'))
    if any(type(value) is not int for value in (final, initial, discount)):
        return None
    if not (0 <= final <= initial and 0 <= discount <= 100):
        return None
    return {'finalMinor': final, 'initialMinor': initial, 'discountPercent': discount}


def _meaningful_change(previous, current):
    return bool(previous and current and (
        previous.get('finalMinor') != current['finalMinor'] or
        previous.get('discountPercent') != current['discountPercent']
    ))


def _entry(app_id, region, currency):
    return {
        'appId': app_id, 'storeRegion': region, 'currency': currency,
        'firstSeen': None, 'firstSeenBasis': None, 'lastSeen': None,
        'priceChangedAt': None, 'checkedAt': None, 'lastAttemptAt': None,
        'lastCheckStatus': None, 'active': False, 'inactiveSince': None,
        'price': None, 'snapshot': None,
    }


def new_history(now, legacy_cards=()):
    """One-time baseline for legacy cards, without guessing their sale age."""
    now = parse_time(now)
    if now is None:
        raise ValueError('A timezone-aware migration time is required')
    baseline = stamp(now)
    history = {
        'schemaVersion': SCHEMA_VERSION, 'initializedAt': baseline,
        'migration': {'baselineAt': baseline, 'policy': MIGRATION_POLICY},
        'offers': {},
    }
    for card in legacy_cards:
        if card.get('kind') != 'deal':
            continue
        deal = card.get('deal') or {}
        app_id = deal.get('appId') or card.get('appId')
        if not app_id:
            match = re.search(r'/app/(\d+)(?:/|$)', card.get('url', ''))
            app_id = match.group(1) if match else None
        region, currency = deal.get('storeRegion', 'HK'), deal.get('currency', 'HKD')
        try:
            key = offer_key(app_id, region, currency)
        except ValueError:
            continue
        app_id, region, currency = key.split(':')
        first = parse_time(deal.get('firstSeen') or card.get('firstSeen'))
        first_known = first is not None and first <= now
        first = first if first_known else now
        checked = parse_time(deal.get('checkedAt') or card.get('checkedAt'))
        checked = checked if checked is not None and checked <= now else None
        changed = parse_time(deal.get('priceChangedAt') or card.get('priceChangedAt'))
        changed = changed if first_known and changed is not None and first <= changed <= now else None
        last = parse_time(deal.get('lastSeen') or card.get('lastSeen')) or checked
        last = last if last is not None and last <= now else None
        entry = _entry(app_id, region, currency)
        entry.update(
            firstSeen=stamp(first),
            firstSeenBasis=(deal.get('firstSeenBasis') or 'observed') if first_known else 'migration_baseline',
            lastSeen=stamp(last) if last else None,
            priceChangedAt=stamp(changed) if changed else None,
            checkedAt=stamp(checked) if checked else None,
            lastAttemptAt=stamp(checked) if checked else None,
            lastCheckStatus='migrated', active=deal.get('active', True) is not False,
            price=_price({'price': deal}), snapshot=copy.deepcopy(card),
        )
        history['offers'][key] = entry
    return history


def load_history(path, now, legacy_cards=()):
    path = pathlib.Path(path)
    if not path.exists():
        return new_history(now, legacy_cards)
    # Fail closed rather than accidentally re-baselining all offers after damage.
    history = json.loads(path.read_text())
    if not isinstance(history, dict) or history.get('schemaVersion') != SCHEMA_VERSION or not isinstance(history.get('offers'), dict):
        raise ValueError('Steam history schema is invalid; preserve and repair it instead of resetting freshness')
    for key, entry in history['offers'].items():
        if not isinstance(entry, dict) or offer_key(*_identity(entry)) != key:
            raise ValueError('Steam history identity is invalid')
        for field in ('firstSeen', 'lastSeen', 'priceChangedAt', 'checkedAt', 'lastAttemptAt', 'inactiveSince'):
            if entry.get(field) is not None and parse_time(entry[field]) is None:
                raise ValueError(f'Steam history {field} timestamp is invalid')
        if entry.get('active') and (not entry.get('firstSeen') or not isinstance(entry.get('snapshot'), dict)):
            raise ValueError('Active Steam history is missing its original observation')
    return history


def save_history(path, history):
    """Atomic replacement keeps interrupted writes from resetting offer history."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as output:
            temporary = pathlib.Path(output.name)
            json.dump(history, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _card(entry):
    if not entry.get('snapshot') or not entry.get('firstSeen'):
        return None
    card = copy.deepcopy(entry['snapshot'])
    first, changed = parse_time(entry['firstSeen']), parse_time(entry.get('priceChangedAt'))
    news_at = stamp(max(first, changed)) if changed else stamp(first)
    card.update(firstSeen=entry['firstSeen'], lastSeen=entry['lastSeen'],
                priceChangedAt=entry['priceChangedAt'], freshnessAt=news_at,
                checkedAt=entry['checkedAt'], publishedAt=None)
    deal = card.setdefault('deal', {})
    deal.update({field: entry.get(field) for field in (
        'appId', 'storeRegion', 'currency', 'firstSeen', 'firstSeenBasis',
        'lastSeen', 'priceChangedAt', 'checkedAt', 'lastAttemptAt',
        'lastCheckStatus', 'active', 'inactiveSince',
    )})
    deal['newsAt'] = news_at
    if entry.get('price'):
        deal.update(entry['price'])
    return card


def reconcile_observations(history, observations, now):
    """Apply verified observations and return cache rows, including outage cards.

    firstSeen is set once at the first discounted observation (or migration).
    lastSeen advances only for a confirmed discount. checkedAt advances only
    for a complete successful price response. Failed/missing responses do not
    prove an offer ended and never refresh either price evidence or novelty.
    """
    now = parse_time(now)
    if now is None:
        raise ValueError('A timezone-aware observation time is required')
    when = stamp(now)
    output = []
    for original in observations:
        row = copy.deepcopy(original)
        app_id, region, currency = _identity(row)
        key = offer_key(app_id, region, currency)
        app_id, region, currency = key.split(':')
        row.update(appId=app_id, storeRegion=region, currency=currency, lastAttemptAt=when)
        entry = history['offers'].get(key)
        status, price = row.get('status'), _price(row)
        confirmed_deal = status == 'deal' and price is not None and price['discountPercent'] > 0 and isinstance(row.get('card'), dict)
        confirmed_ended = status == 'not_on_sale' and price is not None and price['discountPercent'] == 0
        if status in ('deal', 'not_on_sale') and not (confirmed_deal or confirmed_ended):
            row.update(status='failed', error='Incomplete or inconsistent price observation')
            status = 'failed'
        if entry is None and (confirmed_deal or confirmed_ended):
            entry = _entry(app_id, region, currency)
            history['offers'][key] = entry
        if entry is not None:
            previous_attempt = parse_time(entry.get('lastAttemptAt'))
            # A cached/older run must not roll newer evidence backwards.
            if previous_attempt is not None and now < previous_attempt:
                row['status'] = 'out_of_order'
            else:
                entry.update(lastAttemptAt=when, lastCheckStatus=status)
                if confirmed_deal or confirmed_ended:
                    if entry['firstSeen'] and _meaningful_change(entry.get('price'), price):
                        entry['priceChangedAt'] = when
                    entry.update(price=price, checkedAt=when)
                if confirmed_deal:
                    if entry['firstSeen'] is None:
                        entry.update(firstSeen=when, firstSeenBasis='observed')
                    entry.update(lastSeen=when, active=True, inactiveSince=None, snapshot=copy.deepcopy(row['card']))
                elif confirmed_ended:
                    if entry['active'] or entry['inactiveSince'] is None:
                        entry['inactiveSince'] = when
                    entry['active'] = False
            card = _card(entry)
            if card is not None:
                row['card'] = card
            else:
                row.pop('card', None)
            row['active'] = entry['active']
        else:
            row.pop('card', None)
            row['active'] = False
        output.append(row)
    return output


def select_visible_deals(rows, now, window_hours=72):
    """Only active, timestamped, unexpired novelty enters the mixed news feed."""
    now = parse_time(now)
    if now is None:
        raise ValueError('A timezone-aware visibility time is required')
    cards, seen = [], set()
    for row in rows:
        card = row.get('card')
        if not isinstance(card, dict):
            continue
        deal = card.get('deal') or {}
        if deal.get('active') is not True:
            continue
        first, changed = parse_time(deal.get('firstSeen')), parse_time(deal.get('priceChangedAt'))
        checked = parse_time(deal.get('checkedAt') or card.get('checkedAt'))
        if first is None or checked is None or checked > now:
            continue
        news_at = max(first, changed) if changed else first
        age = now - news_at
        if not dt.timedelta(0) <= age < dt.timedelta(hours=window_hours):
            continue
        end = parse_time(deal.get('saleEnd'))
        if end is not None and end <= now:
            continue
        try:
            key = offer_key(deal.get('appId'), deal.get('storeRegion'), deal.get('currency'))
        except ValueError:
            continue
        if key in seen:
            continue
        seen.add(key)
        selected = copy.deepcopy(card)
        selected['freshnessAt'] = selected['deal']['newsAt'] = stamp(news_at)
        cards.append(selected)
    return cards
