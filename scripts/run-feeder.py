#!/usr/bin/env python3
"""Deterministic RSS edition pipeline. No model calls. One JSON result on stdout."""
import datetime as dt, hashlib, html, json, pathlib, re, subprocess, sys, tempfile, urllib.parse
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from steam_lifecycle import select_visible_deals
from cve_json import refresh as refresh_cve_json, merge_cards, read_state
from source_control import controls, filter_intake, cve_selection, require_recent
from source_health import source_counts, require_rss_health
ROOT=pathlib.Path(__file__).resolve().parents[1]
UTC=dt.timezone.utc
POLICY=json.loads((ROOT/'config/editorial-policy.json').read_text())
REGISTRY=json.loads((ROOT/'lib/source-registry.json').read_text())
SOURCES={s['name']:s for s in REGISTRY}
NOW=dt.datetime.now(UTC)
BLOCK_GAMES=re.compile(r'\b(FPS|first.person shooter|Counter.Strike|Call of Duty|Battlefield|Valorant|Fortnite|PUBG|Apex Legends|Overwatch|Destiny 2|esports?|League of Legends|Dota|multiplayer.only|MMORPG|PS[3456]|PlayStation|Xbox|Nintendo|Switch 2|console)\b|第一人稱射擊|电竞|電競|主機獨佔',re.I)
GAME_NAMES=re.compile(r'\b(Hades|Slay the Spire|Silksong|Stardew Valley|Baldur.?s Gate|Factorio|RimWorld|Terraria|Noita|Balatro|Dead Cells|Celeste|Hollow Knight|Civilization|Crusader Kings|Dwarf Fortress|Satisfactory|Against the Storm|Manor Lords|Persona|Obra Dinn|CloverPit)\b',re.I)
CVE_RE=re.compile(r'\bCVE-\d{4}-\d{4,}\b',re.I)
ELIXIR_DIRECT=re.compile(r'\b(?:elixir|erlang)\b',re.I)
ELIXIR_AMBIGUOUS=re.compile(r'\b(?:OTP|Phoenix|Hex|BEAM)\b',re.I)
ELIXIR_CONTEXT=re.compile(r'\b(?:functional programming|actor model|supervis(?:or|ion)|mix\s+(?:deps|compile|test)|LiveView|GenServer|Erlang VM|Elixir|Erlang)\b',re.I)
def elixir_topic(text,source):
 return bool(ELIXIR_DIRECT.search(text) or (ELIXIR_AMBIGUOUS.search(text) and (source.get('category')=='Elixir / Erlang' or ELIXIR_CONTEXT.search(text))))
def ifanr_exception(source,url):
 return source.get('id')=='ifanr' and source.get('url')=='https://www.ifanr.com/feed' and urllib.parse.urlsplit(url).hostname in ['ifanr.com','www.ifanr.com']
TOPICS=[('Java / Kotlin',r'\b(java|kotlin|jvm|spring.?boot|spring framework|quarkus|gradle)\b'),('Rust / Node.js',r'\b(rust|node\.?js|nodejs|deno|bun|javascript|typescript)\b'),('Linux',r'\b(linux|ubuntu|debian|fedora|kernel|kde|gnome|wayland|open.?source)\b|開源|开源'),('Android / 嵌入式',r'\b(android|arduino|embedded|raspberry|microcontroller|esp32|stm32)\b|嵌入式'),('AI',r'\b(AI|LLM|machine learning|neural|chatgpt|claude|gemini|hugging.?face|ollama|agentic|transformer)\b|人工智能|人工智慧|大模型')]
def plain(value,limit=350):
 value=re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>',' ',str(value or ''),flags=re.I|re.S)
 for _ in range(2):value=html.unescape(value)
 value=re.sub(r'<[^>]*>',' ',value)
 value=re.sub(r'[\x00-\x1f\x7f]',' ',value)
 return re.sub(r'\s+',' ',value).strip()[:limit]
