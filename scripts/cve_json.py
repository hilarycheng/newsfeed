"""Bounded official CVE v5 intake. Programmatic only; no model services.
State is private source data, never a public asset. All URLs are constructed, not
followed from untrusted records. A checkpoint represents fully processed events.
"""
import concurrent.futures as futures
import datetime as dt
import hashlib
import html
import json
import pathlib
import re
import time
import subprocess
import urllib.parse
import urllib.request
from source_gateway import authorize_request, audit

UTC = dt.timezone.utc
REVISION_URL = 'https://api.github.com/repos/CVEProject/cvelistV5/git/ref/heads/main'
BASE = 'https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/'
SOURCE = 'CVE List V5'
VERSIONS = {'5.0', '5.1', '5.1.0', '5.1.1', '5.2', '5.2.0'}
ID = re.compile(r'CVE-\d{4}-\d{4,19}\Z')
MAX_RECORDS = 2000
MAX_SECONDS = 240
# Deliberately explicit; a hosting platform or language is NOT an OSS assertion.
PRODUCTS = {
 'apache': {'http server','httpd','tomcat','struts','solr','kafka','activemq','camel','cassandra','spark','airflow','commons text','log4j','httpcomponents'},
 'linux': {'linux','linux kernel','kernel'},
 'openssl': {'openssl'}, 'openbsd': {'openssh'}, 'openssh': {'openssh'},
 'python': {'python','cpython'}, 'python software foundation': {'python','cpython'},
 'php': {'php'}, 'php group': {'php'}, 'postgresql': {'postgresql'},
 'redis': {'redis'}, 'valkey': {'valkey'}, 'nginx': {'nginx'},
 'nodejs': {'node.js','nodejs'}, 'node.js': {'node.js','nodejs'},
 'django': {'django'}, 'django software foundation': {'django'},
 'spring': {'spring framework','spring security','spring boot'},
 'vmware': {'spring framework','spring security','spring boot'},
 'broadcom': {'spring framework','spring security','spring boot'},
 'rust': {'rust'}, 'rust project': {'rust'}, 'curl': {'curl','libcurl'},
 'git': {'git'}, 'gitlab': {'gitlab community edition','gitlab ce'},
 'elixir': {'elixir'}, 'erlang': {'erlang/otp','erlang otp','erlang'},
}
REPOS = {'apache/httpd','apache/tomcat','apache/struts','apache/solr','apache/kafka','apache/activemq','apache/airflow','apache/logging-log4j2','openssl/openssl','openssh/openssh-portable','python/cpython','php/php-src','postgres/postgres','redis/redis','valkey-io/valkey','nginx/nginx','nodejs/node','django/django','spring-projects/spring-framework','spring-projects/spring-security','spring-projects/spring-boot','rust-lang/rust','curl/curl','git/git','elixir-lang/elixir','erlang/otp'}
PACKAGES = {'pkg:pypi/django','pkg:pypi/apache-airflow','pkg:npm/express','pkg:npm/next','pkg:maven/org.apache.logging.log4j/log4j-core','pkg:maven/org.springframework/spring-web','pkg:maven/org.springframework/spring-webmvc','pkg:golang/golang.org/x/net'}
MAJOR = re.compile(r'\bcritical\b|actively exploit|exploited in the wild|remote code execution|重大|嚴重|積極利用|已遭利用', re.I)
EXCLUDED = re.compile(r'\b(?:microsoft|windows|exchange|sharepoint|azure|office)\b', re.I)

def date(value):
 if not isinstance(value,str): raise ValueError('missing_date')
 d=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
 # Official v5 historical records may use timezone-less UTC per the schema.
 return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)

def stamp(value):return value.astimezone(UTC).isoformat().replace('+00:00','Z')
def plain(value,limit=350):
 value=re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>',' ',str(value or ''),flags=re.I|re.S)
 for _ in range(2):value=html.unescape(value)
 return re.sub(r'\s+',' ',re.sub(r'<[^>]*>|[\x00-\x1f\x7f]',' ',value)).strip()[:limit]
def safeurl(value):
 try:
  u=urllib.parse.urlsplit(value)
  if u.scheme!='https' or not u.hostname or u.username or u.password or re.search(r'[\s<>"\x00-\x1f]',value):return None
  if u.hostname in {'localhost','127.0.0.1','::1'} or u.hostname.endswith('.local'):return None
  return urllib.parse.urlunsplit((u.scheme,u.netloc,u.path,u.query,''))
 except (ValueError,TypeError):return None

