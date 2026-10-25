"""Launcher verification uses temporary files and harmless owned processes.

No launchctl registration, macOS apps, real tunnels, Cloudflare account calls or
platform collection are performed by these cloud tests.
"""
import io
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import threading
import time

import pytest

from api import desktop
from api.services.desktop_supervisor import DesktopSettings, DesktopSupervisor, atomic_json


@pytest.fixture
def settings(tmp_path):
    root = tmp_path/'Project with spaces'
    (root/'api/webui').mkdir(parents=True)
    (root/'api/webui/index.html').write_text('<html>fixture</html>')
    (root/'deploy/remote/worker').mkdir(parents=True)
    env = root/'private.env'
    env.write_text('MC_ACCESS_TEAM=test-team\nMC_ACCESS_AUD=fixture-audience\nMC_OPERATORS=owner@example.test\n')
    return DesktopSettings(root, env, root/'.runtime', sys.executable, sys.executable, sys.executable,
                           os.environ.get('PATH',''), 'https://workbench.example.workers.dev')


def test_configuration_private_and_validated(settings,tmp_path):
    settings.validate()
    path = tmp_path/'settings.json';settings.save(path)
    assert DesktopSettings.load(path)==settings
    assert path.stat().st_mode&0o777==0o600
    assert 'fixture-audience' not in path.read_text()
    assert 'owner@example.test' not in path.read_text()
    from dataclasses import replace
    for changes in [{'public_origin':'http://evil.test'},{'public_origin':'https://workbench.example.workers.dev/path'}, {'remote_port':8090}, {'cloudflared':'/missing/tool'}]:
        with pytest.raises(ValueError):replace(settings,**changes).validate()


def test_agent_and_english_applications(settings):
    agent=desktop.agent_definition(settings, settings.state_file.parent/'settings.json')
    assert agent['RunAtLoad'] is False and agent['KeepAlive'] is False
    assert agent['WorkingDirectory']==str(settings.root)
    assert agent['EnvironmentVariables']=={'PATH':settings.tool_path,'UV_FROZEN':'1'}
    assert not any('AUD' in k or 'TOKEN' in k for k in agent['EnvironmentVariables'])
    for action,name in desktop.APP_NAMES.items():
        assert name.isascii() and name.startswith('MediaCrawler ')
        script=desktop.application_script(action,settings.python,settings.state_file,settings.root)
        assert 'cd ' in script and str(settings.root) in script
        assert '-m api.desktop' in script
        assert 'launchctl' not in script  # actions remain in the reviewed controller


def compiler(source,destination):
    (destination/'Contents').mkdir(parents=True)
    with (destination/'Contents/Info.plist').open('wb') as file:plistlib.dump({},file)
    (destination/'source.txt').write_text(source)


def test_installer_builds_backs_up_and_preserves_unknown_files(settings,tmp_path):
    apps=tmp_path/'Applications';agents=tmp_path/'Agents'
    desktop.install(settings,apps,agents,compile_app=compiler,register=False,reveal=False)
    for name in desktop.APP_NAMES.values():
        with (apps/f'{name}.app/Contents/Info.plist').open('rb') as file:
            assert plistlib.load(file)['CFBundleDisplayName']==name
    assert (agents/f'{desktop.LABEL}.plist').exists()
    desktop.install(settings,apps,agents,compile_app=compiler,register=False,reveal=False)
    assert list((apps/'.backups').glob('*/MediaCrawler Start.app'))
    foreign=tmp_path/'Other apps';foreign.mkdir();(foreign/'notes.txt').write_text('preserve')
    with pytest.raises(ValueError):desktop.install(settings,foreign,agents,compile_app=compiler,register=False,reveal=False)
    assert (foreign/'notes.txt').read_text()=='preserve'


def test_compile_failure_keeps_installed_apps(settings,tmp_path):
    apps=tmp_path/'Apps';agents=tmp_path/'Agents'
    desktop.install(settings,apps,agents,compile_app=compiler,register=False,reveal=False)
    original=(apps/'MediaCrawler Start.app/source.txt').read_text()
    def fail(*_):raise RuntimeError('compiler fixture failure')
    with pytest.raises(RuntimeError):desktop.install(settings,apps,agents,compile_app=fail,register=False,reveal=False)
    assert (apps/'MediaCrawler Start.app/source.txt').read_text()==original


def test_foreign_agent_preserved(settings,tmp_path):
    agents=tmp_path/'Agents';agents.mkdir()
    path=agents/f'{desktop.LABEL}.plist'
    with path.open('wb') as file:plistlib.dump({'Label':desktop.LABEL,'ProgramArguments':['other-program']},file)
    before=path.read_bytes()
    with pytest.raises(ValueError):desktop.install(settings,tmp_path/'Apps',agents,compile_app=compiler,register=False,reveal=False)
    assert path.read_bytes()==before


