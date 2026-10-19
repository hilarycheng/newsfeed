"""Synthetic transports only. Any accidental real socket use fails the suite."""
import io
import json
import pathlib
import socket
import tempfile
import unittest
import urllib.request
import urllib.response
from email.message import Message
from unittest.mock import patch

import source_gateway as gateway


class FakeHTTPS(urllib.request.BaseHandler):
    handler_order = 100

    def __init__(self, code=200, location=None):
        self.requests = []
        self.code, self.location = code, location

    def https_open(self, request):
        self.requests.append(request.full_url)
        headers = Message()
        if self.location:
            headers['Location'] = self.location
        response = urllib.response.addinfourl(io.BytesIO(b'fixture body'), headers, request.full_url, self.code)
        response.msg = 'fixture'
        return response


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'config').mkdir()
        (self.root / 'lib').mkdir()
        registry = [
            {'name': 'Fixture', 'url': 'https://example.com/rss'},
            {'name': 'Held', 'url': 'https://inside.java/feed.xml'},
            {'name': 'iThome', 'url': 'https://www.ithome.com.tw/rss'},
            {'name': 'Disabled', 'url': 'https://example.org/rss', 'enabled': False},
        ]
        (self.root / 'config/source-registry.json').write_text(json.dumps(registry))
        (self.root / 'config/steam-deals.json').write_text(json.dumps([{'appId': 123}]))
        self.holds({'rss': {'https://inside.java/feed.xml': 'held'}, 'cve': 'held'})
        self.no_socket = patch.object(socket, 'create_connection', side_effect=AssertionError('real socket forbidden'))
        self.no_socket.start()
        self.addCleanup(self.no_socket.stop)

    def holds(self, value):
        (self.root / 'config/source-holds.json').write_text(json.dumps(value))

    def transport(self, fake):
        # All test traffic terminates in FakeHTTPS, never a socket or proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            gateway.RejectRedirects(self.root, 'rss', 'Fixture'), fake)
        return patch.object(gateway.urllib.request, 'build_opener', return_value=opener)

    def request(self, url='https://example.com/rss', identity='Fixture', adapter='rss'):
        return gateway.open_source(self.root, adapter, identity, url)

    def test_registered_initial_url_only(self):
        fake = FakeHTTPS()
        with self.transport(fake), self.request() as response:
            self.assertEqual(response.read(), b'fixture body')
        self.assertEqual(fake.requests, ['https://example.com/rss'])
        audit = (self.root / '.sites-runtime/source-request-audit.jsonl').read_text()
        self.assertNotIn('fixture body', audit)

    def test_global_hold_precedes_transport_for_every_adapter(self):
        self.holds({'networkRefreshHold': 'unresolved review'})
        with patch.object(gateway.urllib.request, 'build_opener') as opener:
            for adapter, identity, url in [('rss', 'Fixture', 'https://example.com/rss'),
                ('steam', '123', 'https://store.steampowered.com/api/appdetails?appids=123&cc=hk&l=english'),
                ('ithome_metadata', 'iThome', 'https://www.ithome.com.tw/news/123'),
                ('cve', 'CVE List V5', gateway.CVE_REVISION)]:
                with self.subTest(adapter=adapter), self.assertRaisesRegex(gateway.SourcePolicyError, 'network_refresh_held'):
                    self.request(url, identity, adapter)
            opener.assert_not_called()

    def test_held_and_disabled_sources_have_zero_requests(self):
        fake = FakeHTTPS()
        with self.transport(fake):
            for name, url in [('Held', 'https://inside.java/feed.xml'), ('Disabled', 'https://example.org/rss')]:
                with self.subTest(name=name), self.assertRaises(gateway.SourcePolicyError):
                    self.request(url, name)
        self.assertEqual(fake.requests, [])

    def test_exact_denied_source_holds_leave_other_sources_available(self):
        denied = [('Node.js', 'https://nodejs.org/en/feed/blog.xml'),
                  ('OpenAI', 'https://openai.com/news/rss.xml')]
        registry_path = self.root / 'config/source-registry.json'
        registry = json.loads(registry_path.read_text())
        registry.extend({'name': name, 'url': url} for name, url in denied)
        registry_path.write_text(json.dumps(registry))
        self.holds({'rss': {url: 'access review denied; skip' for _, url in denied}, 'cve': 'held'})
        fake = FakeHTTPS()
        with self.transport(fake):
            for name, url in denied:
                with self.subTest(name=name), self.assertRaises(gateway.SourcePolicyError):
                    self.request(url, name)
            with self.request() as response:
                self.assertEqual(response.read(), b'fixture body')
        self.assertEqual(fake.requests, ['https://example.com/rss'])

    def test_opaque_private_and_unregistered_urls_have_zero_requests(self):
        fake = FakeHTTPS()
        urls = ['https://opaque.blob.core.windows.net/data/feed.xml', 'https://127.0.0.1/rss',
            'https://10.0.0.1/rss', 'https://169.254.169.254/rss', 'https://[::1]/rss',
            'https://localhost/rss', 'https://service.internal/rss', 'https://example.org/rss',
            'http://example.com/rss', 'https://user:secret@example.com/rss',
            'https://example.com:444/rss', 'https://example.com/rss?unregistered=1',
            'https://example.com./rss', 'https://example.com/rss#fragment',
            'https://example.com\\@127.0.0.1/rss']
        with self.transport(fake):
            for url in urls:
                with self.subTest(url=url), self.assertRaises(gateway.SourcePolicyError):
                    self.request(url)
        self.assertEqual(fake.requests, [])

    def test_source_identity_cannot_borrow_another_url(self):
        fake = FakeHTTPS()
        with self.transport(fake), self.assertRaisesRegex(gateway.SourcePolicyError, 'unregistered_destination'):
            self.request(identity='Unknown')
        self.assertEqual(fake.requests, [])

    def test_all_redirects_stop_before_target_request(self):
        for code in (301, 302, 303, 307, 308):
            for destination in ('https://opaque.blob.core.windows.net/data', 'https://127.0.0.1/secret',
                                'https://example.org/rss', '/another-path', 'https://example.com/rss'):
                fake = FakeHTTPS(code, destination)
                with self.subTest(code=code, destination=destination), self.transport(fake), self.assertRaises(gateway.RedirectBlocked):
                    self.request()
                self.assertEqual(fake.requests, ['https://example.com/rss'])

    def test_steam_query_is_exact_and_registered(self):
        base = 'https://store.steampowered.com/api/appdetails?appids=123&cc=hk&l=english'
        self.assertEqual(gateway.target_rule(self.root, 'steam', '123', base), base)
        for url in (base.replace('123', '124'), base.replace('cc=hk', 'cc=us'), base + '&redirect=https://example.org'):
            with self.assertRaises(gateway.SourcePolicyError):
                gateway.target_rule(self.root, 'steam', '123', url)

    def test_ithome_path_boundaries_and_source_hold(self):
        good = 'https://www.ithome.com.tw/news/123'
        self.assertEqual(gateway.target_rule(self.root, 'ithome_metadata', 'iThome', good), 'https://www.ithome.com.tw/rss')
        for url in (good + '?next=https://example.org', good + '/other', good.replace('www.ithome.com.tw', 'evil.example.com')):
            with self.assertRaises(gateway.SourcePolicyError):
                gateway.target_rule(self.root, 'ithome_metadata', 'iThome', url)
        self.holds({'rss': {'https://www.ithome.com.tw/rss': 'held'}})
        with patch.object(gateway.urllib.request, 'build_opener') as opener, self.assertRaisesRegex(gateway.SourcePolicyError, 'source_held'):
            self.request(good, 'iThome', 'ithome_metadata')
        opener.assert_not_called()

    def test_cve_held_and_only_immutable_official_paths(self):
        with self.assertRaisesRegex(gateway.SourcePolicyError, 'cve_held'):
            gateway.authorize_request(self.root, 'cve', 'CVE List V5', gateway.CVE_REVISION)
        prefix = 'https://raw.githubusercontent.com/CVEProject/cvelistV5/' + 'a' * 40 + '/cves/'
        for url in (gateway.CVE_REVISION, prefix + 'deltaLog.json', prefix + '2026/12xxx/CVE-2026-12345.json'):
            self.assertEqual(gateway.target_rule(self.root, 'cve', 'CVE List V5', url), url)
        for url in (prefix.replace('a' * 40, 'main') + 'deltaLog.json', prefix + '2025/12xxx/CVE-2026-12345.json', prefix + '../secret'):
            with self.assertRaises(gateway.SourcePolicyError):
                gateway.target_rule(self.root, 'cve', 'CVE List V5', url)

    def test_audit_removes_credentials_and_signed_query_values(self):
        gateway.audit(self.root, 'rss', 'Fixture', 'https://user:password@example.com/feed?token=secret',
            'blocked', redirectLocation='https://blob.example.com/data?sig=private-value#secret')
        text = (self.root / '.sites-runtime/source-request-audit.jsonl').read_text()
        for value in ('password', 'secret', 'private-value', 'user:'):
            self.assertNotIn(value, text)
        self.assertIn('blob.example.com', text)

    def test_preinstalled_platform_opener_is_never_replaced(self):
        installed = object()
        with patch.object(gateway.urllib.request, '_opener', installed), patch.object(gateway.urllib.request, 'build_opener') as build:
            with self.assertRaisesRegex(gateway.SourcePolicyError, 'installed_opener_requires_review'):
                self.request()
            build.assert_not_called()
            self.assertIs(gateway.urllib.request._opener, installed)


if __name__ == '__main__':
    unittest.main()
