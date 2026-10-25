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
from api.services.desktop_supervisor import DesktopSettings, DesktopSupervisor, TunnelSupervisor, atomic_json


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
        assert '-E' in script
        assert 'giving up after 45' in script
        assert 'if errorNumber is -128 then return' in script
        assert 'display dialog errorText' not in script
        assert 'launchctl' not in script  # actions remain in the reviewed controller


def compiler(source,destination):
    (destination/'Contents').mkdir(parents=True)
    with (destination/'Contents/Info.plist').open('wb') as file:plistlib.dump({},file)
    (destination/'source.txt').write_text(source)


def test_installer_builds_backs_up_and_preserves_unknown_files(settings,tmp_path):
    apps=tmp_path/'Applications';agents=tmp_path/'Agents'
    desktop.install(settings,apps,agents,compile_app=compiler,sign_app=lambda _:None,register=False,reveal=False)
    for name in desktop.APP_NAMES.values():
        with (apps/f'{name}.app/Contents/Info.plist').open('rb') as file:
            assert plistlib.load(file)['CFBundleDisplayName']==name
    agent_path=agents/f'{desktop.LABEL}.plist'
    assert agent_path.exists()
    # Simulate the already-installed release before the -E/bootstrap fix.
    with agent_path.open('rb') as file:old_agent=plistlib.load(file)
    old_agent['ProgramArguments'].remove('-E')
    with agent_path.open('wb') as file:plistlib.dump(old_agent,file)
    desktop.install(settings,apps,agents,compile_app=compiler,sign_app=lambda _:None,register=False,reveal=False)
    with agent_path.open('rb') as file:assert '-E' in plistlib.load(file)['ProgramArguments']
    assert list((apps/'.backups').glob('*/MediaCrawler Start.app'))
    foreign=tmp_path/'Other apps';foreign.mkdir();(foreign/'notes.txt').write_text('preserve')
    with pytest.raises(ValueError):desktop.install(settings,foreign,agents,compile_app=compiler,sign_app=lambda _:None,register=False,reveal=False)
    assert (foreign/'notes.txt').read_text()=='preserve'


def test_compile_failure_keeps_installed_apps(settings,tmp_path):
    apps=tmp_path/'Apps';agents=tmp_path/'Agents'
    desktop.install(settings,apps,agents,compile_app=compiler,sign_app=lambda _:None,register=False,reveal=False)
    original=(apps/'MediaCrawler Start.app/source.txt').read_text()
    def fail(*_):raise RuntimeError('compiler fixture failure')
    with pytest.raises(RuntimeError):desktop.install(settings,apps,agents,compile_app=fail,sign_app=lambda _:None,register=False,reveal=False)
    assert (apps/'MediaCrawler Start.app/source.txt').read_text()==original


def test_foreign_agent_preserved(settings,tmp_path):
    agents=tmp_path/'Agents';agents.mkdir()
    path=agents/f'{desktop.LABEL}.plist'
    with path.open('wb') as file:plistlib.dump({'Label':desktop.LABEL,'ProgramArguments':['other-program']},file)
    before=path.read_bytes()
    with pytest.raises(ValueError):desktop.install(settings,tmp_path/'Apps',agents,compile_app=compiler,sign_app=lambda _:None,register=False,reveal=False)
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
    supervisor=TunnelSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory(calls))
    supervisor.start_services()
    state=json.loads(settings.tunnel_state_file.read_text())
    assert state['state']=='ready'
    assert len(calls)==2
    assert 'http://127.0.0.1:8081' in calls[0][0]
    assert 'workbench.example.workers.dev' in calls[0][0]
    assert 'UPSTREAM_ORIGIN:https://fixture-tunnel.trycloudflare.com' in calls[1][0]
    assert '--keep-vars' in calls[1][0]
    assert not any('api.serve' in command for command,_ in calls)
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
    supervisor=TunnelSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory(calls,update_code=1))
    assert supervisor.run()==1
    assert all(p.poll() is not None for _,p in supervisor.children)
    assert json.loads(settings.tunnel_state_file.read_text())['state']=='error'


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
    supervisor=TunnelSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory(calls,update_code=None))
    original=supervisor.state
    def state(value,message):
        original(value,message)
        if message.startswith('Updating Worker'):supervisor.stop_event.set()
    supervisor.state=state
    assert supervisor.run()==0
    assert len(calls)==2
    assert all(process.poll() is not None for process in FakeProcess.registry.values())
    assert json.loads(settings.tunnel_state_file.read_text())['state']=='stopped'