def safeurl(value):
 try:
  u=urllib.parse.urlsplit(value or '');h=(u.hostname or '').lower()
  if u.scheme!='https' or not h or u.username or u.password or re.search(r'[\s<>"\x00-\x1f]',value):return None
  if h in ['localhost','127.0.0.1','::1'] or h.endswith('.local'):return None
  q=urllib.parse.urlencode([(k,v) for k,v in urllib.parse.parse_qsl(u.query) if not k.lower().startswith('utm_') and k.lower() not in ['fbclid','gclid']])
  return urllib.parse.urlunsplit(('https',u.netloc,u.path or '/',q,''))
 except Exception:return None
def medium(a):
 h=urllib.parse.urlsplit(a.get('url','')).hostname or ''
 return a.get('source') in POLICY['excludedMediumSourceNames'] or any(h==d or h.endswith('.'+d) for d in POLICY['excludedMediumDomains'])
def normalize(a):
 s=SOURCES.get(a.get('source'),{});url=safeurl(a.get('url'))
 if s.get('enabled') is False or not url or medium(a):return None
 language=s.get('language',a.get('sourceLanguage','en'))
 if language not in ['en','zh-Hant','zh-TW','zh-HK'] and not (language=='zh-Hans' and ifanr_exception(s,url)):return None
 try:date=dt.datetime.fromisoformat(a['publishedAt'].replace('Z','+00:00'))
 except Exception:return None
 if date.tzinfo is None or not NOW-dt.timedelta(hours=72)<=date<=NOW:return None
 preserve=s.get('preservePublisherText',False)
 full_cve_text=plain(a.get('title'),None)+' '+plain(a.get('feedExcerpt'),None)
 title=plain(a.get('title'),None if preserve else 180);excerpt=plain(a.get('feedExcerpt'),None if preserve else 350);text=title+' '+excerpt
 if not title or re.search(r'[\u3040-\u30ff\u0400-\u04ff\u0600-\u06ff\uac00-\ud7af]',title+' '+excerpt):return None
 category=s.get('category',a.get('category','科技'));discussion=a.get('discussionSource',False)
 general_news=category in ['World / 國際','香港']
 if not general_news and re.search(r'\b(game|gaming|Steam|videogame|PlayStation|Xbox|Nintendo|FPS|esports)\b|遊戲|游戏|電競|电竞',title,re.I):
  if BLOCK_GAMES.search(text) or not GAME_NAMES.search(title):return None
  category='單機遊戲'
 if discussion and not re.search(r'\b(show hn|ask hn)\b',title,re.I):return None
 if category in ['單機遊戲','Steam 單機'] or s.get('filter') in ['pc_singleplayer_nonfps','steam_app_allowlist']:
  if BLOCK_GAMES.search(text):return None
  if s.get('filter')!='steam_app_allowlist' and not GAME_NAMES.search(title):return None
  category='單機遊戲'
 if not general_news and re.search(r'CVE-\d{4}-\d+|vulnerabilit|zero.day|漏洞',title,re.I):category='資安'
 if category=='資安':
  if re.search(r'\b(windows|microsoft|exchange|sharepoint|azure|office)\b',text,re.I):return None
  if not CVE_RE.search(full_cve_text) or not re.search(r'critical|actively exploit|exploited in the wild|remote code execution|重大|嚴重|積極利用|已遭利用',text,re.I) or not re.search(r'open.?source|linux|java|mysql|apache|openssl|php|python|npm|redis|postgres|開源',text,re.I):return None
 if category not in ['單機遊戲','資安','World / 國際','香港']:
  inferred='Elixir / Erlang' if elixir_topic(text,s) else next((cat for cat,pat in TOPICS if re.search(pat,text,re.I)),None)
  if inferred:category=inferred
  elif category not in POLICY['categories']:return None
  elif s.get('discussionSource') and not s.get('preferredByUser'):return None
 if category=='AI' and re.search(r'for beginners|complete guide|what is an? (?:ai|llm)|most beginners',title,re.I):return None
 card={'id':hashlib.sha256(url.encode()).hexdigest()[:24],'category':category,'title':title,'summary':(' '.join(excerpt.split()[:25]) if re.search(r'[A-Za-z]',excerpt) and not re.search(r'[\u3400-\u9fff]',excerpt) else excerpt[:100]) if excerpt else title,'summaryType':'publisher_feed_excerpt' if excerpt else 'title_only','source':a['source'],'url':url,'publishedAt':date.isoformat().replace('+00:00','Z'),'datePrecision':a.get('datePrecision','time'),'sourceLanguage':language,'preferredByUser':s.get('preferredByUser',False)}
 if preserve:card.update(summary=excerpt or title,preservePublisherText=True)
 if s.get('attributionUrl'):card['attributionUrl']=s['attributionUrl']
 if s.get('sourceBrandImage'):card['sourceBrandImage']=s['sourceBrandImage']
 if s.get('contentLicense'):card['contentLicense']=s['contentLicense']
 if category=='資安':card['cveIds']=sorted(set(x.upper() for x in CVE_RE.findall(full_cve_text)))
 if s.get('contentLicense'):
  for field in ['publisherRights','publisherAuthor']:
   if a.get(field):card[field]=plain(a[field],None)
 if a.get('originalPublisher'):card['originalPublisher']=plain(a['originalPublisher'],120)
 if a.get('originalPublisherUrl') and safeurl(a['originalPublisherUrl']):card['originalPublisherUrl']=safeurl(a['originalPublisherUrl'])
 if discussion:card['summary']='[New discussion; timestamp is the discussion date] '+card['summary'][:290]
 image=safeurl(a.get('imageUrl'))
 if image:card.update(imageUrl=image,imageLabel='來源 RSS 配圖')
 if 'youtube.com/' in url:card['kind']='video'
 return card

