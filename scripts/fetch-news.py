#!/usr/bin/env python3
"""Cloud-local public RSS intake. Writes candidates only; never publishes."""
import concurrent.futures, datetime, email.utils, hashlib, html, json, pathlib, re, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from source_control import controls, held_rss
from source_gateway import open_source
from source_health import source_failure, source_counts
ROOT=pathlib.Path(__file__).resolve().parents[1]
UTC=datetime.timezone.utc
NOW=datetime.datetime.now(UTC)
SOURCES=[s for s in json.loads((ROOT/'lib/source-registry.json').read_text()) if s.get('enabled',True)]
def text(node): return ''.join(node.itertext()) if node is not None else ''
def clean(v):
 for _ in range(2): v=html.unescape(v)
 return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',v)).strip()
def local(tag): return tag.split('}')[-1]
def child(node,names): return next((x for x in node if local(x.tag) in names),None)
def iso(v):
 try:d=email.utils.parsedate_to_datetime(v)
 except Exception:
  try:d=datetime.datetime.fromisoformat(v.replace('Z','+00:00'))
  except Exception:return None
 if d.tzinfo is None:return None
 return d.astimezone(UTC)
def ithome_page_date(markup):
 # iThome exposes a calendar publication date, not a reliable clock time.
 match=re.search(r'<span\b[^>]*class=["\']created["\'][^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</span>',markup,re.I)
 if not match:return None
 try:return datetime.datetime.strptime(match.group(1),'%Y-%m-%d').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).astimezone(UTC)
 except ValueError:return None

def recover_ithome_date(url):
 u=urllib.parse.urlsplit(url)
 if u.scheme!='https' or u.hostname!='www.ithome.com.tw' or not re.fullmatch(r'/news/\d+',u.path):return None
 try:
  with open_source(ROOT,'ithome_metadata','iThome',url,headers={'User-Agent':'NewsFeedRSS/1.0'},timeout=15) as r:
   if urllib.parse.urlsplit(r.url).hostname!='www.ithome.com.tw':return None
   raw=r.read(2000001)
  if len(raw)>2000000:return None
  return ithome_page_date(raw.decode('utf-8',errors='replace'))
 except Exception:return None

def fetch(s):
 reason=held_rss(s,controls(ROOT))
 if reason:return {'source':{'name':s['name'],'status':'skipped','detail':reason},'candidates':[]}
 try:
  with open_source(ROOT,'rss',s['name'],s['url'],headers={'User-Agent':'NewsFeedRSS/1.0','Accept':'application/rss+xml, application/atom+xml, application/xml, text/xml, */*'},timeout=25) as r: raw=r.read(5000001)
  if len(raw)>5000000:raise ValueError('Source exceeds safe size')
  if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('DTD/entity declarations not accepted')
  root=ET.fromstring(raw.lstrip())
  if local(root.tag).lower() not in ['rss','feed','rdf']:raise ValueError('Not an RSS/Atom feed')
  entries=[n for n in root.iter() if local(n.tag) in ['item','entry']];out=[];invalid_dates=0;recovered_dates={}
  if s['name']=='iThome':
   recovery_urls=[]
   for n in entries[:30]:
    rawdate=text(child(n,['pubDate','published','date']))
    match=re.match(r'(\d{4}-\d{2}-\d{2})',rawdate)
    # Skip clearly old calendar dates before bounded metadata retrieval.
    if match and match.group(1)>=(NOW-datetime.timedelta(hours=96)).date().isoformat():
     recovery_urls.append(text(child(n,['link'])))
   with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    recovered_dates=dict(zip(recovery_urls,pool.map(recover_ithome_date,recovery_urls)))
  for n in entries[:200]:
   title=clean(text(child(n,['title'])));date_raw=text(child(n,['pubDate','published','date']));d=iso(date_raw)
   if not d:invalid_dates+=1
   links=[x for x in n if local(x.tag)=='link'];url=next((x.attrib.get('href') for x in links if x.attrib.get('href') and x.attrib.get('rel','alternate')=='alternate'),None) or (text(links[0]) if links else '') or n.attrib.get('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about','')
   recovered=False
   if not d and s['name']=='iThome':d=recovered_dates.get(url);recovered=bool(d)
   if not title or not d or d>NOW or d<NOW-datetime.timedelta(hours=72):continue
   if not url.startswith('https://'):continue
   description=text(child(n,['description','summary','encoded','content']));
   if not description:description=text(next((x for x in n.iter() if local(x.tag)=='description'),None))
   summary=clean(description) if s.get('preservePublisherText') else clean(description)[:1800]
   media=next((x.attrib.get('url') for x in n.iter() if local(x.tag) in ['thumbnail','content'] and x.attrib.get('url','').startswith('https://')),None)
   if not media:
    match=re.search(r'<img[^>]+src=["\'](https:[^"\']+)',html.unescape(description),re.I);media=match.group(1) if match else None
   publisher_rights=clean(text(child(n,['rights','copyright']))) or clean(text(child(root,['rights','copyright'])))
   publisher_author=clean(text(child(n,['creator','author'])))
   publisher=child(n,['source']);publisher_name=clean(text(publisher)) if publisher is not None else '';publisher_url=publisher.attrib.get('url','') if publisher is not None else ''
   out.append({'publisherRights':publisher_rights,'publisherAuthor':publisher_author,'originalPublisher':publisher_name,'originalPublisherUrl':publisher_url,'id':hashlib.sha256(url.encode()).hexdigest()[:24],'category':s['category'],'title':title,'source':s['name'],'sourceLanguage':s.get('language'),'preferredByUser':s.get('preferredByUser',False),'url':url,'publishedAt':d.isoformat().replace('+00:00','Z'),'datePrecision':'day' if recovered else 'time' if re.search(r'T\d|\d{1,2}:\d{2}',date_raw) else 'day','feedExcerpt':summary,'imageUrl':media,'discussionSource':s.get('discussionSource',False),'needsReview':True})
  return {'source':{'name':s['name'],'status':'ok','invalidPublicationDates':invalid_dates,'recoveredPublicationDates':sum(bool(d) for d in recovered_dates.values()),'candidateCount':len(out)},'candidates':out}
 except Exception as e:return source_failure(s,e)
def main():
 if controls(ROOT).get('networkRefreshHold'):raise RuntimeError('Network intake held pending denied-source identification')
 with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:results=list(pool.map(fetch,SOURCES))
 unique={}
 for r in results:
  for a in r['candidates']:unique.setdefault(a['url'],a)
 payload={'fetchedAt':NOW.isoformat().replace('+00:00','Z'),'windowHours':72,'sourceStatus':[r['source'] for r in results],'candidates':list(unique.values()),'notice':'Untrusted RSS candidates, not publication-ready. Review using README.md. No website is changed by this script.'}
 out=ROOT/'.sites-runtime/candidates.json';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(payload,ensure_ascii=False,indent=2));print(json.dumps({'candidateFile':str(out),'candidates':len(unique),'sourceCount':len(SOURCES),'readable':sum(r['source']['status']=='ok' for r in results),'sourceHealth':source_counts(payload['sourceStatus'])}))
if __name__=='__main__':main()
