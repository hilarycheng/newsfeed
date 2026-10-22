#!/usr/bin/env python3
"""Offline publication checks using a fake feeder and a real local Git remote."""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPO = 'git@hilary.git:hilarycheng/newsfeed.git'
ASSETS = ['apple-touch-icon.png', 'icon-192.png', 'icon-512.png', 'index.html', 'manifest.webmanifest', 'sw.js']


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='news publish ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'feeder'
        self.root.mkdir()
        self.remote = self.base / 'remote.git'
        self.env = {
            'PATH': '/usr/bin:/bin', 'HOME': str(self.base), 'LANG': 'C.UTF-8',
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': str(self.base / 'gitconfig'),
        }
        self.git('init', '--quiet', '--bare', '--initial-branch=main', str(self.remote))
        self.git('config', '--global', f'url.{self.remote.as_uri()}.insteadOf', REPO)
        shutil.copy(ROOT / 'update_news.sh', self.root / 'update_news.sh')
        (self.root / '.sites-runtime').mkdir()
        (self.root / 'dist').mkdir()
        (self.root / 'data').mkdir()
        (self.root / 'data/private.json').write_text('private state')
        (self.root / 'source.rs').write_text('private source')
        (self.root / 'news-feeder').write_text('''#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > .sites-runtime/feeder-args
cp fixture-result.json .sites-runtime/feeder-result.json
exit "$(cat fixture-exit)"
''')
        (self.root / 'news-feeder').chmod(0o755)
        (self.root / 'fixture-exit').write_text('0')
        self.edition('first')

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(['git', *args], cwd=cwd, env=self.env,
                              text=True, capture_output=True, check=check)

    def edition(self, text):
        html = f'<!doctype html><title>{text}</title>'.encode()
        (self.root / 'dist/index.html').write_bytes(html)
        for name in ASSETS:
            if name != 'index.html':
                (self.root / 'dist' / name).write_bytes(('fixture ' + name).encode())
        self.result = {'status': 'success', 'artifact': 'dist/index.html',
                       'sha256': hashlib.sha256(html).hexdigest(),
                       'artifacts': {name: hashlib.sha256((self.root / 'dist' / name).read_bytes()).hexdigest() for name in ASSETS}}
        self.save_result()

    def save_result(self):
        (self.root / 'fixture-result.json').write_text(json.dumps(self.result))

    def run_publish(self, *args, code=0):
        result = subprocess.run(['bash', str(self.root / 'update_news.sh'), *args],
                                cwd=self.base, env=self.env, text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def head(self):
        return self.git('rev-parse', 'refs/heads/main', cwd=self.remote).stdout.strip()

    def test_only_artifacts_change_and_unchanged_editions_do_not_commit(self):
        self.run_publish('--build-only')
        self.assertEqual((self.root / '.sites-runtime/feeder-args').read_text(),
                         '--quiet\n--build-only\n')
        files = self.git('ls-tree', '--name-only', 'main', cwd=self.remote).stdout
        self.assertEqual(files.splitlines(), ['.nojekyll', *ASSETS])
        self.assertEqual(self.git('show', 'main:index.html', cwd=self.remote).stdout,
                         (self.root / 'dist/index.html').read_text())
        first = self.head()
        self.run_publish()
        self.assertEqual(self.head(), first)
        self.edition('second')
        self.run_publish('--from-cache')
        self.assertNotEqual(self.head(), first)

    def test_bad_results_never_replace_the_remote(self):
        self.run_publish()
        first = self.head()
        self.edition('not published')
        (self.root / 'fixture-exit').write_text('1')
        self.run_publish(code=1)
        (self.root / 'fixture-exit').write_text('0')
        for patch in [{'status': 'failure'}, {'artifact': 'data/private.json'},
                      {'sha256': '0' * 64}, {'sha256': 'invalid'}]:
            with self.subTest(patch=patch):
                self.edition('not published')
                self.result.update(patch)
                self.save_result()
                result = subprocess.run(['bash', str(self.root / 'update_news.sh')],
                                        env=self.env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.head(), first)
        self.assertEqual(list((self.root / '.sites-runtime').glob('pages-build.*')), [])

    def test_failed_push_is_retried_without_an_extra_commit(self):
        self.run_publish()
        first = self.head()
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        self.edition('pending')
        self.run_publish(code=1)
        self.assertEqual(self.head(), first)
        local = self.root / '.sites-runtime/pages'
        pending = self.git('rev-parse', 'HEAD', cwd=local).stdout.strip()
        self.assertNotEqual(pending, first)
        hook.unlink()
        self.run_publish()
        self.assertEqual(self.head(), pending)

    def test_missing_tampered_or_unexpected_pwa_assets_are_rejected(self):
        self.run_publish()
        first = self.head()
        for mode in ['missing', 'tampered', 'extra']:
            with self.subTest(mode=mode):
                self.edition('not published')
                if mode == 'missing':
                    (self.root / 'dist/sw.js').unlink()
                elif mode == 'tampered':
                    (self.root / 'dist/icon-192.png').write_bytes(b'corrupt')
                else:
                    self.result['artifacts']['../private.json'] = '0' * 64
                    self.save_result()
                self.run_publish(code=1 if mode != 'extra' else 4)
                self.assertEqual(self.head(), first)

    def test_overlapping_runs_and_unknown_arguments_do_not_start_the_feeder(self):
        with (self.root / '.sites-runtime/publish.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_publish(code=75)
        self.run_publish('--help', code=2)
        self.assertFalse((self.root / '.sites-runtime/feeder-args').exists())

    def test_unexpected_tracked_files_are_not_pushed(self):
        self.run_publish()
        first = self.head()
        local = self.root / '.sites-runtime/pages'
        (local / 'secret.json').write_text('do not publish')
        self.git('add', 'secret.json', cwd=local)
        self.run_publish(code=1)
        self.assertEqual(self.head(), first)

    def test_remote_updates_fast_forward_but_divergence_is_preserved(self):
        self.run_publish()
        peer = self.base / 'peer'
        self.git('clone', '--quiet', str(self.remote), str(peer))
        for text in ['remote update', 'remote divergence']:
            (peer / 'index.html').write_text(text)
            self.git('add', 'index.html', cwd=peer)
            self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com',
                     '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', text, cwd=peer)
            self.git('push', '--quiet', cwd=peer)
            if text == 'remote update':
                self.run_publish()
                self.git('pull', '--quiet', '--ff-only', cwd=peer)
                hook = self.remote / 'hooks/pre-receive'
                hook.write_text('#!/bin/sh\nexit 1\n')
                hook.chmod(0o755)
                self.edition('local pending')
                self.run_publish(code=1)
                hook.unlink()
            else:
                remote_head = self.head()
                local = self.root / '.sites-runtime/pages'
                local_head = self.git('rev-parse', 'HEAD', cwd=local).stdout
                self.run_publish(code=128)
                self.assertEqual(self.head(), remote_head)
                self.assertEqual(self.git('rev-parse', 'HEAD', cwd=local).stdout, local_head)


if __name__ == '__main__':
    unittest.main()
