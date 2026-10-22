#!/usr/bin/env python3
"""Exercise real Chrome service workers on localhost, without Node or external requests."""
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='news-pwa-') as work:
        work = Path(work)
        editions = {}
        for version in (1, 2):
            project = work / str(version)
            (project / 'data').mkdir(parents=True)
            feed = json.loads((ROOT / 'scripts/fixtures/news-ui.json').read_text())
            feed['note'] = f'PWA fixture {version}'
            (project / 'data/feed.json').write_text(json.dumps(feed))
            subprocess.run([str(ROOT / 'target/debug/news-feeder'), '--quiet', '--build-only'],
                           cwd=project, check=True)
            editions[version] = {p.name: p.read_bytes() for p in (project / 'dist').iterdir()}
        state = {'mode': 'online', 'version': 1, 'result': None}
        completed = threading.Event()
        harness = ('<!doctype html><iframe id="reader"></iframe><script>' +
                   (ROOT / 'tests/pwa.js').read_text() + '</script>').encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send_body(self, body, kind='text/html', status=200):
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                if url.path == '/test.html':
                    return self.send_body(harness)
                if url.path == '/progress':
                    state['step'] = urllib.parse.parse_qs(url.query)['step'][0]
                    return self.send_body(b'ok', 'text/plain')
                if url.path == '/control':
                    args = urllib.parse.parse_qs(url.query)
                    state.update(mode=args['mode'][0], version=int(args['version'][0]))
                    return self.send_body(b'ok', 'text/plain')
                if url.path == '/outside.html':
                    return self.send_body(b'outside', status=503)
                if url.path.startswith('/newsfeed/'):
                    if state['mode'] == 'offline':
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    if state['mode'] == 'error':
                        return self.send_body(b'unavailable', status=503)
                    if state['mode'] == 'truncated':
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html')
                        self.send_header('Content-Length', '10000')
                        self.end_headers()
                        self.wfile.write(b'<!doctype html><title>incomplete')
                        return
                    name = url.path.removeprefix('/newsfeed/') or 'index.html'
                    body = editions[state['version']].get(name)
                    if body is not None:
                        kind = 'text/javascript' if name == 'sw.js' else 'image/png' if name.endswith('.png') else 'application/manifest+json' if name.endswith('.webmanifest') else 'text/html'
                        return self.send_body(body, kind)
                self.send_body(b'not found', status=404)

            def do_POST(self):
                if self.path == '/report':
                    state['result'] = self.rfile.read(int(self.headers['Content-Length'])).decode()
                    self.send_body(b'ok', 'text/plain')
                    completed.set()
                else:
                    self.send_body(b'not found', status=404)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with (work / 'chrome.log').open('w') as log:
            browser = subprocess.Popen([
                os.environ.get('CHROME_BIN', 'google-chrome'), '--headless', '--no-sandbox',
                '--disable-gpu', '--disable-background-networking', '--no-proxy-server',
                '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
                '--remote-debugging-port=0', f'--user-data-dir={work / "profile"}',
                f'http://127.0.0.1:{server.server_port}/test.html',
            ], stdout=log, stderr=log)
            try:
                assert completed.wait(45), f'PWA browser test timed out: {state}\n{(work / "chrome.log").read_text()[-2500:]}'
                assert state['result'] == 'passed', state['result']
                print('PWA passed: manifest, icons, install control, offline navigation/filters, HTTP failure, edition update, scoped cache cleanup')
            finally:
                browser.terminate()
                browser.wait(timeout=10)
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    main()
