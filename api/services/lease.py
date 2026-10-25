"""OS-backed process lease; released only when every holder exits."""
from pathlib import Path
import os


class Lease:
    def __init__(self, path):
        self.path=Path(path)
        self.file=None

    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b')
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                if self.path.stat().st_size==0:
                    self.file.write(b'0');self.file.flush();self.file.seek(0)
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            return self
        except OSError:
            self.file.close();self.file=None
            raise RuntimeError('执行器已被占用；请在采集电脑检查已有服务或遗留任务') from None

    def close(self):
        if self.file:
            self.file.close();self.file=None

    def __enter__(self):
        return self.acquire()

    def __exit__(self,*_):
        self.close()


def available(path):
    try:
        with Lease(path):
            return True
    except RuntimeError:
        return False
