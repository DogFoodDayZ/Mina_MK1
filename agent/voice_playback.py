"""Cross-process speaker ownership; OS locks are released even after a crash."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import time


LOCK_PATH = Path(tempfile.gettempdir()) / "mina_speaker.lock"


def _lock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)


def _open_lock():
    handle = LOCK_PATH.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")
        handle.flush()
    return handle


def playback_active() -> bool:
    with _open_lock() as handle:
        try:
            _lock(handle)
        except OSError as error:
            if error.errno not in (11, 13):
                raise
            return True
        _unlock(handle)
    return False


@contextmanager
def speaker_guard():
    with _open_lock() as handle:
        while True:
            try:
                _lock(handle)
                break
            except OSError as error:
                if error.errno not in (11, 13):
                    raise
                time.sleep(0.05)
        try:
            yield
        finally:
            time.sleep(0.25)
            _unlock(handle)
