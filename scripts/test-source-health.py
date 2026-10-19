"""Offline fixtures proving isolated failures do not abort healthy editions."""
import importlib.util
import pathlib
import unittest
import urllib.error
from unittest.mock import patch
from source_health import require_rss_health, source_counts, source_failure


class Health(unittest.TestCase):
    def test_one_error_does_not_stop_188_good_sources(self):
        rows = [{'status': 'ok'} for _ in range(188)] + [{'status': 'failed'}]
        self.assertEqual(require_rss_health(rows)['sourceFailureCount'], 1)

    def test_existing_threshold_and_holds_still_count(self):
        rows = [{'status': 'ok'} for _ in range(70)] + [{'status': 'skipped'} for _ in range(30)]
        self.assertEqual(require_rss_health(rows)['sourceSkippedCount'], 30)
        rows[0]['status'] = 'failed'
        with self.assertRaises(RuntimeError):
            require_rss_health(rows)
        with self.assertRaises(RuntimeError):
            require_rss_health([{'status': 'ok'}] * 9)

    def test_failure_identity_and_http_code_without_response_or_secret(self):
        error = urllib.error.HTTPError('https://example.org/feed?token=secret', 503, 'private content', {}, None)
        result = source_failure({'id': 'fixture', 'name': 'Fixture'}, error)
        self.assertEqual(result['source']['sourceId'], 'fixture')
        self.assertEqual(result['source']['httpStatus'], 503)
        self.assertNotIn('secret', str(result))
        self.assertNotIn('private content', str(result))
        self.assertEqual(result['candidates'], [])

    def test_failed_source_then_good_source_continues(self):
        spec = importlib.util.spec_from_file_location('fetch_news', pathlib.Path(__file__).with_name('fetch-news.py'))
        fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'<rss><channel/></rss>'
        with patch.object(fetch, 'controls', return_value={}), patch.object(fetch, 'open_source', side_effect=[TimeoutError('fixture'), Response()]) as request:
            bad = fetch.fetch({'name': 'Bad', 'id': 'bad', 'url': 'https://example.org/bad'})
            good = fetch.fetch({'name': 'Good', 'url': 'https://example.org/good'})
        self.assertEqual(bad['source']['errorCode'], 'transport_error')
        self.assertEqual(good['source']['status'], 'ok')
        self.assertEqual(request.call_count, 2)


if __name__ == '__main__':
    unittest.main()
