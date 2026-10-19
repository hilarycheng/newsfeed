#!/usr/bin/env python3
"""Persist an exact, already identified RSS denial. Offline; never retries or lifts holds."""
import argparse
import datetime as dt
import json
import pathlib
from source_control import controls, normalized_host
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
REASON = '暫時略過：此確切 RSS 網址的讀取被存取審核拒絕；不再重試，其他已批准來源繼續。'


def hold_source(root, url):
    registry = json.loads((root / 'config/source-registry.json').read_text())
    sources = [s for s in registry if s.get('enabled', True) and s['url'] == url]
    if len(sources) != 1:
        raise ValueError('Denial must identify exactly one enabled registered RSS URL; stop for review')
    policy = controls(root)
    holds = policy.setdefault('rss', {})
    if not isinstance(holds, dict):
        raise ValueError('Invalid RSS holds; stop for review')
    changed = url not in holds
    if changed:
        holds[url] = REASON
        policy['updatedAt'] = dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')
        path = root / 'config/source-holds.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
    return {'status': 'held', 'changed': changed, 'source': sources[0]['name'],
            'url': url, 'networkRequestsMade': 0, 'existingHoldsPreserved': True}


def hold_domain(root, hostname):
    host = normalized_host(hostname)
    registry = json.loads((root / 'config/source-registry.json').read_text())
    sources = [s for s in registry if normalized_host(urlsplit(s['url']).hostname) == host]
    if not sources:
        raise ValueError('Denied hostname must match an existing registered RSS destination; stop for review')
    policy = controls(root)
    holds = policy.setdefault('deniedHosts', {})
    if not isinstance(holds, dict):
        raise ValueError('Invalid hostname holds; stop for review')
    changed = host not in holds
    if changed:
        holds[host] = '暫時略過：存取審核拒絕此 hostname；證據只提供 domain，並非精確 URL。所有同 hostname 請求均禁止。'
        policy['updatedAt'] = dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')
        path = root / 'config/source-holds.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
    return {'status': 'held', 'changed': changed, 'evidenceType': 'hostname',
            'hostname': host, 'registeredSourceCount': len(sources),
            'networkRequestsMade': 0, 'existingHoldsPreserved': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', nargs='?', help='Exact denied URL, verified against enabled RSS registry')
    parser.add_argument('--hostname', help='Explicit domain-only denial, exact normalized hostname match')
    args = parser.parse_args()
    try:
        if bool(args.url) == bool(args.hostname):
            raise ValueError('Supply exactly one URL or --hostname')
        print(json.dumps(hold_domain(ROOT, args.hostname) if args.hostname else hold_source(ROOT, args.url), ensure_ascii=False))
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
