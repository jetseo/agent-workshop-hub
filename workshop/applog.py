"""Small rotating log file (~/.agent-workshop/logs/hub.log): request lines and start/stop events, never tokens or bodies."""
import logging
import logging.handlers
import os
from pathlib import Path

MAX_BYTES = 1_000_000
BACKUPS = 3
_logger = None


def default_path():
    return Path.home() / ".agent-workshop" / "logs" / "hub.log"


def setup(path=None):
    """Create (once) the file logger. Returns None when the file cannot be written: logging must never stop the app."""
    global _logger
    if _logger is not None and path is None:
        return _logger
    target = Path(path) if path else default_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT, 0o600))
        handler = logging.handlers.RotatingFileHandler(target, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger = logging.getLogger("jetai.hub." + str(target))
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for old in list(logger.handlers):
            logger.removeHandler(old)
            old.close()
        logger.addHandler(handler)
    except OSError:
        return None
    if path is None:
        _logger = logger
    return logger


def close(logger=None):
    """Release the file handle (needed before the file can be deleted on Windows)."""
    logger = logger or _logger
    if logger is None:
        return
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def write(line, logger=None):
    logger = logger or _logger
    if logger is None:
        return
    try:
        logger.info(line)
    except Exception:                      # a full disk or a closed handle must not break request handling
        pass
