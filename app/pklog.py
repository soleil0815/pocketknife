"""Logging to file (/data/logs) and to the database."""
import logging
import logging.handlers
import os

from . import config, db, util

logger = logging.getLogger("pocketknife")


def setup():
    config.ensure_dirs()
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(config.LOG_DIR, "pocketknife.log"), maxBytes=10 * 1024 * 1024,
        backupCount=10, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    # quieten the libraries
    for name in ("smbprotocol", "spnego", "pypsrp", "urllib3", "ldap3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log(level, category, message, user=None, computer_id=None):
    level = level.upper()
    logger.log(getattr(logging, level if level != "WARN" else "WARNING", logging.INFO),
               "[%s] %s%s", category, message, f" ({user})" if user else "")
    try:
        db.ex("INSERT INTO logs(ts, level, category, message, username, computer_id) VALUES(?,?,?,?,?,?)",
              (util.now(), "WARN" if level == "WARNING" else level, category, str(message)[:4000], user, computer_id))
    except Exception:  # pragma: no cover
        logger.exception("Log konnte nicht gespeichert werden")


def info(cat, msg, **kw):
    log("INFO", cat, msg, **kw)


def warn(cat, msg, **kw):
    log("WARN", cat, msg, **kw)


def error(cat, msg, **kw):
    log("ERROR", cat, msg, **kw)
