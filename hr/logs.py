"""Logging: stderr, and <data>/logs/app.log once setup_logging() has run."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from . import config as cfg


logger = logging.getLogger("heroic-ratings")
logger.setLevel(logging.INFO)
logger.propagate = False
_stderr = logging.StreamHandler(sys.stderr)
_stderr.setFormatter(logging.Formatter("[heroic-ratings] %(message)s"))
logger.addHandler(_stderr)


def setup_logging(log_dir: Path | None = None, max_bytes: int = 1_000_000, backups: int = 3) -> Path | None:
    """Add the rotating app.log file handler. Returns the log file, or None if it can't be written."""
    log_dir = log_dir or cfg.LOG_DIR
    for h in [h for h in logger.handlers if isinstance(h, logging.FileHandler)]:
        logger.removeHandler(h)
        h.close()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=max_bytes,
                                                  backupCount=backups, encoding="utf-8")
    except OSError as e:
        log("can't write logs to", log_dir, e)
        return None
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s: %(message)s"))
    logger.addHandler(fh)
    return log_dir / "app.log"


def log(*a, level: int = logging.INFO):
    logger.log(level, " ".join(str(x) for x in a))


def log_error(*a, exc: BaseException | None = None):
    """An error, with the traceback of exc (or of the exception being handled)."""
    logger.error(" ".join(str(x) for x in a), exc_info=exc if exc is not None else True)
