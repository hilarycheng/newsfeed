"""Explicit source holds and bounded offline intake. No network or retry logic."""
import datetime as dt
import json
import re
import urllib.parse

MAX_CACHE_AGE = dt.timedelta(hours=1)

def controls(root):
    path = root / 'config/source-holds.json'
    value = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError('Invalid source holds')
    return value

def require_recent(when, now):
    value = dt.datetime.fromisoformat(when.replace('Z', '+00:00'))
    if value.tzinfo is None or not now - MAX_CACHE_AGE <= value <= now:
        raise ValueError('Intake cache outside one-hour regeneration window')
    return when

def normalized_host(host):
    if not isinstance(host, str) or host != host.strip():
        raise ValueError('Invalid denied hostname')
    host = host.lower().removesuffix('.')
    if len(host) > 253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+', host):
        raise ValueError('An exact DNS hostname without URL, port or wildcard is required')
    return host


def held_host(url, policy):
    host = urllib.parse.urlsplit(url).hostname
    return policy.get('deniedHosts', {}).get(normalized_host(host)) if host else None


def held_rss(source, policy):
    return held_host(source['url'], policy) or policy.get('rss', {}).get(source['url'])

def filter_intake(raw, registry, policy, now, cached=False):
    # Failed and explicitly held sources cannot supply cards, even from cache.
    fetched = require_recent(raw['fetchedAt'], now)
    by_name = {s['name']: s for s in registry}
    statuses = []
    for original in raw['sourceStatus']:
        row = dict(original)
        source = by_name.get(row['name'], {})
        reason = held_rss(source, policy) if source.get('url') else None
        if reason:
            row.update(status='skipped', detail=reason, candidateCount=0)
        row.update(fetchedAt=fetched, cached=cached)
        statuses.append(row)
    allowed = {s['name'] for s in statuses if s['status'] == 'ok'}
    return [a for a in raw['candidates'] if a['source'] in allowed], statuses

def cve_selection(root, now, cards, policy, cached, refresh, read_state, merge):
    state = read_state(root)
    reason = policy.get('cve')
    if reason:
        counts = dict(state.get('lastResult') or {})
        counts.update(status='skipped', pending=len(state.get('pending', {})), cached=cached)
    elif cached:
        counts = dict(state.get('lastResult') or {'status': 'failure'})
        counts['cached'] = True
    else:
        # Unexpected engine exceptions remain fatal. The adapter reports only
        # its own bounded source result; a reviewer denial is never retried here.
        counts = refresh(root, now, extra_ids=sorted({c for a in cards for c in a.get('cveIds', [])}))
        state = read_state(root)
    if counts.get('status') != 'success':
        # Omit every CVE-dependent card while intake is incomplete. No cached
        # projection can resurrect rejected, unverified or expired records.
        return [a for a in cards if not a.get('cveIds')], counts, {
            'name': 'CVE List V5', 'status': 'skipped' if reason else 'failed',
            'detail': reason or '官方 CVE JSON 讀取未完成；本期略過 CVE，保留待處理進度。',
            'pending': len(state.get('pending', {})), 'cached': cached,
            'lastAttemptAt': state.get('lastAttemptAt')}
    return merge(cards, state, now), counts, {'name': 'CVE List V5', 'status': 'ok', 'cached': cached,
        'detail': '官方 CVE JSON；依首次公布日期與既有重大開源漏洞規則篩選。'}