def main():
 global NOW
 runtime=ROOT/'.sites-runtime';runtime.mkdir(exist_ok=True)
 stage='preflight';rss_counts={};log=[];target=ROOT/'data/feed.json';old=target.read_bytes();old_dist=None
 try:
  control=controls(ROOT);cached='--from-cache' in sys.argv
  if not cached and control.get('networkRefreshHold'):raise RuntimeError(control['networkRefreshHold'])
  stage='intake'
  for script in ([] if '--from-cache' in sys.argv else ['fetch-news.py','fetch-steam-deals.py']):
   r=subprocess.run([sys.executable,str(ROOT/'scripts'/script)],cwd=ROOT,capture_output=True,text=True,timeout=480)
   log.append(r.stdout+r.stderr)
   if r.returncode:raise RuntimeError(script+' failed')
  NOW=dt.datetime.now(UTC)
  raw=json.loads((runtime/'candidates.json').read_text());candidates,statuses=filter_intake(raw,REGISTRY,control,NOW,cached);good=sum(s['status']=='ok' for s in statuses)
  stage='rss_health';rss_counts=source_counts(statuses);require_rss_health(statuses)
  for source in statuses:
   if source['name']=='iThome' and source['status']!='skipped' and not any(a.get('source')=='iThome' for a in raw['candidates']):source.update(status='limited',detail='未能從官方文章頁核實 RSS 日期；只保留仍在 72 小時內的已核實日期項目。')
  stage='normalize';cards=[];seen=set();titles=set()
  for candidate in candidates:
   a=normalize(candidate)
   if not a:continue
   key=re.sub(r'\W+','',a['title'].lower())
   if (a['url'] in seen or key in titles) and not a.get('cveIds'):continue
   seen.add(a['url']);titles.add(key);cards.append(a)
  # Date-only iThome dates are malformed in RSS: retain only previously verified dates inside window.
  for a in json.loads(old)['stories']:
   if a.get('source')=='iThome' and a.get('datePrecision')=='day' and not any(x['name']=='iThome' and x['status']=='skipped' for x in statuses):
    copy=dict(a,feedExcerpt=a.get('summary',''));n=normalize(copy)
    if n and n['url'] not in seen:n['summaryType']='previously_verified_summary';cards.append(n);seen.add(n['url'])
  stage='cve';cards,cve_counts,cve_status=cve_selection(ROOT,NOW,cards,control,cached,refresh_cve_json,read_state,merge_cards)
  statuses.append(cve_status)
  if cve_status['status']=='ok':good+=1
  stage='steam';deals=json.loads((runtime/'steam-deal-candidates.json').read_text())
  if not deals:raise RuntimeError('Missing Steam intake observations')
  for row in deals:require_recent(row['lastAttemptAt'],NOW)
  steam_failed=sum(row['status']=='failed' for row in deals)
  statuses.append({'name':'Steam 香港商店','status':'failed' if steam_failed else 'ok','cached':cached,'fetchedAt':min(row['lastAttemptAt'] for row in deals),'detail':'部分價格核對失敗；保留具時效且明確標示的上次核對資料。' if steam_failed else '官方香港商店價格觀察。'})
  if not steam_failed:good+=1
  cards.extend(select_visible_deals(deals,NOW,window_hours=POLICY.get('windowHours',72)))
  stage='card_health'
  if len(cards)<30:raise RuntimeError('Fewer than 30 eligible stories; previous edition preserved')
  cards=sorted(cards,key=lambda a:a.get('freshnessAt') or a['publishedAt'],reverse=True)[:1200]
  feed={'updatedAt':NOW.isoformat().replace('+00:00','Z'),'editionDate':NOW.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat(),'stories':cards,'sourceStatus':statuses,'intake':{'cached':cached,'rssFetchedAt':raw['fetchedAt'],'steamFetchedAt':min(row['lastAttemptAt'] for row in deals),'partial':any(x['status']!='ok' for x in statuses)},'note':('本期沿用已完成的來源讀取（RSS '+raw['fetchedAt']+'）；並未重新讀取全部來源。 ' if cached else '')+'自動 RSS 整理：標題與短摘錄取自來源，未經 AI 改寫。規則篩選可能有遺漏；討論時間不代表原文日期。','editorialStats':{'medium':0,'total':len(cards),'modelCalls':0}}
  stage='build';target.write_text(json.dumps(feed,ensure_ascii=False))
  r=subprocess.run(['node','scripts/build-static.mjs'],cwd=ROOT,capture_output=True,text=True,timeout=90);log.append(r.stdout+r.stderr)
  if r.returncode:raise RuntimeError('Static build failed')
  r=subprocess.run(['node','--check','dist/reader.js'],cwd=ROOT,capture_output=True,text=True,timeout=20)
  if r.returncode:raise RuntimeError('Reader syntax check failed')
  stage='validation';r=subprocess.run([sys.executable,'scripts/verify-feeder.py'],cwd=ROOT,capture_output=True,text=True,timeout=30);log.append(r.stdout+r.stderr)
  if r.returncode:raise RuntimeError('Final publication validation failed')
  result={'status':'success','rssHealth':rss_counts,'partial':any(x['status']!='ok' for x in statuses),'cached':cached,'rssFetchedAt':raw['fetchedAt'],'itemCount':len(cards),'sourceCount':len(statuses),'failureCount':len(statuses)-good,'failedSources':[s['name'] for s in statuses if s['status'] in ['failed','skipped']][:8],'limitedSourceCount':sum(s['status']=='limited' for s in statuses),'dealCount':sum(a.get('kind')=='deal' for a in cards),'cveCount':sum(bool(a.get('cveIds')) for a in cards),'cveJSON':cve_counts,'modelCalls':0,'artifact':'dist/index.html','sha256':hashlib.sha256((ROOT/'dist/feed.json').read_bytes()).hexdigest()}
 except Exception as e:
  target.write_bytes(old)
  subprocess.run(['node','scripts/build-static.mjs'],cwd=ROOT,capture_output=True,timeout=90)
  result={'status':'failure','stage':stage,'errorType':type(e).__name__,'rssHealth':rss_counts,'error':str(e)[:180],'preservedPreviousEdition':True,'modelCalls':0,'cveJSON':locals().get('cve_counts',{'status':'failure'})}
 (runtime/'feeder.log').write_text('\n'.join(log));(runtime/'feeder-result.json').write_text(json.dumps(result));print(json.dumps(result));return 0 if result['status']=='success' else 1
if __name__=='__main__':sys.exit(main())
