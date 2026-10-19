#!/usr/bin/env python3
import hashlib,importlib.util,json,pathlib,sys,urllib.parse
root=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('engine',root/'scripts/run-feeder.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
a=json.loads((root/'dist/feed.json').read_text());s=a['stories'];assert 30<=len(s)<=1200;assert len({x['id'] for x in s})==len(s);assert len({x['url'] for x in s})==len(s)
assert all(not e.medium(x) and e.safeurl(x['url']) for x in s)
assert all('<script' not in x['title'].lower()+x['summary'].lower() for x in s)
assert all(e.NOW-e.dt.timedelta(hours=72)<=e.dt.datetime.fromisoformat((x.get('freshnessAt') if x.get('kind')=='deal' else x['publishedAt']).replace('Z','+00:00'))<=e.NOW for x in s)
assert all(x.get('publishedAt') is None and x.get('firstSeen') and x.get('checkedAt') for x in s if x.get('kind')=='deal')
assert all(all(e.CVE_RE.fullmatch(c) for c in x.get('cveIds',[])) for x in s)
cve_ids=[c.upper() for x in s for c in x.get('cveIds',[])]
assert len(cve_ids)==len(set(cve_ids)), 'Duplicate CVE across cards'
assert all(all(e.safeurl(article.get('url')) for article in x.get('relatedArticles',[])) for x in s)
assert not any(p.name=='cve-json-state.json' for p in (root/'dist').rglob('*')), 'Private CVE intake state leaked into publication'
assert not (root/'dist/_worker.js').exists()
print(json.dumps({'status':'verified','itemCount':len(s),'mediumCount':0,'modelCalls':0,'sha256':hashlib.sha256((root/'dist/feed.json').read_bytes()).hexdigest()}))
