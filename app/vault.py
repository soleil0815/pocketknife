"""Verschlüsselte Ablage von Kennwörtern in der Datenbank (Dienstkonto, Repository, SMTP).

Der Schlüssel liegt in /data/pocketknife.key (nur für root lesbar) und wird beim ersten Start erzeugt.
Wer nur die Datenbank kopiert, kann die Kennwörter nicht lesen; Sicherungen müssen den Schlüssel mitnehmen.
"""
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from . import config

_lock = threading.Lock()
_fernet = None
KEY_FILE = os.path.join(config.DATA_DIR, "pocketknife.key")


def _f():
    global _fernet
    with _lock:
        if _fernet is None:
            config.ensure_dirs()
            if not os.path.exists(KEY_FILE):
                key = Fernet.generate_key()
                fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(key)
            with open(KEY_FILE, "rb") as fh:
                _fernet = Fernet(fh.read().strip())
        return _fernet


def encrypt(text):
    if not text:
        return ""
    return _f().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(token):
    if not token:
        return ""
    try:
        return _f().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
