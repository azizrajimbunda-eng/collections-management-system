"""Authentication & role-based access control (session-cookie based)."""
import functools
import os
import pathlib
import secrets
import time

from flask import g, jsonify, session

import db
from pwutil import hash_password, verify_password

SECRET_FILE = pathlib.Path(__file__).resolve().parent / ".secret_key"
ROLES = ("admin", "encoder", "certifier", "viewer")


def get_secret():
    """Persistent session-signing key (so logins survive a server restart).
    Created atomically with owner-only permissions. NOTE: on Windows chmod is a
    no-op — protect the server folder with NTFS ACLs (see DEPLOY.md)."""
    if SECRET_FILE.exists():
        s = SECRET_FILE.read_text().strip()
        if s:
            return s
    s = secrets.token_hex(32)
    try:
        fd = os.open(str(SECRET_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(s)
    except FileExistsError:
        return SECRET_FILE.read_text().strip()   # a concurrent startup created it first
    return s


def get_db():
    if "db" not in g:
        g.db = db.connect()
    return g.db


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    r = get_db().execute("SELECT id, username, name, role FROM users WHERE id=? AND active=1", [uid]).fetchone()
    return dict(r) if r else None


# --- simple in-memory login throttle (per username; resets on server restart) ---
_FAILED = {}              # username -> [recent failure epoch-seconds]
_LOCK_THRESHOLD = 10      # this many failures ...
_LOCK_WINDOW = 900        # ... within 15 minutes locks the username for the rest of the window
_DUMMY_HASH = None


def _dummy_hash():
    """A real PBKDF2 hash used to equalize timing on a missing user (anti-enumeration)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("login-timing-equalizer")
    return _DUMMY_HASH


def is_locked(username):
    recent = [t for t in _FAILED.get(username, []) if t > time.time() - _LOCK_WINDOW]
    _FAILED[username] = recent
    return len(recent) >= _LOCK_THRESHOLD


def do_login(username, password):
    if is_locked(username):
        return None
    r = get_db().execute("SELECT * FROM users WHERE username=? AND active=1", [username]).fetchone()
    if r and verify_password(password, r["password_hash"]):
        _FAILED.pop(username, None)
        session.clear()
        session["uid"] = r["id"]
        session.permanent = True
        return {"id": r["id"], "username": r["username"], "name": r["name"], "role": r["role"]}
    if not r:
        verify_password(password, _dummy_hash())   # spend the same time as a real check
    _FAILED.setdefault(username, []).append(time.time())
    return None


def require_login(fn):
    @functools.wraps(fn)
    def wrap(*a, **k):
        u = current_user()
        if not u:
            return jsonify({"error": "Not logged in"}), 401
        g.user = u
        return fn(*a, **k)
    return wrap


def require_role(*roles):
    """Allow the listed roles; admin is always allowed."""
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **k):
            u = current_user()
            if not u:
                return jsonify({"error": "Not logged in"}), 401
            if u["role"] != "admin" and u["role"] not in roles:
                return jsonify({"error": "Forbidden — your role does not permit this action"}), 403
            g.user = u
            return fn(*a, **k)
        return wrap
    return deco
