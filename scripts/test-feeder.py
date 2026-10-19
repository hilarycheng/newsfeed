import importlib.util,unittest,datetime
spec=importlib.util.spec_from_file_location('engine','scripts/run-feeder.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
class Tests(unittest.TestCase):
 def row(self,**kw):
  a={'source':'Inside Java','title':'Java release','url':'https://example.com/news','publishedAt':e.NOW.isoformat(),'feedExcerpt':'<script>alert(1)</script><p>Useful release</p>','datePrecision':'time'};a.update(kw);return a
 def test_sanitize(self):
  a=e.normalize(self.row());self.assertEqual(a['summary'],'Useful release');self.assertNotIn('<',a['summary'])
 def test_url(self):
  for url in ['javascript:alert(1)','http://example.com','https://user:pass@example.com','https://localhost/a'] :self.assertIsNone(e.normalize(self.row(url=url)))
 def test_medium(self):
  for u in ['https://medium.com/a','https://a.medium.com/b','https://blog.devops.dev/c']:self.assertIsNone(e.normalize(self.row(url=u)))
  self.assertIsNotNone(e.normalize(self.row(title='Java medium-sized buffers')))
 def test_dates(self):
  for d in [e.NOW+datetime.timedelta(hours=1),e.NOW-datetime.timedelta(hours=73)]:self.assertIsNone(e.normalize(self.row(publishedAt=d.isoformat())))
 def test_games(self):
  self.assertIsNone(e.normalize(self.row(source='PC Gamer',title='Call of Duty sale')))
  self.assertIsNotNone(e.normalize(self.row(source='PC Gamer',title='Hades update')))
 def test_language(self):
  self.assertIsNone(e.normalize(self.row(source='A!@attrip')))
 def test_discussion(self):
  self.assertIsNone(e.normalize(self.row(discussionSource=True)))
 def test_cross_source_game(self):
  self.assertIsNone(e.normalize(self.row(source='Engadget',title='Xbox game console launch')))
 def test_item_language(self):
  self.assertIsNone(e.normalize(self.row(title='こんにちは Java')))
 def test_canonical_url(self):
  self.assertEqual(e.safeurl('https://example.com/a?utm_source=x#top'),'https://example.com/a')
 def test_source_medium_mapping(self):
  self.assertIsNone(e.normalize(self.row(source='Medium linux',url='https://custom.example/a')))
 def test_failed_run_preserves_snapshot(self):
  import tempfile,pathlib,unittest.mock as m,contextlib,io,types
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);(root/'data').mkdir();(root/'data/feed.json').write_text('{"original":true}')
   with m.patch.object(e,'ROOT',root),m.patch.object(e.subprocess,'run',return_value=types.SimpleNamespace(returncode=1,stdout='',stderr='fixture failure')),contextlib.redirect_stdout(io.StringIO()) as output:
    self.assertEqual(e.main(),1)
   self.assertEqual((root/'data/feed.json').read_text(),'{"original":true}')
   self.assertIn('preservedPreviousEdition',output.getvalue())