class FakeProcess:
    registry={}
    next_pid=300000
    def __init__(self, lines='', returncode=None):
        self.stdout=io.StringIO(lines);self.returncode=returncode
        FakeProcess.next_pid+=1;self.pid=FakeProcess.next_pid
        FakeProcess.registry[self.pid]=self
        self.signals=[]
    def poll(self):return self.returncode
    def wait(self,timeout=None):
        self.returncode=0;return 0
    def send_signal(self,value):self.signals.append(value);self.returncode=0


@pytest.fixture(autouse=True)
def fake_process_signals(monkeypatch):
    original=os.killpg
    def owned_signal(pid,value):
        if pid in FakeProcess.registry:
            FakeProcess.registry[pid].send_signal(value)
        else:
            original(pid,value)
    monkeypatch.setattr(os,'killpg',owned_signal)
    yield
    FakeProcess.registry.clear()


def factory(calls, update_code=0, tunnel=True):
    def spawn(command,**options):
        calls.append((command,options))
        if 'tunnel' in command:
            return FakeProcess('https://fixture-tunnel.trycloudflare.com\nRegistered tunnel connection\n' if tunnel else '')
        if 'deploy' in command:return FakeProcess('Worker updated\n',update_code)
        return FakeProcess('Workbench ready\n')
    return spawn


def probe(url,host=None):
    return 200 if '127.0.0.1:8090' in url else 401


def test_supervisor_readiness_fixed_commands_and_automatic_upstream(settings):
    calls=[]
    supervisor=DesktopSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory(calls))
    supervisor.start_services()
    state=json.loads(settings.state_file.read_text())
    assert state['state']=='ready'
    assert len(calls)==3
    assert '--local-port' in calls[0][0] and '8090' in calls[0][0]
    assert 'http://127.0.0.1:8081' in calls[1][0]
    assert 'workbench.example.workers.dev' in calls[1][0]
    assert 'UPSTREAM_ORIGIN:https://fixture-tunnel.trycloudflare.com' in calls[2][0]
    assert '--keep-vars' in calls[2][0]
    assert all(options.get('shell') is not True for _,options in calls)
    supervisor.shutdown()
    assert all(p.poll() is not None for _,p in supervisor.children)


def test_conflict_does_not_stop_manual_service(settings):
    calls=[]
    supervisor=DesktopSupervisor(settings,probe=probe,port_check=lambda _:True,popen=factory(calls))
    assert supervisor.run()==1
    assert not calls
    assert 'already in use' in json.loads(settings.state_file.read_text())['message']


def test_worker_update_failure_stops_only_created_services(settings):
    calls=[]
    supervisor=DesktopSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory(calls,update_code=1))
    assert supervisor.run()==1
    assert all(p.poll() is not None for _,p in supervisor.children)
    assert json.loads(settings.state_file.read_text())['state']=='error'


def test_protection_check_must_be_401(settings):
    calls=[]
    supervisor=DesktopSupervisor(settings,probe=lambda *_:200,port_check=lambda _:False,popen=factory(calls))
    # Shorten waits without changing the tested protection predicate.
    def wait(condition,timeout,message):
        if not condition():raise RuntimeError(message)
    supervisor.wait_until=wait
    assert supervisor.run()==1
    assert len(calls)==1
    assert supervisor.children[0][1].poll() is not None


def test_duplicate_supervisor_preserves_current_status(settings):
    from api.services.lease import Lease
    atomic_json(settings.state_file,{'state':'ready','supervisor_pid':123})
    with Lease(settings.state_file.parent/'supervisor.lock'):
        duplicate=DesktopSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory([]))
        with pytest.raises(RuntimeError):duplicate.run()
    assert json.loads(settings.state_file.read_text())['supervisor_pid']==123


def test_running_start_is_idempotent_and_never_kills(settings,monkeypatch):
    atomic_json(settings.state_file,{'state':'ready','supervisor_pid':123})
    monkeypatch.setattr(desktop,'agent_pid',lambda:123)
    monkeypatch.setattr(desktop,'http_status',lambda _:200)
    monkeypatch.setattr(desktop,'launchctl',lambda *_:pytest.fail('kickstart must not be called for a running instance'))
    opened=[];monkeypatch.setattr(desktop.subprocess,'run',lambda args,**_:opened.append(args))
    assert 'running' in desktop.start(settings)
    assert opened==[['/usr/bin/open',settings.local_origin]]