def test_real_workbench_independent_lifecycle(settings,tmp_path):
    """Actual managed API starts without a Tunnel or Cloudflare call."""
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
        if 'api.serve' in command:
            options['cwd']=str(Path(__file__).resolve().parents[1])
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
        assert not record.exists()
        assert len(supervisor.children)==1 and supervisor.children[0][0]=='Workbench'
        assert not (tmp_path/'collection.db').exists()
    finally:
        supervisor.stop_event.set();thread.join(timeout=45)
        assert not thread.is_alive()
    assert all(p.poll() is not None for _,p in supervisor.children)
    assert json.loads(settings.state_file.read_text())['state']=='stopped'


def test_bootstrap_crash_is_logged_without_becoming_dialog_text(tmp_path):
    root=tmp_path/'Project folder';root.mkdir()
    broken=tmp_path/'broken-python'
    broken.write_text('#!/bin/sh\nhead -c 20000 /dev/zero | tr "\\000" X >&2\nexit 1\n')
    broken.chmod(0o700)
    log=tmp_path/'Library Logs/start.log'
    command=desktop.control_command('start',str(broken),root/'settings.json',root,log)
    result=subprocess.run(['/bin/sh','-c',command],capture_output=True,text=True)
    assert result.returncode!=0
    assert not result.stdout and not result.stderr
    assert log.stat().st_size==20000
    assert log.stat().st_mode&0o777==0o600
    script=desktop.application_script('start',str(broken),root/'settings.json',root)
    assert 'display dialog errorText' not in script
    assert 'buttons {"Close", "Open Log", "Diagnostics"}' in script
    assert 'cancel button "Close"' in script


def test_gui_python_ignores_inherited_pythonhome(tmp_path):
    root=tmp_path/'Project folder';(root/'api').mkdir(parents=True)
    (root/'api/__init__.py').write_text('')
    (root/'api/desktop.py').write_text('import encodings; print("runtime ok")')
    command=desktop.control_command('status',sys.executable,root/'settings.json',root,tmp_path/'status.log')
    result=subprocess.run(['/bin/sh','-c',command],env={**os.environ,'PYTHONHOME':str(tmp_path/'missing-runtime'),'PYTHONPATH':str(tmp_path/'missing-code')},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='runtime ok'


def test_preflight_failure_preserves_config_and_caps_error(tmp_path,monkeypatch):
    monkeypatch.setattr(desktop,'control_log',lambda action:tmp_path/f'{action}.log')
    monkeypatch.setattr(desktop.subprocess,'run',lambda *_,**__:subprocess.CompletedProcess([],1,'','Python bootstrap failed\nCookie: session=secret'))
    with pytest.raises(RuntimeError):desktop.preflight_python('/fixture/python',tmp_path)
    assert 'session=secret' not in (tmp_path/'preflight.log').read_text()


def test_app_signing_happens_after_metadata_and_before_install(settings,tmp_path):
    signed=[]
    def sign(path):
        with (path/'Contents/Info.plist').open('rb') as file:
            info=plistlib.load(file)
        assert info['CFBundleIdentifier'].startswith(desktop.LABEL)
        signed.append(info['CFBundleName'])
    desktop.install(settings,tmp_path/'Apps',tmp_path/'Agents',compile_app=compiler,sign_app=sign,register=False,reveal=False)
    assert signed==list(desktop.APP_NAMES.values())


def test_diagnostics_do_not_dump_private_environment(settings,monkeypatch,tmp_path):
    monkeypatch.setattr(desktop,'control_log',lambda action:tmp_path/f'{action}.log')
    (tmp_path/'start.log').write_text('Cookie: session=secret\nPython path problem')
    output=desktop.diagnose(settings)
    assert 'Python:' in output and 'Encodings:' in output
    assert 'session=secret' not in output
    assert 'fixture-audience' not in output
    assert 'owner@example.test' not in output


def test_workbench_start_never_probes_cloudflare(settings):
    calls=[];urls=[]
    def offline_probe(url,host=None):
        urls.append(url)
        assert url.startswith('http://127.0.0.1:')
        return probe(url,host)
    supervisor=DesktopSupervisor(settings,probe=offline_probe,port_check=lambda _:False,popen=factory(calls))
    supervisor.start_services()
    assert len(calls)==1 and 'api.serve' in calls[0][0]
    assert json.loads(settings.state_file.read_text())['state']=='ready'
    assert not settings.tunnel_state_file.exists()
    supervisor.shutdown()


def test_tunnel_failure_preserves_workbench(settings):
    wb_calls=[];tunnel_calls=[]
    workbench=DesktopSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory(wb_calls))
    workbench.start_services()
    def unreachable(url,host=None):
        return None if url.startswith('https://') else probe(url,host)
    tunnel=TunnelSupervisor(settings,probe=unreachable,port_check=lambda _:False,popen=factory(tunnel_calls))
    original_wait=tunnel.wait_until
    tunnel.wait_until=lambda condition,timeout,message:original_wait(condition,min(timeout,.3),message)
    try:
        assert tunnel.run()==1
        assert all(child.poll() is None for _,child in workbench.children)
        assert json.loads(settings.state_file.read_text())['state']=='ready'
        assert json.loads(settings.tunnel_state_file.read_text())['state']=='error'
        assert 'network/DNS/TLS unavailable' in (settings.state_file.parent/'tunnel.log').read_text()
        assert len(tunnel_calls)==1 and 'tunnel' in tunnel_calls[0][0]
    finally:
        workbench.shutdown()


