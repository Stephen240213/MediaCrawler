"""One owned Workbench + Quick Tunnel pair, launched by the macOS user agent.

No browser login or collection is initiated by this supervisor. Credentials stay
in the private environment file and Wrangler's own local credential storage.
"""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import dotenv_values
from .lease import Lease
from .redaction import redact

WRANGLER = 'wrangler@4.152.0'
TUNNEL_URL = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com\b')


@dataclass(frozen=True)
class DesktopSettings:
    root: Path
    env_file: Path
    runtime: Path
    python: str
    cloudflared: str
    npx: str
    tool_path: str
    public_origin: str
    local_port: int = 8090
    remote_port: int = 8081

    @property
    def state_file(self):
        return self.runtime / 'desktop' / 'status.json'

    @property
    def local_origin(self):
        return f'http://127.0.0.1:{self.local_port}'

    def validate(self):
        if not self.env_file.is_file():
            raise ValueError('Remote configuration is missing. Complete remote.env setup first.')
        if not (self.root / 'api/webui/index.html').is_file():
            raise ValueError('WebUI build is missing. Run npm --prefix webui run build first.')
        if self.local_port == self.remote_port or any(not 1024 <= p <= 65535 for p in (self.local_port, self.remote_port)):
            raise ValueError('Use two different ports between 1024 and 65535.')
        url = urlsplit(self.public_origin)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.port or url.path or url.query or url.fragment:
            raise ValueError('Use the HTTPS Worker origin without a path or trailing slash.')
        if not re.fullmatch(r'[a-z0-9.-]+\.workers\.dev', url.hostname):
            raise ValueError('Quick Tunnel launcher requires an existing workers.dev origin.')
        for program in (self.python, self.cloudflared, self.npx):
            if not Path(program).is_file() or not os.access(program, os.X_OK):
                raise ValueError('A required executable is missing. Check Python, Node and cloudflared.')
        values = {**os.environ, **{k: v for k, v in dotenv_values(self.env_file).items() if v is not None}}
        # Validate configured Access identity before starting a listener.
        from .access import AccessSettings
        try:
            AccessSettings('https://' + values.get('MC_ACCESS_TEAM', '') + '.cloudflareaccess.com',
                           values.get('MC_ACCESS_AUD', ''), self.public_origin,
                           frozenset(x.strip().lower() for x in values.get('MC_OPERATORS', '').split(',') if x.strip()),
                           frozenset(x.strip().lower() for x in values.get('MC_VIEWERS', '').split(',') if x.strip()))
        except ValueError:
            raise ValueError('Remote Access configuration is incomplete or invalid. Check private remote.env.') from None

    def save(self, path):
        data = {k: str(v) if isinstance(v, Path) else v for k, v in self.__dict__.items()}
        atomic_json(path, data)

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        for key in ('root', 'env_file', 'runtime'):
            data[key] = Path(data[key])
        return cls(**data)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temporary.open('w') as file:
        json.dump(value, file, ensure_ascii=False)
    temporary.chmod(0o600)
    os.replace(temporary, path)


def occupied(port):
    with socket.socket() as connection:
        connection.settimeout(.3)
        return connection.connect_ex(('127.0.0.1', port)) == 0


def http_status(url, host=None):
    headers = {'Host': host} if host else {}
    # Bypass environment proxies only for our loopback readiness requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if urlsplit(url).hostname == '127.0.0.1' else urllib.request.build_opener()
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=4) as response:
            body = response.read(4096)
            if url.endswith('/api/health') and response.status == 200:
                return 200 if json.loads(body) == {'status': 'ok'} else 500
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (OSError, ValueError):
        return None


