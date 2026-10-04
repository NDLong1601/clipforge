"""Build to TEMP, run an isolated real backend and Chrome browser E2E, save logs."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=REPO / 'review/reliability-r4/browser')
    args = parser.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    node = shutil.which('node')
    with tempfile.TemporaryDirectory(prefix='clipforge-r4-browser-') as temporary:
        root = Path(temporary)
        with (out / 'build.txt').open('wb') as log:
            subprocess.run([node, 'node_modules/vite/bin/vite.js', 'build', '--outDir', str(root / 'dist'), '--emptyOutDir'], cwd=REPO / 'frontend', stdout=log, stderr=subprocess.STDOUT, check=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = {**os.environ, 'CLIPFORGE_DATA': str(root), 'CLIPFORGE_TEST_DIST': str(root / 'dist'),
            'CLIPFORGE_BROWSER_URL': f'http://127.0.0.1:{port}', 'CLIPFORGE_BROWSER_REPORT': str(out / 'results.json'),
            'CLIPFORGE_BROWSER_ARTIFACTS': str(out / 'artifacts'), 'PYTHONUTF8': '1'}
        with (out / 'backend.txt').open('wb') as server_log:
            command = [sys.executable, '-m', 'uvicorn', 'tests.fixtures.reliability.r4_browser_server:app', '--host', '127.0.0.1', '--port', str(port)]
            def start_server():
                return subprocess.Popen(command, cwd=REPO, env=env, stdout=server_log, stderr=subprocess.STDOUT)
            process = start_server()
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if process.poll() is not None: raise RuntimeError('Browser fixture server exited before ready')
                    try:
                        urllib.request.urlopen(env['CLIPFORGE_BROWSER_URL'] + '/api/projects', timeout=1).close()
                        break
                    except OSError: time.sleep(0.2)
                else: raise RuntimeError('Browser fixture server did not become ready')
                with (out / 'browser.txt').open('wb') as log:
                    browser = subprocess.Popen([node, 'node_modules/@playwright/test/cli.js', 'test', '--config', 'playwright.config.cjs'], cwd=REPO / 'frontend', env=env, stdout=log, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 600
                    crashed_at = None
                    restarts = 0
                    while browser.poll() is None:
                        if time.monotonic() > deadline:
                            browser.terminate(); browser.wait(timeout=10); raise RuntimeError('Browser suite timed out')
                        if process.poll() == 91:
                            crashed_at = crashed_at or time.monotonic()
                            if time.monotonic() - crashed_at >= 8:
                                process = start_server(); restarts += 1; crashed_at = None
                        elif process.poll() is not None:
                            browser.terminate(); browser.wait(timeout=10); raise RuntimeError('Unexpected fixture server exit')
                        time.sleep(0.1)
                print(json.dumps({'output': str(out), 'exit_code': browser.returncode, 'browser': env.get('CLIPFORGE_BROWSER_CHANNEL', 'chrome'), 'isolated': True, 'backend_crash_restarts': restarts}))
                (out / 'lifecycle.json').write_text(json.dumps({'backend_crash_restarts': restarts, 'data_root': str(root), 'own_server_pid': process.pid, 'exit_code': browser.returncode}), encoding='utf-8')
                return browser.returncode
            finally:
                # Only this fixture's exact child is stopped, never a user's backend.
                process.terminate()
                try: process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