def oss(affected):
 for a in affected:
  vendor=str(a.get('vendor','')).lower().strip(); product=str(a.get('product','')).lower().strip()
  if product in PRODUCTS.get(vendor,set()):return True
  # Apache's official organization often supplies full product names.
  if vendor in {'apache software foundation','apache'} and product.removeprefix('apache ') in PRODUCTS['apache']:return True
  p=a.get('packageURL')
  if isinstance(p,str) and p.split('@')[0].split('?')[0].split('#')[0].lower() in PACKAGES:return True
  repo=a.get('repo','')
  try:
   u=urllib.parse.urlsplit(repo)
   if u.scheme=='https' and u.hostname=='github.com' and u.path.strip('/').removesuffix('.git').lower() in REPOS:return True
  except ValueError:pass
 return False

def record_url(cve):
 if not ID.fullmatch(cve):raise ValueError('invalid_cve_id')
 _,year,number=cve.split('-')
 return f'{BASE}{year}/{number[:-3]}xxx/{cve}.json'

def projection(record,expected):
 if not isinstance(record,dict) or record.get('dataType')!='CVE_RECORD':raise ValueError('unknown_record_type')
 if record.get('dataVersion') not in VERSIONS:raise ValueError('unsupported_schema')
 meta=record.get('cveMetadata',{})
 if meta.get('cveId')!=expected or not ID.fullmatch(expected):raise ValueError('record_id_mismatch')
 state=meta.get('state')
 if state=='REJECTED':return {'state':'REJECTED','card':None}
 if state!='PUBLISHED':raise ValueError('unknown_record_state')
 published=stamp(date(meta.get('datePublished')))
 containers=record.get('containers',{});cna=containers.get('cna')
 if not isinstance(cna,dict) or not isinstance(cna.get('affected'),list):raise ValueError('invalid_cna')
 allcontainers=[cna]+[a for a in containers.get('adp',[]) if isinstance(a,dict)]
 descriptions=cna.get('descriptions',[])
 description=next((plain(a.get('value')) for a in descriptions if isinstance(a,dict) and str(a.get('lang','')).lower().startswith('en')), '')
 title=plain(cna.get('title'),180) or expected
 text=title+' '+description
 affected=[a for a in cna['affected'] if isinstance(a,dict)]
 exclusion=text+' '+' '.join(str(a.get('vendor',''))+' '+str(a.get('product','')) for a in affected)
 ratings=[]
 for container in allcontainers:
  provider=plain(container.get('providerMetadata',{}).get('shortName') or container.get('providerMetadata',{}).get('orgId') or ('CNA' if container is cna else 'ADP'),120)
  for metric in container.get('metrics',[]):
   if not isinstance(metric,dict):continue
   for key,version in [('cvssV4_0','4.0'),('cvssV3_1','3.1'),('cvssV3_0','3.0'),('cvssV2_0','2.0')]:
    val=metric.get(key)
    if not isinstance(val,dict):continue
    score=val.get('baseScore')
    if isinstance(score,(int,float)) and not isinstance(score,bool) and 0<=score<=10:
     ratings.append({'version':version,'score':score,'source':provider,'vector':plain(val.get('vectorString'),200)})
 result={'state':'PUBLISHED','publishedAt':published,'card':None,'dataVersion':record['dataVersion']}
 # Preserve prior major-vulnerability policy; ordinary HIGH / CVSS 7.x is insufficient.
 if re.search(r'[\u3040-\u30ff\u0400-\u04ff\u0600-\u06ff\uac00-\ud7af]',text):return result
 if EXCLUDED.search(exclusion) or not oss(affected) or not (MAJOR.search(text) or any(r['score']>=9 for r in ratings)):return result
 refs=[]
 for container in allcontainers:
  for ref in container.get('references',[]):
   if isinstance(ref,dict) and safeurl(ref.get('url')) and ref['url'] not in refs:refs.append(ref['url'])
 rating='未評分' if not ratings else ' · '.join(f"CVSS {r['version']} {r['score']:g} ({r['source']})" for r in ratings)
 result['card']={'id':expected,'cveIds':[expected],'category':'資安','title':title if expected in title else expected+' · '+title,'summary':description or title,'summaryType':'official_cve_description' if description else 'title_only','source':SOURCE,'url':'https://www.cve.org/CVERecord?id='+expected,'publishedAt':published,'datePrecision':'time','sourceLanguage':'en','cveRating':rating,'cvss':ratings,'cveReferences':refs,'relatedArticles':[]}
 return result

