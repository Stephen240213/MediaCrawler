"""Install and control English-named macOS launcher applications."""
import argparse
import json
import encodings
import platform
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from dotenv import dotenv_values
from api.services.desktop_supervisor import DesktopSettings, DesktopSupervisor, atomic_json, http_status
from api.services.redaction import redact

LABEL = 'com.mediacrawler.desktop'
APP_NAMES = {'start': 'MediaCrawler Start', 'stop': 'MediaCrawler Stop', 'status': 'MediaCrawler Status'}
ROOT = Path(__file__).resolve().parents[1]


def tool_path(extra=''):
    candidates = [extra, '/opt/homebrew/opt/node@22/bin', '/usr/local/opt/node@22/bin',
                  '/opt/homebrew/bin', '/usr/local/bin', str(Path.home()/'.local/bin'), os.environ.get('PATH', '')]
    return os.pathsep.join(p for p in candidates if p)


def preflight_python(python, root):
    command = [python, '-E', '-c', 'import encodings, ssl, dotenv, sys; print(sys.base_prefix)']
    result = subprocess.run(command, cwd=str(root), capture_output=True, text=True, timeout=15)
    if result.returncode:
        path = control_log('preflight')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(redact(result.stderr))
        path.chmod(0o600)
        raise RuntimeError(f'Python startup check failed. Read {path} before reinstalling the launcher.')


def settings_for_install(env_file, local_port, remote_port):
    env_file = Path(env_file).expanduser().resolve()
    values = dotenv_values(env_file) if env_file.is_file() else {}
    path = tool_path(values.get('MC_TOOL_PATH', ''))
    cloudflared, npx = shutil.which('cloudflared', path=path), shutil.which('npx', path=path)
    if not cloudflared or not npx or not shutil.which('uv', path=path) or not shutil.which('node', path=path):
        raise ValueError('Install uv, Node 22 and cloudflared first, or set MC_TOOL_PATH in remote.env.')
    runtime = Path(values.get('MC_RUNTIME_DIR') or ROOT/'.runtime').expanduser()
    if not runtime.is_absolute():
        runtime = ROOT/runtime
    settings = DesktopSettings(ROOT, env_file, runtime.resolve(), str(ROOT/'.venv/bin/python'),
               cloudflared, npx, path, str(values.get('MC_PUBLIC_ORIGIN', '')).rstrip('/'), local_port, remote_port)
    settings.validate()
    preflight_python(settings.python, settings.root)
    return settings


def agent_definition(settings, settings_file):
    directory = settings.state_file.parent
    return {'Label': LABEL, 'ProgramArguments': [settings.python, '-E', '-m', 'api.desktop', 'supervise', '--settings', str(settings_file)],
            'WorkingDirectory': str(settings.root), 'RunAtLoad': False, 'KeepAlive': False,
            'ProcessType': 'Interactive', 'ExitTimeOut': 45, 'ThrottleInterval': 5, 'Umask': 0o077,
            'StandardOutPath': str(directory/'agent.stdout.log'), 'StandardErrorPath': str(directory/'agent.stderr.log'),
            'EnvironmentVariables': {'PATH': settings.tool_path, 'UV_FROZEN': '1'}}


def control_log(action):
    return Path.home()/'Library/Logs/MediaCrawler'/f'{action}.log'


def control_command(action, python, settings_file, root=None, log_file=None):
    """Capture even interpreter bootstrap failures, before our Python can run."""
    root = Path(root or Path(python).parents[2])
    log_file = Path(log_file or control_log(action))
    command = shlex.join([python, '-E', '-m', 'api.desktop', action, '--settings', str(settings_file)])
    # The group redirects cd/import/bootstrap failures too. No failure output is
    # returned to AppleScript's unbounded errorText field.
    return ('umask 077; /bin/mkdir -p ' + shlex.quote(str(log_file.parent)) + ' || exit 1; '
            'if (cd ' + shlex.quote(str(root)) + ' && ' + command + ') > '
            + shlex.quote(str(log_file)) + ' 2>&1; then '
            '/usr/bin/head -n 8 ' + shlex.quote(str(log_file)) + '; else exit 1; fi')


def application_script(action, python, settings_file, root=None):
    root = Path(root or Path(python).parents[2])
    log_file = control_log(action)
    escaped = json.dumps(control_command(action, python, settings_file, root, log_file), ensure_ascii=False)
    title = json.dumps(APP_NAMES[action])
    # Use the known terminal bootstrap for diagnostics. It never starts tasks,
    # registers services, or prints the private environment's values.
    terminal = ('cd ' + shlex.quote(str(root))
                + ' && uv run --frozen python -m api.desktop diagnose --settings '
                + shlex.quote(str(settings_file)))
    present = (f'display dialog resultText buttons {{"Close"}} default button "Close" cancel button "Close" with title {title} giving up after 60'
               if action == 'status' else f'display notification resultText with title {title}')
    message = json.dumps('MediaCrawler could not run. The full error was saved to the control log. Choose Diagnostics to check Python in Terminal. This dialog closes automatically after 45 seconds.')
    open_log = json.dumps(shlex.join(['/usr/bin/open', '-t', str(log_file)]), ensure_ascii=False)
    return f'''on run
    try
        with timeout of 420 seconds
            set resultText to do shell script {escaped}
        end timeout
        if (length of resultText) > 700 then set resultText to (text 1 thru 700 of resultText) & "..."
        {present}
    on error errorText number errorNumber
        if errorNumber is -128 then return
        try
            set choice to display dialog {message} buttons {{"Close", "Open Log", "Diagnostics"}} default button "Close" cancel button "Close" with title {title} with icon stop giving up after 45
            if gave up of choice then return
            if button returned of choice is "Open Log" then
                do shell script {open_log}
            else if button returned of choice is "Diagnostics" then
                tell application "Terminal"
                    activate
                    do script {json.dumps(terminal, ensure_ascii=False)}
                end tell
            end if
        on error
            return
        end try
    end try
end run
'''


