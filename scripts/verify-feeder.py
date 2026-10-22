#!/usr/bin/env python3
import hashlib,importlib.util,json,pathlib,re,sys,urllib.parse
root=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('engine',root/'scripts/run-feeder.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
page=(root/'dist/index.html').read_text()
a=json.loads(re.search(r'<script type="application/json" id="feed-data">(.*?)</script>',page,re.S)[1]);s=a['stories'];assert 30<=len(s)<=1200;assert len({x['id'] for x in s})==len(s);assert len({x['url'] for x in s})==len(s)
assert all(not e.medium(x) and e.safeurl(x['url']) for x in s)
assert all('<script' not in x['title'].lower()+x['summary'].lower() for x in s)
edition=e.dt.datetime.fromisoformat(a['updatedAt'].replace('Z','+00:00'))
for x in s:
 at=e.dt.datetime.fromisoformat((x.get('freshnessAt') if x.get('kind')=='deal' else x['publishedAt']).replace('Z','+00:00'))
 anchor=e.NOW if x.get('kind')=='deal' else edition
 assert anchor-e.dt.timedelta(hours=72)<=at<=anchor
assert all(x.get('publishedAt') is None and (x.get('deal',{}).get('firstSeen') or x.get('deal',{}).get('firstSeenAt')) and x.get('checkedAt') for x in s if x.get('kind')=='deal')
assert all(all(e.CVE_RE.fullmatch(c) for c in x.get('cveIds',[])) for x in s)
cve_ids=[c.upper() for x in s for c in x.get('cveIds',[])]
assert len(cve_ids)==len(set(cve_ids)), 'Duplicate CVE across cards'
assert all(all(e.safeurl(article.get('url')) for article in x.get('relatedArticles',[])) for x in s)
assert not any(p.name=='cve-json-state.json' for p in (root/'dist').rglob('*')), 'Private CVE intake state leaked into publication'
assert sorted(p.name for p in (root/'dist').iterdir())==['apple-touch-icon.png','icon-192.png','icon-512.png','index.html','manifest.webmanifest','sw.js']
assert '<script src=' not in page and 'rel="stylesheet"' not in page
print(json.dumps({'status':'verified','itemCount':len(s),'mediumCount':0,'modelCalls':0,'sha256':hashlib.sha256((root/'dist/index.html').read_bytes()).hexdigest()}))