def fetch_json(url,limit=2_000_000):
 root=pathlib.Path(__file__).resolve().parents[1]
 authorize_request(root,'cve',SOURCE,url)
 # curl enforces a total transfer timeout (urllib timeout is only per socket
 # operation). Every worker terminates within 22s even on a trickling response.
 r=subprocess.run(['curl','--fail','--silent','--show-error','--max-time','20',
                   '--proto','=https','--proto-redir','=https','--no-location','--max-redirs','0',
                   '--max-filesize',str(limit),'--user-agent','NewsFeed-CVE-JSON/1.0',
                   '--header','Accept: application/json','--write-out','\n%{http_code}',url],capture_output=True,timeout=22)
 # No --location flag: redirects are never followed, including registered ones.
 body,separator,status=r.stdout.rpartition(b'\n')
 http_status=int(status) if separator and re.fullmatch(rb'[0-9]{3}',status) else None
 audit(root,'cve',SOURCE,url,'curl_result',returnCode=r.returncode,httpStatus=http_status)
 if r.returncode:raise OSError('official_source_fetch_failed')
 if http_status != 200:raise OSError('official_source_http_status_rejected')
 if len(body)>limit:raise ValueError('response_size_limit')
 return json.loads(body)

def empty_state():return {'version':1,'checkpoint':None,'records':{},'versions':{},'tombstones':{},'quarantine':{},'failures':{},'pending':{}}
def read_state(root):
 path=pathlib.Path(root)/'data/cve-json-state.json'
 if not path.exists():return empty_state()
 state=json.loads(path.read_text())
 if state.get('version')!=1:raise ValueError('unknown_state_version')
 return state

def save_state(root,state):
 path=pathlib.Path(root)/'data/cve-json-state.json';path.parent.mkdir(exist_ok=True)
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,separators=(',',':')));tmp.replace(path)

def discover(log,state,now):
 if not isinstance(log,list) or not log:raise ValueError('invalid_delta_log')
 events=[]
 for item in log:
  if not isinstance(item,dict):raise ValueError('invalid_delta_event')
  when=date(item.get('fetchTime'))
  if when>now+dt.timedelta(minutes=5):raise ValueError('future_delta_event')
  events.append((when,item))
 checkpoint=date(state['checkpoint']) if state.get('checkpoint') else now-dt.timedelta(hours=72)
 if min(t for t,_ in events)>checkpoint:raise ValueError('delta_history_gap')
 if max(t for t,_ in events)<checkpoint:raise ValueError('stale_delta_log')
 pending=dict(state.get('pending',{}))
 for when,event in sorted(events,key=lambda p:p[0]):
  if when<=checkpoint:continue
  if event.get('error'):raise ValueError('upstream_delta_error')
  for group in ['new','updated']:
   if not isinstance(event.get(group),list):raise ValueError('invalid_delta_group')
   for item in event[group]:
    cve=item.get('cveId');updated=item.get('dateUpdated')
    if not isinstance(cve,str) or not ID.fullmatch(cve):raise ValueError('invalid_delta_id')
    version=stamp(date(updated))
    if cve not in pending or date(pending[cve])<date(version):pending[cve]=version
 # Collapse cumulative/repeated changes to their latest receipt before comparing
 # persisted versions, otherwise old events would requeue a completed ID on retry.
 pending={k:v for k,v in pending.items() if state['versions'].get(k)!=v}
 return pending,stamp(max(t for t,_ in events))

