"""Local administrator-managed identities, independent of BOM snapshots."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from collections import deque
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from .roles import LABELS

SESSION_SECONDS = 8 * 60 * 60
ROLES = tuple(LABELS)
GROUPS = (*ROLES, "other")


def default_roles(department):
    return ("rd" if department == "other" else department,)


@dataclass(frozen=True)
class Session:
    username: str
    salt: bytes
    deadline: float
    active_role: str


def password_key(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1,
                          maxmem=64 * 1024 * 1024, dklen=32)


def set_account(path: Path, username: str, group: str, password: str, roles=None):
    """Provision locally. Changing password or membership invalidates prior sessions."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username) or group not in GROUPS:
        raise ValueError("账号或部门组无效")
    if not 12 <= len(password) <= 256:
        raise ValueError("口令长度应为 12 至 256 个字符")
    roles = tuple(default_roles(group) if roles is None else roles)
    if not roles or any(role not in ROLES for role in roles) or len(set(roles)) != len(roles):
        raise ValueError("可用角色须为非空且不能重复")
    salt = secrets.token_bytes(16)
    digest = password_key(password, salt)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path)) as db, db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {"accounts"}:
            raise ValueError("该文件包含其他数据表，请指定独立的账号库")
        db.execute("CREATE TABLE IF NOT EXISTS accounts "
                   "(name TEXT PRIMARY KEY, department TEXT NOT NULL, salt BLOB NOT NULL, digest BLOB NOT NULL)")
        if "roles" not in {row[1] for row in db.execute("PRAGMA table_info(accounts)")}:
            db.execute("ALTER TABLE accounts ADD COLUMN roles TEXT")
        db.execute("INSERT OR REPLACE INTO accounts (name, department, salt, digest, roles) VALUES (?, ?, ?, ?, ?)",
                   (username, group, salt, digest, json.dumps(roles)))


class Accounts:
    """Session methods run under AppState.import_lock. The server opens accounts read-only."""

    def __init__(self, path=None):
        self.path = Path(path).resolve() if path else None
        self.sessions = {}
        self.failures = deque()
        self.dummy_salt = secrets.token_bytes(16)

    def _record(self, username):
        if self.path:
            try:
                with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    row = db.execute("SELECT * FROM accounts WHERE name=?", (username,)).fetchone()
                    if not row or row["department"] not in GROUPS:
                        return None
                    roles = default_roles(row["department"])
                    if "roles" in row.keys() and row["roles"] is not None:
                        roles = json.loads(row["roles"])
                        if not isinstance(roles, list) or not roles or any(role not in ROLES for role in roles):
                            return None
                    return row["department"], row["salt"], row["digest"], tuple(roles)
            except (sqlite3.Error, ValueError):
                return None
        return None

    def login(self, username, password):
        now = monotonic()
        while self.failures and self.failures[0] + 60 <= now:
            self.failures.popleft()
        if len(self.failures) >= 12:
            raise ValueError("登录尝试过多，请稍后再试")
        if (not isinstance(username, str) or not isinstance(password, str)
                or len(username) > 64 or not 1 <= len(password) <= 256):
            return None
        record = self._record(username)
        key = password_key(password, record[1] if record else self.dummy_salt)
        if not record or not hmac.compare_digest(key, record[2]):
            self.failures.append(now)
            return None
        self.sessions = {token: session for token, session in self.sessions.items() if session.deadline > now}
        token = secrets.token_urlsafe(32)
        self.sessions[token] = Session(username, record[1], now + SESSION_SECONDS, record[3][0])
        return token

    def identity(self, token):
        session = self.sessions.get(token)
        if session:
            record = self._record(session.username) if monotonic() < session.deadline else None
            if record and hmac.compare_digest(record[1], session.salt) and session.active_role in record[3]:
                return {"username": session.username, "department": record[0],
                        "roles": record[3], "active_role": session.active_role}
            self.logout(token)
        return None

    def switch_role(self, token, identity, role):
        if role == identity["active_role"]:
            return token
        session = self.sessions.pop(token)
        new_token = secrets.token_urlsafe(32)
        self.sessions[new_token] = Session(session.username, session.salt, session.deadline, role)
        return new_token

    def logout(self, token):
        self.sessions.pop(token, None)
