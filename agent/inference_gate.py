"""Serialize Mina-owned inference, including complete streaming responses."""
from contextlib import contextmanager
from functools import wraps
import threading


_gate = threading.Lock()


@contextmanager
def inference_slot():
    if not _gate.acquire(timeout=120):
        raise TimeoutError("Local inference is busy; no additional request was started.")
    try:
        yield
    finally:
        _gate.release()


def serial_completion(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with inference_slot():
            return function(*args, **kwargs)
    return wrapped


def serial_stream(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with inference_slot():
            yield from function(*args, **kwargs)
    return wrapped