def test_tunnel_requires_existing_protected_workbench(settings):
    calls=[]
    tunnel=TunnelSupervisor(settings,probe=lambda *_:None,popen=factory(calls))
    assert tunnel.run()==1
    assert not calls
    assert 'Open MediaCrawler Start first' in json.loads(settings.tunnel_state_file.read_text())['message']


def test_tunnel_stop_uses_only_its_agent(settings,monkeypatch):
    pids=iter([999,None]);components=[]
    def pid(component='workbench'):
        components.append(component);return next(pids)
    monkeypatch.setattr(desktop,'agent_pid',pid)
    calls=[]
    def ctl(*args):
        calls.append(args);return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr(desktop,'launchctl',ctl)
    assert 'Tunnel stopped' in desktop.stop(settings,'tunnel')
    assert components==['tunnel','tunnel']
    assert calls==[('kill','SIGTERM',desktop.target('tunnel'))]
    assert desktop.target('tunnel')!=desktop.target()


def test_installer_creates_two_independent_agents(settings,tmp_path):
    apps=tmp_path/'Apps';agents=tmp_path/'Agents'
    desktop.install(settings,apps,agents,compile_app=compiler,sign_app=lambda _:None,register=False,reveal=False)
    with (agents/(desktop.LABEL+'.plist')).open('rb') as file:workbench=plistlib.load(file)
    with (agents/(desktop.TUNNEL_LABEL+'.plist')).open('rb') as file:tunnel=plistlib.load(file)
    assert 'supervise' in workbench['ProgramArguments']
    assert 'tunnel-supervise' in tunnel['ProgramArguments']
    assert workbench['Label']!=tunnel['Label']
    assert workbench['StandardErrorPath']!=tunnel['StandardErrorPath']
    assert len(list(apps.glob('*.app')))==6


def test_foreign_tunnel_agent_is_preserved(settings,tmp_path):
    agents=tmp_path/'Agents';agents.mkdir()
    path=agents/(desktop.TUNNEL_LABEL+'.plist')
    with path.open('wb') as file:plistlib.dump({'Label':desktop.TUNNEL_LABEL,'ProgramArguments':['foreign']},file)
    before=path.read_bytes()
    with pytest.raises(ValueError):
        desktop.install(settings,tmp_path/'Apps',agents,compile_app=compiler,sign_app=lambda _:None,register=False,reveal=False)
    assert path.read_bytes()==before


def test_stale_status_does_not_report_old_ready_message(settings,monkeypatch):
    atomic_json(settings.state_file,{'state':'ready','message':'Workbench and Tunnel are running','supervisor_pid':123})
    monkeypatch.setattr(desktop,'agent_pid',lambda:None)
    output=desktop.status(settings)
    assert 'State: stopped' in output and 'Workbench and Tunnel are running' not in output


def test_workbench_does_not_require_tunnel_executables(settings):
    from dataclasses import replace
    settings=replace(settings,cloudflared='/missing/cloudflared',npx='/missing/npx')
    calls=[]
    supervisor=DesktopSupervisor(settings,probe=probe,port_check=lambda _:False,popen=factory(calls))
    supervisor.start_services()
    assert len(calls)==1
    supervisor.shutdown()


def test_tunnel_start_is_idempotent_and_opens_only_remote(settings,monkeypatch):
    atomic_json(settings.tunnel_state_file,{'state':'ready','supervisor_pid':456})
    monkeypatch.setattr(desktop,'agent_pid',lambda component='workbench':456 if component=='tunnel' else pytest.fail('must not control Workbench'))
    monkeypatch.setattr(desktop,'http_status',lambda url,*args:401 if ':8081/' in url else 200)
    monkeypatch.setattr(desktop,'launchctl',lambda *_:pytest.fail('must not restart running Tunnel'))
    opened=[];monkeypatch.setattr(desktop.subprocess,'run',lambda args,**_:opened.append(args))
    assert 'Tunnel is running' in desktop.start(settings,component='tunnel')
    assert opened==[['/usr/bin/open',settings.public_origin]]