class NewTopics(Tests):
 def test_ifanr_exception_is_scoped(self):
  with __import__('unittest.mock',fromlist=['patch']).patch.dict(e.SOURCES, {'愛范兒':dict(e.SOURCES['愛范兒'],enabled=True)}):
   self.assertIsNotNone(e.normalize(self.row(source='愛范兒',url='https://www.ifanr.com/123',title='科技發布',feedExcerpt='简体中文原文')))
  self.assertIsNone(e.normalize(self.row(source='愛范兒',url='https://example.com/123')))
  with __import__('unittest.mock',fromlist=['patch']).patch.dict(e.SOURCES, {'Other Simplified':{'language':'zh-Hans','category':'科技'}}):
   self.assertIsNone(e.normalize(self.row(source='Other Simplified')))
 def test_disabled_source_held(self):
  self.assertIsNone(e.normalize(self.row(source='愛范兒',url='https://www.ifanr.com/123')))
 def test_elixir_topics(self):
  for title in ['Elixir release','Erlang release','Phoenix LiveView release','OTP supervisor improvements','Hex Elixir dependencies','BEAM actor model']:
   self.assertEqual(e.normalize(self.row(title=title))['category'],'Elixir / Erlang')
  for title in ['Phoenix weather','Hex color palette','OTP bank password','BEAM construction update']:
   self.assertNotEqual(e.normalize(self.row(title=title))['category'],'Elixir / Erlang')
 def test_cve_structured(self):
  a=e.normalize(self.row(title='Critical Apache CVE-2026-12345 remote code execution',feedExcerpt='Open source Linux vulnerability'))
  self.assertEqual(a['cveIds'],['CVE-2026-12345'])
  self.assertIsNone(e.normalize(self.row(title='Critical Apache CVE-2026-12 remote code execution',feedExcerpt='Open source Linux vulnerability')))
 def test_full_cve_ids_before_display_truncation(self):
  description='Open source Linux critical remote code execution. '+('padding '*50)+'CVE-2026-123456789 CVE-2026-23456'
  a=e.normalize(self.row(title='Critical Apache CVE-2026-10000 remote code execution',feedExcerpt=description))
  self.assertEqual(a['cveIds'],['CVE-2026-10000','CVE-2026-123456789','CVE-2026-23456'])
 def test_publisher_text_preserved(self):
  description='Original publisher text. '*40
  title='Original full publisher title '*20
  a=e.normalize(self.row(source='BBC News',title=title,feedExcerpt=description))
  self.assertEqual(a['title'],title.strip());self.assertEqual(a['summary'],description.strip());self.assertTrue(a['preservePublisherText'])
  self.assertEqual(a['attributionUrl'],'https://www.bbc.co.uk/news/world')
 def test_world_not_reclassified(self):
  with __import__('unittest.mock',fromlist=['patch']).patch.dict(e.SOURCES, {'World fixture':{'language':'en','category':'World / 國際'}}):
   for title in ['AI policy debated','Olympic Games announcement','A community faces vulnerability']:
    self.assertEqual(e.normalize(self.row(source='World fixture',title=title))['category'],'World / 國際')

class PublicationDates(unittest.TestCase):
 def test_whitespace_xml(self):
  import xml.etree.ElementTree as ET
  self.assertEqual(ET.fromstring(b' \n<?xml version="1.0"?><rss/>'.lstrip()).tag,'rss')

 def test_ithome_date_only(self):
  sp=importlib.util.spec_from_file_location('fetch','scripts/fetch-news.py');f=importlib.util.module_from_spec(sp);sp.loader.exec_module(f)
  self.assertEqual(f.ithome_page_date('<span class="created">2026-10-05</span>').isoformat(),'2026-10-04T16:00:00+00:00')
  self.assertIsNone(f.ithome_page_date('<p class="created">2026-10-05</p>'))
  self.assertIsNone(f.ithome_page_date('<span class="created">2026-02-31</span>'))
  self.assertIsNone(f.iso('2026-10-05  07:10:21'))
 def test_ithome_metadata_host_limit(self):
  sp=importlib.util.spec_from_file_location('fetch','scripts/fetch-news.py');f=importlib.util.module_from_spec(sp);sp.loader.exec_module(f)
  for u in ['https://evil.test/news/123','https://www.ithome.com.tw.evil.test/news/123','http://www.ithome.com.tw/news/123','https://www.ithome.com.tw/admin']:
   self.assertIsNone(f.recover_ithome_date(u))
