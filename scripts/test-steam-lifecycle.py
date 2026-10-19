#!/usr/bin/env python3
"""Offline, fixed-clock Steam history and API regression fixtures."""
import copy
import datetime as dt
import importlib.util
import io
import json
import pathlib
import tempfile
import types
import contextlib
import unittest
from unittest import mock

from steam_lifecycle import (
    load_history, new_history, offer_key, reconcile_observations,
    save_history, select_visible_deals, stamp,
)

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = json.loads((HERE / 'fixtures/steam-lifecycle.json').read_text())
T0 = dt.datetime.fromisoformat(FIXTURE['baselineAt'].replace('Z', '+00:00'))
spec = importlib.util.spec_from_file_location('steam_fetch', HERE / 'fetch-steam-deals.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


def observed(hours=0, response=None):
    raw = copy.deepcopy(FIXTURE['discounted'] if response is None else response)
    with mock.patch.object(f, 'open_source', return_value=io.StringIO(json.dumps(raw))):
        return f.fetch(FIXTURE['watchItem'], T0 + dt.timedelta(hours=hours))


def changed(hours, final=5000, discount=50):
    raw = copy.deepcopy(FIXTURE['discounted'])
    raw['123']['data']['price_overview'].update(final=final, discount_percent=discount, final_formatted=f'HK$ {final / 100:.2f}')
    return observed(hours, raw)


def failed(hours=0, status='failed'):
    return {'appId': '123', 'storeRegion': 'HK', 'currency': 'HKD', 'status': status, 'lastAttemptAt': stamp(T0 + dt.timedelta(hours=hours))}


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.history = new_history(T0)
        self.rows = reconcile_observations(self.history, [observed()], T0)
        self.entry = self.history['offers']['123:HK:HKD']

    def apply(self, row, hours):
        self.rows = reconcile_observations(self.history, [row], T0 + dt.timedelta(hours=hours))
        return self.rows[0]['card']['deal']

    def visible(self, hours):
        return select_visible_deals(self.rows, T0 + dt.timedelta(hours=hours))

    def test_first_observation_is_not_a_price_change(self):
        self.assertEqual(self.entry['firstSeen'], stamp(T0))
        self.assertEqual(self.entry['lastSeen'], stamp(T0))
        self.assertEqual(self.entry['firstSeenBasis'], 'observed')
        self.assertIsNone(self.entry['priceChangedAt'])
        self.assertEqual(self.visible(0)[0]['freshnessAt'], stamp(T0))

    def test_unchanged_refreshes_expire_at_72_hours(self):
        for hours in (1, 24, 48, 72, 120):
            self.apply(observed(hours), hours)
        self.assertEqual(self.entry['firstSeen'], stamp(T0))
        self.assertIsNone(self.entry['priceChangedAt'])
        self.assertEqual(self.entry['lastSeen'], stamp(T0 + dt.timedelta(hours=120)))
        self.assertEqual(self.visible(120), [])
        self.assertEqual(len(self.visible(71)), 0)  # future checkedAt cannot be backdated

    def test_exact_expiry_boundary(self):
        self.assertEqual(len(self.visible(71.999)), 1)
        self.assertEqual(self.visible(72), [])

    def test_actual_payable_price_change_reenters_feed(self):
        self.assertEqual(self.visible(73), [])
        deal = self.apply(changed(96), 96)
        self.assertEqual(deal['priceChangedAt'], stamp(T0 + dt.timedelta(hours=96)))
        self.assertEqual(deal['firstSeen'], stamp(T0))
        self.assertEqual(len(self.visible(96)), 1)
        self.apply(changed(120), 120)
        self.assertEqual(self.entry['priceChangedAt'], stamp(T0 + dt.timedelta(hours=96)))
        self.assertEqual(self.visible(168), [])

    def test_changed_discount_counts_even_if_payable_price_same(self):
        self.apply(changed(24, final=6000, discount=45), 24)
        self.assertEqual(self.entry['priceChangedAt'], stamp(T0 + dt.timedelta(hours=24)))

    def test_metadata_formatting_and_initial_price_only_are_not_novelty(self):
        row = observed(24)
        row['card'].update(title='New display name', imageUrl='https://example.com/changed.png')
        row['card']['deal']['priceText'] = 'HK$60'
        row['price']['initialMinor'] = 10001
        self.apply(row, 24)
        self.assertIsNone(self.entry['priceChangedAt'])
        self.assertEqual(self.entry['checkedAt'], stamp(T0 + dt.timedelta(hours=24)))

    def test_network_failure_retains_last_confirmed_sale(self):
        deal = self.apply(failed(24), 24)
        self.assertTrue(self.entry['active'])
        self.assertEqual(deal['checkedAt'], stamp(T0))
        self.assertEqual(deal['lastSeen'], stamp(T0))
        self.assertEqual(deal['lastAttemptAt'], stamp(T0 + dt.timedelta(hours=24)))
        self.assertEqual(deal['lastCheckStatus'], 'failed')
        self.assertEqual(len(self.visible(24)), 1)
        self.assertEqual(self.visible(73), [])

    def test_missing_currency_is_not_a_sale_end(self):
        self.apply(failed(24, 'no_hkd_price'), 24)
        self.assertTrue(self.entry['active'])
        self.assertEqual(self.entry['checkedAt'], stamp(T0))
        self.assertIsNone(self.entry['priceChangedAt'])

    def test_successfully_verified_full_price_marks_inactive(self):
        self.apply(changed(24, final=10000, discount=0), 24)
        self.assertFalse(self.entry['active'])
        self.assertEqual(self.entry['lastSeen'], stamp(T0))
        self.assertEqual(self.visible(24), [])
        self.apply(failed(48), 48)
        self.assertFalse(self.entry['active'])
        self.assertEqual(self.entry['inactiveSince'], stamp(T0 + dt.timedelta(hours=24)))

    def test_reactivated_sale_keeps_first_seen_and_records_real_change(self):
        self.apply(changed(24, final=10000, discount=0), 24)
        self.apply(observed(120), 120)
        self.assertTrue(self.entry['active'])
        self.assertEqual(self.entry['firstSeen'], stamp(T0))
        self.assertEqual(self.entry['priceChangedAt'], stamp(T0 + dt.timedelta(hours=120)))
        self.assertEqual(len(self.visible(120)), 1)

    def test_incomplete_price_cannot_end_sale_or_fabricate_freshness(self):
        for status in ('not_on_sale', 'deal'):
            row = failed(24, status)
            row['price'] = {'discountPercent': 0}
            deal = self.apply(row, 24)
            self.assertTrue(deal['active'])
            self.assertEqual(deal['checkedAt'], stamp(T0))
            self.assertIsNone(deal['priceChangedAt'])
            self.assertEqual(deal['lastCheckStatus'], 'failed')

    def test_history_survives_feed_pruning_and_reload(self):
        self.apply(observed(100), 100)
        self.assertEqual(self.visible(100), [])
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'history.json'
            save_history(path, self.history)
            loaded = load_history(path, T0 + dt.timedelta(hours=200), legacy_cards=[])
        rows = reconcile_observations(loaded, [observed(200)], T0 + dt.timedelta(hours=200))
        self.assertEqual(select_visible_deals(rows, T0 + dt.timedelta(hours=200)), [])
        self.assertEqual(loaded['offers']['123:HK:HKD']['firstSeen'], stamp(T0))

    def test_region_and_currency_identity_are_independent(self):
        us = observed()
        us.update(storeRegion='US', currency='USD')
        reconcile_observations(self.history, [us], T0)
        self.assertEqual(len(self.history['offers']), 2)
        self.assertEqual(offer_key('00123', 'hk', 'hkd'), '123:HK:HKD')
        self.apply(changed(24), 24)
        self.assertIsNone(self.history['offers']['123:US:USD']['priceChangedAt'])

    def test_older_observation_cannot_roll_back_price_or_time(self):
        self.apply(changed(48), 48)
        self.apply(observed(24), 24)
        self.assertEqual(self.rows[0]['status'], 'out_of_order')
        self.assertEqual(self.entry['price']['finalMinor'], 5000)
        self.assertEqual(self.entry['checkedAt'], stamp(T0 + dt.timedelta(hours=48)))

    def test_checked_at_never_substitutes_for_freshness(self):
        card = self.rows[0]['card']
        card['deal'].pop('firstSeen')
        card.pop('firstSeen')
        self.assertEqual(self.visible(0), [])

    def test_verified_end_expires_but_date_only_does_not_invent_end_time(self):
        self.rows[0]['card']['deal']['saleEnd'] = stamp(T0 + dt.timedelta(hours=1))
        self.assertEqual(self.visible(2), [])
        self.rows[0]['card']['deal']['saleEnd'] = '2026-10-01'
        self.assertEqual(len(self.visible(2)), 1)


class Migration(unittest.TestCase):
    def legacy(self):
        return observed()['card']

    def test_unknown_first_seen_uses_explicit_migration_baseline(self):
        now = T0 + dt.timedelta(hours=240)
        history = new_history(now, [self.legacy()])
        entry = history['offers']['123:HK:HKD']
        self.assertEqual(entry['firstSeen'], stamp(now))
        self.assertEqual(entry['firstSeenBasis'], 'migration_baseline')
        self.assertEqual(entry['checkedAt'], stamp(T0))
        self.assertIsNone(entry['priceChangedAt'])
        self.assertIn('not a historical sale start', history['migration']['policy'])

    def test_baseline_is_not_repeated_on_future_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'history.json'
            history = load_history(path, T0, [self.legacy()])
            save_history(path, history)
            later = T0 + dt.timedelta(hours=100)
            loaded = load_history(path, later, [self.legacy()])
            rows = reconcile_observations(loaded, [observed(100)], later)
            self.assertEqual(loaded['migration']['baselineAt'], stamp(T0))
            self.assertEqual(select_visible_deals(rows, later), [])

    def test_legacy_display_prices_do_not_invent_a_numeric_change(self):
        legacy = self.legacy()
        for field in ('finalMinor', 'initialMinor'):
            legacy['deal'].pop(field, None)
        history = new_history(T0, [legacy])
        self.assertIsNone(history['offers']['123:HK:HKD']['price'])
        reconcile_observations(history, [observed(24)], T0 + dt.timedelta(hours=24))
        entry = history['offers']['123:HK:HKD']
        self.assertEqual(entry['price']['finalMinor'], 6000)
        self.assertIsNone(entry['priceChangedAt'])
        self.assertEqual(entry['firstSeen'], stamp(T0))

    def test_existing_known_first_seen_is_retained(self):
        legacy = self.legacy()
        legacy['deal'].update(firstSeen=stamp(T0), firstSeenBasis='observed')
        history = new_history(T0 + dt.timedelta(hours=100), [legacy])
        self.assertEqual(history['offers']['123:HK:HKD']['firstSeen'], stamp(T0))
        self.assertEqual(history['offers']['123:HK:HKD']['firstSeenBasis'], 'observed')

    def test_corrupt_state_fails_closed_instead_of_resetting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'history.json'
            for body in ('not-json', '{"schemaVersion": 99, "offers": {}}'):
                path.write_text(body)
                with self.assertRaises(ValueError):
                    load_history(path, T0, [self.legacy()])
                self.assertEqual(path.read_text(), body)

    def test_unknown_failure_does_not_claim_first_observation(self):
        history = new_history(T0)
        rows = reconcile_observations(history, [failed()], T0)
        self.assertEqual(history['offers'], {})
        self.assertEqual(select_visible_deals(rows, T0), [])


class Fetch(unittest.TestCase):
    def test_valid_discount_returns_numeric_snapshot(self):
        row = observed()
        self.assertEqual(row['status'], 'deal')
        self.assertEqual(row['price']['finalMinor'], 6000)
        self.assertEqual(row['card']['deal']['currency'], 'HKD')

    def test_no_price_and_unavailable_app_are_not_confirmed_sale_ends(self):
        missing = copy.deepcopy(FIXTURE['discounted'])
        missing['123']['data'].pop('price_overview')
        self.assertEqual(observed(response=missing)['status'], 'no_hkd_price')
        self.assertEqual(observed(response={'123': {'success': False}})['status'], 'failed')

    def test_network_failure_does_not_advance_checked_at(self):
        with mock.patch.object(f, 'open_source', side_effect=TimeoutError('fixture timeout')):
            row = f.fetch(FIXTURE['watchItem'], T0)
        self.assertEqual(row['status'], 'failed')
        self.assertNotIn('checkedAt', row)
        self.assertEqual(row['lastAttemptAt'], stamp(T0))

    def test_invalid_numeric_price_and_single_player_check(self):
        bad = copy.deepcopy(FIXTURE['discounted'])
        bad['123']['data']['price_overview']['final'] = '6000'
        self.assertEqual(observed(response=bad)['status'], 'failed')
        bad = copy.deepcopy(FIXTURE['discounted'])
        bad['123']['data']['categories'] = []
        self.assertEqual(observed(response=bad)['status'], 'failed')


class MainWorkflow(unittest.TestCase):
    def test_persisted_state_survives_a_later_empty_visible_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'config').mkdir()
            (root / 'data').mkdir()
            (root / 'config/steam-deals.json').write_text(json.dumps([FIXTURE['watchItem']]))
            (root / 'data/feed.json').write_text('{"stories": []}')
            for hours in (0, 100):
                now = T0 + dt.timedelta(hours=hours)
                row = observed(hours)
                frozen = types.SimpleNamespace(datetime=types.SimpleNamespace(now=lambda zone: now), timezone=dt.timezone)
                with mock.patch.object(f, 'ROOT', root), mock.patch.object(f, 'dt', frozen), mock.patch.object(f, 'fetch', return_value=row), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(f.main(), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result['visibleDeals'], 1 if hours == 0 else 0)
                history = load_history(root / 'data/steam-deal-history.json', now)
                self.assertEqual(history['offers']['123:HK:HKD']['firstSeen'], stamp(T0))
            self.assertEqual(json.loads((root / '.sites-runtime/steam-deal-candidates.json').read_text())[0]['card']['freshnessAt'], stamp(T0))


if __name__ == '__main__':
    unittest.main()