class DesktopSupervisor:
    def __init__(self, settings, *, probe=http_status, port_check=occupied, popen=subprocess.Popen):
        self.settings = settings
        self.probe = probe
        self.port_check = port_check
        self.popen = popen
        self.stop_event = threading.Event()
        self.connected = threading.Event()
        self.tunnel_url = None
        self.children = []
        self.readers = []
        self.failure = None
        self.logger = logging.getLogger(f'mediacrawler.desktop.{id(self)}')
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.handler = None

    def state(self, state, message):
        atomic_json(self.settings.state_file, dict(state=state, message=message,
            supervisor_pid=os.getpid(), local_url=self.settings.local_origin,
            public_url=self.settings.public_origin, tunnel_url=self.tunnel_url,
            updated_at=time.time()))

    def child_environment(self):
        # Keep OS networking and Wrangler's standard local credential lookup.
        # The Workbench loads private configuration itself, not into command args.
        return {**os.environ, 'PATH': self.settings.tool_path, 'UV_FROZEN': '1',
                'PYTHONUNBUFFERED': '1', 'WRANGLER_SEND_METRICS': 'false', 'BROWSER': 'false'}

    def spawn(self, name, command, tunnel=False, cwd=None):
        process = self.popen(command, cwd=str(cwd or self.settings.root), env=self.child_environment(),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding='utf-8', errors='replace', bufsize=1, start_new_session=True)
        self.children.append((name, process))
        def read():
            for line in process.stdout:
                if tunnel:
                    match = TUNNEL_URL.search(line)
                    if match:
                        self.tunnel_url = match[0]
                    if 'Registered tunnel connection' in line:
                        self.connected.set()
                self.logger.info('%s: %s', name, redact(line.strip()))
        thread = threading.Thread(target=read, daemon=True, name=f'MediaCrawler-{name}')
        thread.start()
        self.readers.append(thread)
        return process

    def wait_until(self, condition, timeout, message):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                raise InterruptedError('Startup cancelled.')
            for name, child in self.children:
                if child.poll() is not None:
                    raise RuntimeError(f'{name} exited. See Desktop Status and the desktop log.')
            if condition():
                return
            self.stop_event.wait(.2)
        raise RuntimeError(message)

    def start_services(self):
        self.settings.validate()
        for port in (self.settings.local_port, self.settings.remote_port):
            if self.port_check(port):
                raise RuntimeError(f'Port {port} is already in use. Stop the manually started Workbench first.')
        self.state('starting', 'Starting Workbench.')
        self.spawn('Workbench', [self.settings.python, '-m', 'api.serve', '--env-file', str(self.settings.env_file),
                   '--local-port', str(self.settings.local_port), '--remote-port', str(self.settings.remote_port)])
        self.wait_until(lambda: self.probe(self.settings.local_origin + '/api/health') == 200 and
                        self.probe(f'http://127.0.0.1:{self.settings.remote_port}/api/health', urlsplit(self.settings.public_origin).netloc) == 401,
                        45, 'Workbench readiness check failed.')
        self.state('starting', 'Connecting Quick Tunnel.')
        self.spawn('Tunnel', [self.settings.cloudflared, 'tunnel', '--url', f'http://127.0.0.1:{self.settings.remote_port}',
                   '--http-host-header', urlsplit(self.settings.public_origin).netloc], tunnel=True)
        self.wait_until(lambda: bool(self.tunnel_url) and self.connected.is_set(), 90, 'Tunnel did not connect in time.')
        self.state('starting', 'Checking protected Tunnel origin.')
        self.wait_until(lambda: self.probe(self.tunnel_url + '/api/health') == 401, 45, 'Tunnel origin is not reachable or is not protected.')
        self.state('starting', 'Updating Worker upstream. Cloudflare CLI login must already be complete.')
        update = self.spawn('Worker Update', [self.settings.npx, '--yes', WRANGLER, 'deploy', '--keep-vars',
            '--var', f'PUBLIC_ORIGIN:{self.settings.public_origin}', '--var', f'UPSTREAM_ORIGIN:{self.tunnel_url}'],
            cwd=self.settings.root / 'deploy/remote/worker')
        # This child is expected to exit. Other children must remain alive.
        self.children.remove(('Worker Update', update))
        try:
            deadline = time.monotonic() + 120
            while update.poll() is None:
                if self.stop_event.wait(.2):
                    raise InterruptedError('Startup cancelled.')
                if any(p.poll() is not None for _, p in self.children):
                    raise RuntimeError('Workbench or Tunnel exited during Worker update.')
                if time.monotonic() >= deadline:
                    raise RuntimeError('Worker update timed out. Check the connection and run Wrangler login in Terminal.')
            if update.returncode != 0:
                raise RuntimeError('Worker update failed. Run npx wrangler@4.152.0 login in Terminal, then retry. See the desktop log.')
        finally:
            self.terminate(update, graceful=False)
        self.state('ready', 'Workbench and Tunnel are running. Worker upstream updated. Sign in through the browser for remote access.')

    def terminate(self, process, graceful=True):
        if process.poll() is not None:
            return
        # Only Popen children created by this supervisor are signalled. Existing
        # browsers and manually started services are never selected by name/PID.
        try:
            if graceful:
                process.send_signal(signal.SIGTERM)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=5)
            return
        try:
            process.wait(timeout=35 if graceful else 5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

    def shutdown(self):
        # Tunnel first, then Workbench so it can stop its owned collection task.
        for name, process in reversed(self.children):
            try:
                self.terminate(process, graceful=name == 'Workbench')
            except (OSError, subprocess.SubprocessError) as error:
                self.failure = f'{name} did not stop cleanly. Check Desktop Status and the desktop log.'
                self.logger.error('Shutdown: %s', redact(error))
        for thread in self.readers:
            thread.join(timeout=2)

    def run(self):
        directory = self.settings.state_file.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Acquire before touching status: a duplicate process must not overwrite
        # the running instance's status file or stop its children.
        with Lease(directory / 'supervisor.lock'):
            self.handler = RotatingFileHandler(directory / 'desktop.log', maxBytes=2*1024*1024, backupCount=2, encoding='utf-8')
            self.handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            self.logger.addHandler(self.handler)
            try:
                self.start_services()
                while not self.stop_event.wait(1):
                    if any(p.poll() is not None for _, p in self.children):
                        raise RuntimeError('A managed service exited. Both services have been stopped; use MediaCrawler Start to retry.')
            except InterruptedError:
                pass
            except Exception as error:
                self.failure = str(error)
                self.logger.error('Supervisor: %s', redact(error))
            finally:
                self.shutdown()
                self.state('error' if self.failure else 'stopped', self.failure or 'Workbench and Tunnel stopped.')
                self.logger.removeHandler(self.handler)
                self.handler.close()
        return 1 if self.failure else 0
