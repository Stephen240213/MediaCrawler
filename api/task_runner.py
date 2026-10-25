"""Task process supervisor. No shell, browser endpoint or automatic restart."""
import os
import signal
import subprocess
import sys
from pathlib import Path
from api.services.lease import Lease


def main():
    lock=Path(os.environ['MEDIACRAWLER_EXECUTION_LOCK'])
    try:
        with Lease(lock) as lease:
            # Keep the lease alive in the actual collector too on POSIX. If this
            # supervisor is killed, a surviving collector still blocks new tasks.
            inherited=()
            if os.name!='nt':
                inherited=(lease.file.fileno(),)
            command=sys.argv[1:]
            if not command:
                return 64
            process=subprocess.Popen(command,env=os.environ,pass_fds=inherited) if os.name!='nt' else subprocess.Popen(command,env=os.environ)
            cleanup=None
            if os.name=='nt':
                from api.services.windows_job import attach
                cleanup=attach(process)
            if os.name!='nt':
                signal.signal(signal.SIGTERM,lambda *_: None)
            try:
                return process.wait()
            except KeyboardInterrupt:
                return process.wait()
            finally:
                if cleanup: cleanup()
    except RuntimeError as exc:
        print(str(exc),flush=True)
        return 75


if __name__=='__main__':
    raise SystemExit(main())