def test_stop_without_job_never_signals_unknown_process(settings,monkeypatch):
    monkeypatch.setattr(desktop,'agent_pid',lambda:None)
    monkeypatch.setattr(desktop,'launchctl',lambda *_:pytest.fail('no owned agent'))
    assert 'already stopped' in desktop.stop(settings)


def test_stop_uses_launchd_service_identity(settings,monkeypatch):
    pids=iter([123,None])
    monkeypatch.setattr(desktop,'agent_pid',lambda:next(pids))
    calls=[]
    def ctl(*args):
        calls.append(args);return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr(desktop,'launchctl',ctl)
    assert 'stopped' in desktop.stop(settings)
    assert calls==[('kill','SIGTERM',desktop.target())]


def test_status_detects_stale_file(settings,monkeypatch):
    atomic_json(settings.state_file,{'state':'ready','message':'old','supervisor_pid':123})
    monkeypatch.setattr(desktop,'agent_pid',lambda:None)
    assert 'State: stopped' in desktop.status(settings)


def test_real_child_shutdown_and_signal_ownership(settings,tmp_path):
    # A real harmless process proves that stop targets owned Popen instances.
    owned=subprocess.Popen([sys.executable,'-u','-c','import time;print("ready",flush=True);time.sleep(30)'],stdout=subprocess.PIPE,text=True,start_new_session=True)
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
    supervisor=DesktopSupervisor(settings)
    supervisor.children=[('Workbench',owned)]
    try:
        assert owned.stdout.readline().strip()=='ready'
        supervisor.shutdown()
        assert owned.poll() is not None
        assert unrelated.poll() is None
    finally:
        unrelated.terminate();unrelated.wait(timeout=5)
        if owned.poll() is None:owned.kill();owned.wait(timeout=5)


def test_cancel_during_worker_update_cleans_every_owned_process(settings):
    calls=[]
    supervisor=DesktopSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory(calls,update_code=None))
    original=supervisor.state
    def state(value,message):
        original(value,message)
        if message.startswith('Updating Worker'):supervisor.stop_event.set()
    supervisor.state=state
    assert supervisor.run()==0
    assert len(calls)==3
    assert all(process.poll() is not None for process in FakeProcess.registry.values())
    assert json.loads(settings.state_file.read_text())['state']=='stopped'


def test_real_workbench_paired_lifecycle(settings,tmp_path):
    """Actual managed API, stub Tunnel/Worker; no cloud account or collection."""
    from dataclasses import replace
    import socket
    from api.services.desktop_supervisor import http_status
    def port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
    first,second=port(),port()
    while second==first:second=port()
    settings=replace(settings,local_port=first,remote_port=second)
    with settings.env_file.open('a') as file:
        file.write(f'MC_PUBLIC_ORIGIN={settings.public_origin}\nMC_RUNTIME_DIR={settings.runtime}\nSQLITE_DB_PATH={tmp_path}/collection.db\n')
    record=tmp_path/'worker-update.json'
    def spawn(command,**options):
        if 'tunnel' in command:
            command=[sys.executable,'-u','-c','import time;print("https://fixture-tunnel.trycloudflare.com",flush=True);print("Registered tunnel connection",flush=True);time.sleep(60)']
        elif 'deploy' in command:
            command=[sys.executable,'-u','-c',f'from pathlib import Path;Path({str(record)!r}).write_text({json.dumps(command)!r})']
        environment=options['env'].copy();environment['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        options['env']=environment
        return subprocess.Popen(command,**options)
    def readiness(url,host=None):
        if url.startswith('https://fixture-tunnel.trycloudflare.com'):
            return 401  # The only mocked network path is the external tunnel.
        return http_status(url,host)
    supervisor=DesktopSupervisor(settings,probe=readiness,port_check=lambda _:False,popen=spawn)
    thread=threading.Thread(target=supervisor.run);thread.start()
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if settings.state_file.exists():
                state=json.loads(settings.state_file.read_text())
                if state['state'] in ('ready','error'):break
            time.sleep(.1)
        assert state['state']=='ready',state
        assert http_status(settings.local_origin+'/api/health')==200
        assert http_status(f'http://127.0.0.1:{second}/api/health','workbench.example.workers.dev')==401
        assert 'UPSTREAM_ORIGIN:https://fixture-tunnel.trycloudflare.com' in json.loads(record.read_text())
        assert not (tmp_path/'collection.db').exists()
    finally:
        supervisor.stop_event.set();thread.join(timeout=45)
        assert not thread.is_alive()
    assert all(p.poll() is not None for _,p in supervisor.children)
    assert json.loads(settings.state_file.read_text())['state']=='stopped'
