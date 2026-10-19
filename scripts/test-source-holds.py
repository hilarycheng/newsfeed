"""Offline persisted-denial regression tests. No transport is imported or called."""
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('hold_source', pathlib.Path(__file__).with_name('hold-source.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Holds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'lib').mkdir()
        (self.root / 'config').mkdir()
        self.url = 'https://nodejs.org/en/feed/blog.xml'
        self.registry = [{'name': 'Node.js', 'url': self.url},
                         {'name': 'Disabled', 'url': 'https://example.org/rss', 'enabled': False}]
        (self.root / 'lib/source-registry.json').write_text(json.dumps(self.registry))
        self.path = self.root / 'config/source-holds.json'
        self.initial = {'rss': {'https://openai.com/news/rss.xml': 'existing'},
                        'cve': 'held', 'networkPolicy': 'fixed inventory'}
        self.path.write_text(json.dumps(self.initial))

    def test_exact_url_added_without_changing_existing_policy(self):
        result = m.hold_source(self.root, self.url)
        policy = json.loads(self.path.read_text())
        self.assertEqual(result['networkRequestsMade'], 0)
        self.assertTrue(result['changed'])
        self.assertEqual(policy['rss']['https://openai.com/news/rss.xml'], 'existing')
        self.assertEqual(policy['cve'], 'held')
        self.assertEqual(policy['networkPolicy'], 'fixed inventory')
        self.assertNotIn('networkRefreshHold', policy)

    def test_existing_hold_is_byte_identical(self):
        m.hold_source(self.root, self.url)
        before = self.path.read_bytes()
        self.assertFalse(m.hold_source(self.root, self.url)['changed'])
        self.assertEqual(before, self.path.read_bytes())

    def test_unknown_alternate_and_disabled_urls_do_not_mutate(self):
        before = self.path.read_bytes()
        for url in ['https://opaque.blob.core.windows.net/feed', self.url+'?alternate=1',
                    self.url.replace('https:', 'http:'), 'https://example.org/rss']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                m.hold_source(self.root, url)
        self.assertEqual(before, self.path.read_bytes())

    def test_duplicate_identity_fails_without_mutation(self):
        self.registry.append({'name': 'Other', 'url': self.url})
        (self.root / 'lib/source-registry.json').write_text(json.dumps(self.registry))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            m.hold_source(self.root, self.url)
        self.assertEqual(before, self.path.read_bytes())

    def test_global_hold_is_never_removed(self):
        self.initial['networkRefreshHold'] = 'unknown target'
        self.path.write_text(json.dumps(self.initial))
        m.hold_source(self.root, self.url)
        self.assertEqual(json.loads(self.path.read_text())['networkRefreshHold'], 'unknown target')

    def test_hostname_hold_all_matches_and_preserves_other_holds(self):
        self.registry.extend([{'name': 'Second', 'url': 'https://nodejs.org/second'},
                              {'name': 'Lookalike', 'url': 'https://nodejs.org.example.com/rss'}])
        (self.root / 'lib/source-registry.json').write_text(json.dumps(self.registry))
        result = m.hold_domain(self.root, 'NODEJS.ORG.')
        policy = json.loads(self.path.read_text())
        self.assertEqual(result['evidenceType'], 'hostname')
        self.assertEqual(result['registeredSourceCount'], 2)
        self.assertEqual(result['networkRequestsMade'], 0)
        self.assertEqual(policy['rss'], self.initial['rss'])
        self.assertEqual(policy['cve'], 'held')
        from source_control import held_rss
        self.assertTrue(held_rss(self.registry[0], policy))
        self.assertTrue(held_rss(self.registry[2], policy))
        self.assertFalse(held_rss(self.registry[3], policy))
        before = self.path.read_bytes()
        self.assertFalse(m.hold_domain(self.root, 'nodejs.org')['changed'])
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_unknown_hostname_no_mutation(self):
        before = self.path.read_bytes()
        for host in ['https://nodejs.org/rss', '*.nodejs.org', 'org', 'evilnodejs.org',
                     'nodejs.org:443', 'nodejs.org/alternate', ' nodejs.org', 'sub.nodejs.org']:
            with self.subTest(host=host), self.assertRaises(ValueError):
                m.hold_domain(self.root, host)
        self.assertEqual(before, self.path.read_bytes())

    def test_held_hostname_blocks_transport_even_on_other_path(self):
        m.hold_domain(self.root, 'nodejs.org')
        from source_gateway import open_source, SourcePolicyError
        from unittest.mock import patch
        with patch('urllib.request.build_opener') as transport:
            with self.assertRaises(SourcePolicyError):
                open_source(self.root, 'rss', 'Node.js', self.url)
            transport.assert_not_called()



if __name__ == '__main__':
    unittest.main()