def target():
    return f'gui/{os.getuid()}/{LABEL}'


def launchctl(*args, check=False):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, check=check)


def agent_pid():
    result = launchctl('print', target())
    if result.returncode:
        return None
    match = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.MULTILINE)
    return int(match[1]) if match else None


def read_state(settings):
    try:
        return json.loads(settings.state_file.read_text())
    except (OSError, ValueError):
        return {'state': 'stopped', 'message': 'No launcher status is available yet.'}


def start(settings, timeout=300):
    requested_at = time.time()
    pid = agent_pid()
    if not pid:
        result = launchctl('kickstart', target())  # Never use -k to kill a running job.
        if result.returncode:
            raise RuntimeError('Launcher is not registered. Run python -m api.desktop install again.')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = agent_pid()
        state = read_state(settings)
        if pid and state.get('supervisor_pid') == pid and state.get('state') == 'ready' and http_status(settings.local_origin+'/api/health') == 200:
            subprocess.run(['/usr/bin/open', settings.local_origin], check=True)
            return 'Workbench and Tunnel are running. The local Workbench is open.'
        fresh = (pid is not None and state.get('supervisor_pid') == pid) or state.get('updated_at', 0) >= requested_at
        if state.get('state') == 'error' and fresh:
            raise RuntimeError(state.get('message', 'Startup failed.'))
        # Allow launchd time to create the process before checking its exit.
        if not pid and time.monotonic() > deadline-timeout+3:
            raise RuntimeError(state.get('message', 'The launcher exited. Check Desktop Status.'))
        time.sleep(.5)
    raise RuntimeError('Startup is still in progress. Open MediaCrawler Status for details; click Stop to cancel.')


def stop(settings):
    if not agent_pid():
        return 'Workbench and Tunnel are already stopped.'
    result = launchctl('kill', 'SIGTERM', target())
    if result.returncode:
        raise RuntimeError('Unable to stop the managed launcher. Check Desktop Status.')
    deadline = time.monotonic()+55
    while time.monotonic()<deadline:
        if not agent_pid():
            return 'Workbench and Tunnel stopped. Saved data was preserved.'
        time.sleep(.5)
    raise RuntimeError('Shutdown is taking longer than expected. Check Desktop Status; no unrelated process was stopped.')


def status(settings):
    state = read_state(settings)
    running = agent_pid()
    active = state.get('supervisor_pid') == running and running is not None
    current = state.get('state', 'unknown') if active else ('error' if state.get('state') == 'error' else 'stopped')
    return '\n'.join([f'State: {current}', str(state.get('message', '')), f'Local: {settings.local_origin}',
                      f'Remote: {settings.public_origin}', f'Logs: {settings.state_file.parent}',
                      'Remote access requires browser sign-in. The Mac must remain awake and signed in.'])


def diagnose(settings):
    lines = [f'Platform: {platform.system()}', f'Python: {sys.executable}',
             f'Base runtime: {sys.base_prefix}', f'Encodings: {encodings.__file__}',
             f'Encodings readable in this process: {os.access(encodings.__file__, os.R_OK)}',
             f'Project: {settings.root}',
             f'Project inside Documents: {settings.root.is_relative_to(Path.home()/"Documents")}',
             f'Finder control log: {control_log("start")}',
             'Note: a successful Terminal check does not establish Finder/LaunchAgent folder access.']
    for name, path in [('Finder Start', control_log('start')), ('Finder Status', control_log('status')), ('Finder Stop', control_log('stop')), ('Agent stderr', settings.state_file.parent/'agent.stderr.log')]:
        if path.is_file():
            with path.open('rb') as file:
                file.seek(0, os.SEEK_END)
                file.seek(max(0, file.tell()-6000))
                tail = file.read().decode('utf-8', errors='replace')
            lines.extend([f'{name} recent output:', redact(tail)[-1800:]])
    return '\n'.join(lines)


