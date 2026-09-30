"""Set up ClipForge, start the local server, then open its web interface."""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def clipforge_running(port):
    url = f'http://127.0.0.1:{port}'
    try:
        with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
            info = json.load(response)
        if info.get('product') == 'clipforge-local':
            return True
        # Recognize a server started by an older ClipForge release.
        if info.get('version') == '1.0.0':
            with urllib.request.urlopen(url, timeout=1) as response:
                return b'ClipForge' in response.read(4096)
    except Exception:
        pass
    return False


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(.5)
        return connection.connect_ex(('127.0.0.1', port)) == 0


def choose_port(requested=None):
    ports = [requested] if requested else list(range(8765, 8786))
    # Search the complete candidate range for an existing ClipForge before
    # treating an earlier free port as a reason to launch another server.
    for port in ports:
        if clipforge_running(port):
            return port, True
    for port in ports:
        if not port_in_use(port):
            return port, False
    if requested:
        raise SystemExit(f'Port {requested} is being used by another program.')
    raise SystemExit('No free port from 8765 to 8785 is available for ClipForge.')


def open_browser(url):
    try:
        opened = webbrowser.open(url, new=2)
    except Exception:
        opened = False
    if opened:
        return
    try:
        if os.name == 'nt':
            os.startfile(url)
            return
    except OSError:
        pass
    if not opened:
        print('Open this URL in your browser: ' + url, flush=True)


def main():
    parser = argparse.ArgumentParser(description='ClipForge Local')
    parser.add_argument('--port', type=int, metavar='PORT')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--setup-only', action='store_true')
    args = parser.parse_args()
    if args.port is not None and not 1024 <= args.port <= 65535:
        raise SystemExit('Port must be between 1024 and 65535.')
    if sys.version_info < (3, 12):
        raise SystemExit('Python 3.12 or newer is required for ClipForge.')
    os.chdir(ROOT)
    python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('Creating the local Python environment...', flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    stamp = ROOT / '.venv' / '.clipforge-ready'
    install_file = ROOT / ('requirements-lock.txt' if (ROOT / 'requirements-lock.txt').exists() else 'requirements.txt')
    signature = hashlib.sha256(install_file.read_bytes()).hexdigest()
    if not stamp.exists() or stamp.read_text() != signature:
        print('Installing required packages (this may take a while on first launch)...', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(install_file)], check=True)
        stamp.write_text(signature)
    if not (ROOT / 'frontend' / 'dist' / 'index.html').exists():
        npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
        if not npm:
            raise SystemExit('Node.js is needed to build the interface. The delivered ZIP includes frontend/dist.')
        subprocess.run([npm, 'ci'], cwd=ROOT / 'frontend', check=True)
        subprocess.run([npm, 'run', 'build'], cwd=ROOT / 'frontend', check=True)
    if args.setup_only:
        print('Setup complete. Double-click MO_CLIPFORGE.bat to open ClipForge.')
        return

    port, running = choose_port(args.port)
    url = f'http://127.0.0.1:{port}'
    if running:
        print('ClipForge is already running: ' + url, flush=True)
        if not args.no_browser:
            open_browser(url)
        return

    if not args.no_browser:
        def open_when_ready():
            for _ in range(60):
                if clipforge_running(port):
                    open_browser(url)
                    return
                time.sleep(1)
        threading.Thread(target=open_when_ready, daemon=True).start()
    print('Opening ClipForge at ' + url, flush=True)
    print('Keep this window open while using ClipForge. Press Ctrl+C to stop.', flush=True)
    try:
        subprocess.run([str(python), '-m', 'uvicorn', 'backend.main:app',
                        '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, check=True)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
