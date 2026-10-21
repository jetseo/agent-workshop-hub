"""Per-service request-per-minute limiter so a 429 is avoided before it happens (rpm=0 means unlimited).

The window is keyed by base_url, in memory, and shared by chat, board and digest calls in this process."""
import threading
import time

WINDOW = 60.0
MAX_WAIT = 15.0          # shorter waits are absorbed; longer ones fail so the caller can fall back to another model
_lock = threading.Lock()
_calls = {}


class RateLimited(RuntimeError):
    pass


def _scope(provider, account):
    return provider.get("base_url", "") + ("|" + account if account else "")


def try_acquire(provider, account=None, rpm=None, *, clock=time.monotonic):
    """Take a slot now if one is free (never waits). Used to pick among several accounts of one service."""
    limit = int(rpm if rpm is not None else provider.get("rpm") or 0)
    if limit <= 0:
        return True
    key = _scope(provider, account)
    with _lock:
        now = clock()
        window = [t for t in _calls.get(key, []) if now - t < WINDOW]
        ok = len(window) < limit
        if ok:
            window.append(now)
        _calls[key] = window
        return ok


def acquire(provider, account=None, rpm=None, *, clock=time.monotonic, sleep=time.sleep, max_wait=MAX_WAIT):
    limit = int(rpm if rpm is not None else provider.get("rpm") or 0)
    if limit <= 0:
        return
    key = _scope(provider, account)
    while True:
        with _lock:
            now = clock()
            window = [t for t in _calls.get(key, []) if now - t < WINDOW]
            if len(window) < limit:
                window.append(now)
                _calls[key] = window
                return
            wait = WINDOW - (now - window[0])
            _calls[key] = window
        if wait > max_wait:
            raise RateLimited("Local rate limit: %d requests/minute for this service (retry in ~%ds)" % (limit, int(wait) + 1))
        sleep(wait + 0.01)


def reset():
    with _lock:
        _calls.clear()