class FeedFormats(unittest.TestCase):
 def setUp(self):
  sp=importlib.util.spec_from_file_location('fetch','scripts/fetch-news.py');self.f=importlib.util.module_from_spec(sp);sp.loader.exec_module(self.f)
 def feed(self,xml):
  import io,unittest.mock as m
  source={'name':'Fixture','category':'World / 國際','url':'https://example.com/rss','language':'en'}
  with m.patch.object(self.f,'open_source',return_value=io.BytesIO(xml.encode())):
   return self.f.fetch(source)
 def test_rdf_dc_date_and_id(self):
  d=self.f.NOW.isoformat()
  result=self.feed(f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/"><item rdf:about="https://example.com/fixture"><title>Fixture</title><dc:date>{d}</dc:date><description>Fixture summary</description></item></rdf:RDF>')
  self.assertEqual(result['source']['status'],'ok');self.assertEqual(len(result['candidates']),1);self.assertEqual(result['candidates'][0]['url'],'https://example.com/fixture')
 def test_syndication_attribution(self):
  d=self.f.NOW.isoformat()
  result=self.feed(f'<rss><channel><item><title>Fixture</title><link>https://example.com/fixture</link><pubDate>{d}</pubDate><source url="https://publisher.example/rss">Original publisher</source></item></channel></rss>')
  self.assertEqual(result['candidates'][0]['originalPublisher'],'Original publisher')
 def test_html_rejected(self):
  self.assertEqual(self.feed('<html><body>Not RSS</body></html>')['source']['status'],'failed')

class CVEIntegration(unittest.TestCase):
 def exercise(self,cached=False,last_result=None,refresh_result=None,validation_failure=False,stale_cache=False):
  import tempfile,pathlib,json,types,contextlib,io,unittest.mock as m
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp)
   for sub in ['data','.sites-runtime','dist']:(root/sub).mkdir()
   now=e.dt.datetime.now(e.UTC).isoformat()
   cards=[{'id':str(i),'title':f'Synthetic {i}','url':f'https://example.com/{i}','publishedAt':now,'source':'Fixture'} for i in range(30)]
   original=json.dumps({'stories':cards}).encode();(root/'data/feed.json').write_bytes(original)
   state={'version':1,'lastResult':last_result or {'status':'success','success':2,'skips':3,'errors':0,'pending':0,'quarantined':0}}
   (root/'data/cve-json-state.json').write_text(json.dumps(state))
   (root/'.sites-runtime/candidates.json').write_text(json.dumps({'fetchedAt':(e.dt.datetime.now(e.UTC)-e.dt.timedelta(hours=2)).isoformat() if stale_cache else now,'candidates':cards,'sourceStatus':[{'name':'Fixture' if i==0 else str(i),'status':'ok'} for i in range(10)]}))
   (root/'.sites-runtime/steam-deal-candidates.json').write_text(json.dumps([{'status':'not_on_sale','lastAttemptAt':now}]))
   def run(args,**kwargs):
    if args[:2]==['node','scripts/build-static.mjs']:(root/'dist/feed.json').write_bytes((root/'data/feed.json').read_bytes())
    return types.SimpleNamespace(stdout='',stderr='',returncode=1 if validation_failure and args[-1]=='scripts/verify-feeder.py' else 0)
   with m.patch.object(e,'ROOT',root),m.patch.object(e,'normalize',side_effect=lambda a:a),m.patch.object(e,'select_visible_deals',return_value=[]),m.patch.object(e,'merge_cards',side_effect=lambda cards,state,now:cards) as merge,m.patch.object(e,'refresh_cve_json',return_value=refresh_result or state['lastResult']) as refresh,m.patch.object(e.subprocess,'run',side_effect=run),m.patch.object(e.sys,'argv',['run-feeder.py']+(['--from-cache'] if cached else [])),contextlib.redirect_stdout(io.StringIO()) as stdout:
    code=e.main()
   result=json.loads(stdout.getvalue())
   self.assertEqual(refresh.call_count,0 if cached or stale_cache else 1)
   if code:self.assertEqual((root/'data/feed.json').read_bytes(),original)
   else:
    self.assertEqual(merge.call_count,1 if result['cveJSON']['status']=='success' else 0);self.assertEqual(result['sourceCount'],12)
   return code,result
 def test_cache_uses_successful_persisted_counts_without_fetch(self):
  code,result=self.exercise(cached=True);self.assertEqual(code,0);self.assertTrue(result['cveJSON']['cached']);self.assertEqual(result['cveJSON']['success'],2)
 def test_incomplete_cache_publishes_partial_with_actual_counts(self):
  counts={'status':'failure','success':4,'skips':2,'errors':1,'pending':7,'quarantined':1}
  code,result=self.exercise(cached=True,last_result=counts);self.assertEqual(code,0);self.assertTrue(result['partial']);self.assertEqual({k:result['cveJSON'][k] for k in counts},counts)
 def test_refresh_failure_publishes_partial_with_actual_counts(self):
  counts={'status':'failure','success':4,'skips':2,'errors':1,'pending':7,'quarantined':1}
  code,result=self.exercise(refresh_result=counts);self.assertEqual(code,0);self.assertTrue(result['partial']);self.assertEqual({k:result['cveJSON'][k] for k in counts},counts)
 def test_final_validation_failure_restores_snapshot(self):
  code,result=self.exercise(validation_failure=True);self.assertEqual(code,1);self.assertEqual(result['error'],'Final publication validation failed')
 def test_stale_intake_fails_before_fetch(self):
  code,result=self.exercise(cached=True,stale_cache=True);self.assertEqual(code,1);self.assertIn('one-hour',result['error'])
 def test_successful_refresh_merges_cards(self):
  code,result=self.exercise();self.assertEqual(code,0);self.assertEqual(result['cveJSON']['status'],'success')

if __name__=='__main__':unittest.main()
