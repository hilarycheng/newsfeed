#!/usr/bin/env python3
"""Offline request inventory. Never opens, resolves, retries or approves a URL."""
import hashlib
import json
import pathlib
import urllib.parse
from source_gateway import target_rule, SourcePolicyError
from source_control import controls, held_rss, held_host

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    policy = controls(ROOT)
    registry_path = ROOT / 'lib/source-registry.json'
    watch_path = ROOT / 'config/steam-deals.json'
    rows = []
    for source in json.loads(registry_path.read_text()):
        if not source.get('enabled', True):
            continue
        row = {'adapter': 'rss', 'source': source['name'], 'url': source['url']}
        try:
            target_rule(ROOT, 'rss', source['name'], source['url'])
            row['destinationCheck'] = 'exact_registered_url'
        except SourcePolicyError as error:
            row['destinationCheck'] = str(error)
        row['sourceHeld'] = bool(held_rss(source, policy))
        rows.append(row)
    for item in json.loads(watch_path.read_text()):
        app = str(item['appId'])
        query = urllib.parse.urlencode({'appids': app, 'cc': item.get('storeRegion', 'HK').lower(), 'l': 'english'})
        url = 'https://store.steampowered.com/api/appdetails?' + query
        target_rule(ROOT, 'steam', app, url)
        rows.append({'adapter': 'steam', 'source': app, 'url': url, 'destinationCheck': 'exact_registered_url', 'sourceHeld': bool(held_host(url, policy))})
    proposed = [row for row in rows if not row['sourceHeld'] and row['destinationCheck'] == 'exact_registered_url']
    result = {
        'mode': 'offline_request_inventory', 'networkRequestsMade': 0,
        'globalHoldActive': bool(policy.get('networkRefreshHold')),
        'currentlyPermittedRequests': 0 if policy.get('networkRefreshHold') else len(proposed),
        'reviewCandidateInitialRequests': len(proposed),
        'requestPlanIsAuthorization': False,
        'redirectPolicy': 'Reject all redirects before requesting or resolving their target, including same-origin redirects.',
        'proxyTlsPolicy': 'Preserve existing platform proxy, TLS verification, DNS and access reviewer; no alternative route.',
        'metadataRequests': {'source': 'iThome', 'host': 'www.ithome.com.tw', 'path': '/news/[0-9]+',
            'query': 'forbidden', 'maxRequests': 30, 'condition': 'Only dates for article URLs from the registered iThome feed, with its source hold respected.'},
        'cveHeld': bool(policy.get('cve')), 'cveRequestsPlanned': 0,
        'requestUpperBoundAfterReview': len(proposed) + 30,
        'limits': 'No feed-content model calls. 5 MB RSS response limit, 25-second request timeout, 12 RSS workers; existing Steam and edition thresholds unchanged.',
        'scopeEvidence': {'registrySha256': hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            'steamConfigSha256': hashlib.sha256(watch_path.read_bytes()).hexdigest(),
            'holdsSha256': hashlib.sha256((ROOT / 'config/source-holds.json').read_bytes()).hexdigest()},
        'requests': rows,
        'unresolved': 'The original opaque denied URL is unknown. This inventory does not identify it or establish permission. Per-source and any global holds remain enforced.'}
    runtime = ROOT / '.sites-runtime'
    runtime.mkdir(exist_ok=True)
    output = runtime / 'source-request-plan.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ['mode', 'networkRequestsMade', 'globalHoldActive', 'currentlyPermittedRequests', 'reviewCandidateInitialRequests', 'requestUpperBoundAfterReview', 'cveHeld']}, ensure_ascii=False))
    return result


if __name__ == '__main__':
    main()
