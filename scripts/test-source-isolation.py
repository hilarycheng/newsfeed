"""Fixture-only source failure and no-retry regression tests."""
import copy, datetime as dt, importlib.util, json, pathlib, tempfile, unittest
from unittest.mock import Mock, patch
import source_control as s
import cve_json as c
NOW=dt.datetime(2026,10,13,23,tzinfo=dt.timezone.utc)
class Isolation(unittest.TestCase):
 def test_skip_never_uses_cached_source_cards(self):
  raw={'fetchedAt':NOW.isoformat(),'sourceStatus':[{'name':'held','status':'ok'},{'name':'failed','status':'failed'},{'name':'good','status':'ok'}],'candidates':[{'source':n} for n in ['held','failed','good']]}
  registry=[{'name':'held','url':'https://fixture.invalid/feed'}]
  cards,status=s.filter_intake(raw,registry,{'rss':{'https://fixture.invalid/feed':'held'}},NOW,True)
  self.assertEqual(cards,[{'source':'good'}]);self.assertEqual(status[0]['status'],'skipped');self.assertTrue(all(x['cached'] for x in status))
 def test_cache_age_and_future_fail(self):
  for when in [NOW-dt.timedelta(hours=1,seconds=1),NOW+dt.timedelta(seconds=1)]:
   with self.assertRaises(ValueError):s.require_recent(when.isoformat(),NOW)
 def test_paused_cve_no_fetch_no_state_write(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);state=c.empty_state();state['pending']={'CVE-2026-12345':'2026-10-13T22:00:00Z'};state['checkpoint']='2026-10-13T09:00:00Z';c.save_state(root,state)
   before=(root/'data/cve-json-state.json').read_bytes();fetch=Mock(side_effect=AssertionError('no network'));merge=Mock(side_effect=AssertionError('no stale projections'))
   cards,counts,status=s.cve_selection(root,NOW,[{'id':'ordinary'},{'cveIds':['CVE-2026-12345']}],{'cve':'held'},True,fetch,c.read_state,merge)
   self.assertEqual(cards,[{'id':'ordinary'}]);self.assertEqual(counts['pending'],1);self.assertEqual(status['status'],'skipped');fetch.assert_not_called();merge.assert_not_called();self.assertEqual(before,(root/'data/cve-json-state.json').read_bytes())
 def test_fetch_failure_keeps_pending_checkpoint_and_ordinary_cards(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);state=c.empty_state();state['pending']={'CVE-2026-12345':'2026-10-13T22:00:00Z'};state['checkpoint']='2026-10-13T09:00:00Z';c.save_state(root,state)
   fetch=Mock(side_effect=TimeoutError('fixture failure'))
   refresh=lambda root,now,**kw:c.refresh(root,now,fetch=fetch,**kw)
   cards,counts,status=s.cve_selection(root,NOW,[{'id':'ordinary'},{'cveIds':['CVE-2026-12345']}],{},False,refresh,c.read_state,c.merge_cards)
   self.assertEqual(cards,[{'id':'ordinary'}]);self.assertEqual(status['status'],'failed');self.assertEqual(counts['pending'],1);self.assertEqual(c.read_state(root)['checkpoint'],state['checkpoint']);self.assertEqual(fetch.call_count,1)
 def test_partial_batch_omits_all_cve_projections(self):
  state=c.empty_state();state['lastResult']={'status':'failure','pending':2};state['pending']={'a':'date','b':'date'}
  refresh=Mock();merge=Mock()
  cards,counts,status=s.cve_selection(None,NOW,[{'cveIds':['CVE-2026-12345']},{'id':'ordinary'}],{},True,refresh,lambda _:state,merge)
  self.assertEqual(cards,[{'id':'ordinary'}]);self.assertEqual(status['pending'],2);refresh.assert_not_called();merge.assert_not_called()
 def test_engine_errors_are_not_source_outages(self):
  with self.assertRaisesRegex(ValueError,'engine validation'):
   s.cve_selection(None,NOW,[],{},False,Mock(side_effect=ValueError('engine validation')),lambda _:c.empty_state(),c.merge_cards)
 def test_rss_hold_precedes_network(self):
  spec=importlib.util.spec_from_file_location('fetch_news',pathlib.Path(__file__).with_name('fetch-news.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with patch.object(m,'controls',return_value={'rss':{'https://fixture.invalid/rss':'held'}}),patch.object(m,'open_source',side_effect=AssertionError('no network')) as request:
   result=m.fetch({'name':'held','url':'https://fixture.invalid/rss'})
   self.assertEqual(result['source']['status'],'skipped');self.assertEqual(result['candidates'],[]);request.assert_not_called()
 def test_rss_http_failure_isolated(self):
  spec=importlib.util.spec_from_file_location('fetch_news',pathlib.Path(__file__).with_name('fetch-news.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with patch.object(m,'controls',return_value={}),patch.object(m,'open_source',side_effect=TimeoutError('fixture timeout')) as request:
   result=m.fetch({'name':'timeout','url':'https://fixture.invalid/rss'})
   self.assertEqual(result['source']['status'],'failed');self.assertEqual(result['candidates'],[]);self.assertEqual(request.call_count,1)
if __name__=='__main__':unittest.main()
