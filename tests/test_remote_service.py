"""Actual dual-listener startup and singleton ownership, on loopback only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def test_dual_service_shares_process_and_fails_closed(tmp_path):
    local,remote=unused_port(),unused_port()
    while remote==local:remote=unused_port()
    environment={**os.environ,'MC_ACCESS_TEAM':'test-team','MC_ACCESS_AUD':'fixture-audience','MC_PUBLIC_ORIGIN':'https://crawler.test','MC_OPERATORS':'operator@test.invalid','MC_VIEWERS':'','MC_RUNTIME_DIR':str(tmp_path/'runtime'),'SQLITE_DB_PATH':str(tmp_path/'collection.db')}
    command=[sys.executable,'-m','api.serve','--local-port',str(local),'--remote-port',str(remote)]
    with (tmp_path/'service.log').open('w') as log:
        process=subprocess.Popen(command,env=environment,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if process.poll() is not None:raise AssertionError((tmp_path/'service.log').read_text())
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{local}/api/session',timeout=1) as response:
                        session=json.load(response)
                        assert session['mode']=='local' and session['csrf_token']
                        break
                except OSError:time.sleep(.1)
            else:raise AssertionError('local listener not ready')
            request=urllib.request.Request(f'http://127.0.0.1:{remote}/',headers={'Host':'crawler.test'})
            try:
                urllib.request.urlopen(request,timeout=5)
                raise AssertionError('remote listener must reject anonymous clients')
            except urllib.error.HTTPError as error:
                assert error.code==401
            duplicate=subprocess.run(command,env=environment,capture_output=True,text=True,timeout=10)
            assert duplicate.returncode!=0
            assert '执行器已被占用' in duplicate.stderr
            assert not Path(environment['SQLITE_DB_PATH']).exists()
        finally:
            process.terminate();process.wait(timeout=15)