def refresh(root,now,fetch=fetch_json,max_records=MAX_RECORDS,max_seconds=MAX_SECONDS,extra_ids=()):
 state=read_state(root);start=time.monotonic();counts={'status':'failure','success':0,'skips':0,'errors':0,'pending':0,'quarantined':0}
 try:
  revision=fetch(REVISION_URL,100_000).get('object',{}).get('sha')
  if not isinstance(revision,str) or not re.fullmatch(r'[0-9a-f]{40}',revision):raise ValueError('invalid_repository_revision')
  snapshot=BASE.replace('/main/','/'+revision+'/')
  log=fetch(snapshot+'deltaLog.json',40_000_000)
  state['repositoryRevision']=revision
  pending,target=discover(log,state,now)
  for cve in extra_ids:
   if not isinstance(cve,str) or not ID.fullmatch(cve):raise ValueError('invalid_extra_id')
   if cve not in state['versions'] and cve not in pending:pending[cve]='1970-01-01T00:00:00Z'
  state['pending']=pending;state['targetCheckpoint']=target
  counts['discovered']=len(pending)
  jobs=sorted(pending.items(),key=lambda x:(x[1],x[0]))[:max_records]
  def download(job):
   cve,version=job
   if time.monotonic()-start>=max_seconds:return cve,version,None,'run_time_limit'
   try:
    raw=fetch(record_url(cve).replace(BASE,snapshot),2_000_000)
    if time.monotonic()-start>=max_seconds:return cve,version,None,'run_time_limit'
    updated=raw.get('cveMetadata',{}).get('dateUpdated')
    if updated is not None and date(updated)<date(version):raise ValueError('stale_record_version')
    # Optional dateUpdated is not required: the delta and records are pinned
    # to the same immutable official repository revision.
    return cve,version,projection(raw,cve),None
   except Exception as exc:return cve,version,None,type(exc).__name__+':'+(str(exc)[:80] if isinstance(exc,ValueError) else 'record_fetch_failed')
  with futures.ThreadPoolExecutor(max_workers=12) as pool:
   for cve,version,record,error in pool.map(download,jobs):
    if error:
     counts['errors']+=1;state['failures'][cve]={'at':stamp(now),'error':error}
     if error.startswith('ValueError:'):state['quarantine'][cve]={'at':stamp(now),'reason':error,'version':version};counts['quarantined']+=1
    else:
     counts['success']+=1
     if record['state']=='REJECTED':
      state['tombstones'][cve]={'at':stamp(now),'version':version};state['records'].pop(cve,None)
     elif cve not in state['tombstones']:
      state['records'][cve]=record
      if not record['card']:counts['skips']+=1
     state['versions'][cve]=version;state['pending'].pop(cve,None);state['failures'].pop(cve,None);state['quarantine'].pop(cve,None)
  counts['pending']=len(state['pending'])
  if not state['pending']:
   state['checkpoint']=target;counts['status']='success';state.pop('lastError',None)
  else:state['lastError']='incomplete_delta_batch'
  # Keep only the rolling publication cache; version receipts and tombstones
  # remain durable so retries and stale RSS cannot resurrect a rejected CVE.
  state['records']={k:v for k,v in state['records'].items() if date(v['publishedAt'])>=now-dt.timedelta(hours=72)}
 except Exception as exc:
  counts['errors']+=1;counts['pending']=len(state.get('pending',{}));state['lastError']=str(exc)[:120] if isinstance(exc,ValueError) else type(exc).__name__
 state['lastAttemptAt']=stamp(now);state['lastResult']=counts;save_state(root,state)
 return counts

def merge_cards(rss_cards,state,now):
 ordinary=[];canonical={}
 for cve,record in state.get('records',{}).items():
  card=record.get('card')
  if card and re.search(r'[\u3040-\u30ff\u0400-\u04ff\u0600-\u06ff\uac00-\ud7af]',card['title']+' '+card['summary']):continue
  if card and cve not in state.get('tombstones',{}) and now-dt.timedelta(hours=72)<=date(card['publishedAt'])<=now:
   canonical[cve]=dict(card,relatedArticles=[])
 for original in rss_cards:
  ids=sorted({v for v in original.get('cveIds',[]) if isinstance(v,str) and ID.fullmatch(v)})
  if not ids:ordinary.append(original);continue
  for cve in ids:
   if cve in state.get('tombstones',{}):continue
   known=state.get('records',{}).get(cve)
   if known and not known.get('card'):continue
   # A processed ID whose publication cache expired must not regain freshness
   # from a newly dated RSS article about that old vulnerability.
   if cve in state.get('versions',{}) and not known:continue
   if cve not in canonical:continue  # Unverified/future/expired records cannot borrow RSS freshness.
   link={'url':safeurl(original.get('url')),'title':plain(original.get('title'),180),'source':plain(original.get('source'),120)}
   if link['url'] and all(l['url']!=link['url'] for l in canonical[cve]['relatedArticles']):canonical[cve]['relatedArticles'].append(link)
 return ordinary+list(canonical.values())