def install(settings, applications=None, agents=None, *, compile_app=None, sign_app=None, register=True, reveal=True):
    applications = Path(applications or Path.home()/'Applications/MediaCrawler')
    agents = Path(agents or Path.home()/'Library/LaunchAgents')
    marker = applications/'.mediacrawler-launcher.json'
    agent_file = agents/f'{LABEL}.plist'
    settings_file = settings.state_file.parent/'settings.json'
    if applications.exists() and any(applications.iterdir()):
        try:
            previous = json.loads(marker.read_text())
            if previous.get('project_root') != str(settings.root):
                raise ValueError()
        except (OSError, ValueError):
            raise ValueError('Applications folder is not owned by this installer; existing files were preserved.') from None
    if agent_file.exists():
        with agent_file.open('rb') as file:
            old = plistlib.load(file)
        arguments = old.get('ProgramArguments', [])
        owned = (arguments[:4] == [settings.python, '-m', 'api.desktop', 'supervise'] or
                 arguments[:5] == [settings.python, '-E', '-m', 'api.desktop', 'supervise'])
        if old.get('Label') != LABEL or not owned:
            raise ValueError('A different LaunchAgent already uses this name; it was preserved.')
    if register and agent_pid():
        raise RuntimeError('Stop MediaCrawler with MediaCrawler Stop before updating its launchers.')
    applications.mkdir(parents=True, exist_ok=True, mode=0o700)
    agents.mkdir(parents=True, exist_ok=True)
    settings.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    def native_compile(source, destination):
        try:
            subprocess.run(['/usr/bin/osacompile', '-l', 'AppleScript', '-o', str(destination)], input=source, text=True, check=True, capture_output=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError('macOS application compilation failed: ' + redact(error.stderr)[:1200]) from None
    def native_sign(destination):
        try:
            # Info.plist changes invalidate osacompile's original signature.
            # Sign our generated local app, then verify instead of disabling checks.
            subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(destination)], check=True, capture_output=True, text=True)
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError('macOS application signing failed: ' + redact(error.stderr)[:1200]) from None
    compile_app = compile_app or native_compile
    sign_app = sign_app or native_sign
    # Compile everything before replacing any installed app or configuration.
    with tempfile.TemporaryDirectory(prefix='.build-', dir=applications) as temporary:
        built = Path(temporary)
        for action, name in APP_NAMES.items():
            destination = built/f'{name}.app'
            compile_app(application_script(action, settings.python, settings_file, settings.root), destination)
            info_file = destination/'Contents/Info.plist'
            with info_file.open('rb') as file:
                info = plistlib.load(file)
            info.update(CFBundleName=name, CFBundleDisplayName=name, CFBundleIdentifier=LABEL+'.'+action)
            with info_file.open('wb') as file:
                plistlib.dump(info, file)
            sign_app(destination)
        if register:
            launchctl('bootout', target())  # Unload only our stopped, validated agent.
        backup = applications/'.backups'/str(time.time_ns())
        backup.mkdir(parents=True, mode=0o700)
        for name in APP_NAMES.values():
            existing = applications/f'{name}.app'
            if existing.exists():
                existing.rename(backup/existing.name)
            (built/f'{name}.app').rename(existing)
        if agent_file.exists():
            shutil.copy2(agent_file, backup/agent_file.name)
        if settings_file.exists():
            shutil.copy2(settings_file, backup/'settings.json')
        settings.save(settings_file)
        with agent_file.open('wb') as file:
            plistlib.dump(agent_definition(settings, settings_file), file)
        agent_file.chmod(0o600)
        atomic_json(marker, {'project_root': str(settings.root), 'schema': 1})
        if register:
            result = launchctl('bootstrap', f'gui/{os.getuid()}', str(agent_file))
            if result.returncode:
                raise RuntimeError('LaunchAgent registration failed. Compiled applications were preserved; check macOS Background Items permissions.')
    if reveal:
        subprocess.run(['/usr/bin/open', str(applications)], check=True)
    return applications


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'start', 'stop', 'status', 'supervise', 'diagnose'])
    parser.add_argument('--settings', type=Path, default=ROOT/'.runtime/desktop/settings.json')
    parser.add_argument('--env-file', type=Path, default=Path.home()/'.config/mediacrawler/remote.env')
    parser.add_argument('--local-port', type=int, default=8090)
    parser.add_argument('--remote-port', type=int, default=8081)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action == 'install':
            if sys.platform != 'darwin':
                raise RuntimeError('Install this launcher on the collection Mac, not in the cloud workspace.')
            settings = settings_for_install(args.env_file, args.local_port, args.remote_port)
            folder = install(settings)
            print(f'Installed MediaCrawler Start, MediaCrawler Stop and MediaCrawler Status in {folder}.')
            return
        settings = DesktopSettings.load(args.settings)
        if args.action == 'diagnose':
            print(diagnose(settings))
            return
        if args.action == 'supervise':
            supervisor = DesktopSupervisor(settings)
            signal.signal(signal.SIGTERM, lambda *_: supervisor.stop_event.set())
            signal.signal(signal.SIGINT, lambda *_: supervisor.stop_event.set())
            raise SystemExit(supervisor.run())
        if sys.platform != 'darwin':
            raise RuntimeError('LaunchAgent controls are available only on the collection Mac.')
        print({'start': start, 'stop': stop, 'status': status}[args.action](settings))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
