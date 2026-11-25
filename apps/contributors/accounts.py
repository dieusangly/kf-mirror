"""Accounts and server-side sessions for the contributor workspace."""

from contextlib import closing
from hashlib import scrypt, sha256
from hmac import compare_digest
import os
import re
from secrets import token_bytes, token_urlsafe
import sqlite3
from threading import BoundedSemaphore
import time


SESSION_SECONDS = 8 * 60 * 60
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_ATTEMPTS = 5
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


def connect(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA foreign_keys = ON')
    return db


def password_hash(password, salt):
    return scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3, maxmem=64 * 1024**2)


def normalized_email(email):
    if isinstance(email, str) and len(email) <= 254:
        email = email.strip().lower()
        if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            return email
    return None


def manageable(user, owner_id):
    return user['admin'] or user['id'] == owner_id


class LoginLimited(Exception):
    def __init__(self, seconds):
        self.seconds = seconds


class Accounts:
    def __init__(self, path):
        self.path = path
        self.login_slots = BoundedSemaphore(4)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        with closing(connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    admin INTEGER NOT NULL CHECK (admin IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS user_sessions (
                    token_hash BLOB PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    email_hash BLOB PRIMARY KEY,
                    attempts INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
            """)

    def add(self, email, name, password, *, admin=False):
        email = normalized_email(email)
        name = name.strip()
        if not email:
            raise ValueError('L’adresse e-mail est invalide.')
        if not 0 < len(name) <= 120:
            raise ValueError('Le nom d’utilisateur doit contenir entre 1 et 120 caractères.')
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise ValueError(
                f'Le mot de passe doit contenir entre {MIN_PASSWORD_LENGTH} et {MAX_PASSWORD_LENGTH} caractères.'
            )
        salt = token_bytes(16)
        digest = password_hash(password, salt)
        with closing(connect(self.path)) as db, db:
            db.execute("""
                INSERT INTO users(email, display_name, salt, password_hash, admin)
                VALUES (?, ?, ?, ?, ?)
            """, (email, name, salt, digest, admin))

    def register_attempt(self, email, now):
        key = sha256(email.encode()).digest()
        with closing(connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_attempts WHERE expires_at <= ?', (now,))
            attempt = db.execute(
                'SELECT attempts, expires_at FROM login_attempts WHERE email_hash = ?', (key,)
            ).fetchone()
            if attempt and attempt[0] >= LOGIN_ATTEMPTS:
                raise LoginLimited(attempt[1] - now)
            db.execute("""
                INSERT INTO login_attempts(email_hash, attempts, expires_at) VALUES (?, 1, ?)
                ON CONFLICT(email_hash) DO UPDATE SET attempts = attempts + 1
            """, (key, now + LOGIN_WINDOW_SECONDS))
        return key

    def login(self, email, password, previous_token):
        email = normalized_email(email)
        if not email or not isinstance(password, str) or not 0 < len(password) <= MAX_PASSWORD_LENGTH:
            return None
        if not self.login_slots.acquire(blocking=False):
            raise LoginLimited(2)
        try:
            now = int(time.time())
            attempt_key = self.register_attempt(email, now)
            with closing(connect(self.path)) as db:
                row = db.execute("""
                    SELECT id, display_name, admin, salt, password_hash FROM users WHERE email = ?
                """, (email,)).fetchone()
            salt, expected = row[3:] if row else (bytes(16), bytes(64))
            matches = compare_digest(password_hash(password, salt), expected)
            if not row or not matches:
                return None
            token = token_urlsafe(32)
            with closing(connect(self.path)) as db, db:
                db.execute('DELETE FROM login_attempts WHERE email_hash = ?', (attempt_key,))
                db.execute('DELETE FROM user_sessions WHERE expires_at <= ? OR token_hash = ?',
                           (now, self.token_hash(previous_token)))
                db.execute('INSERT INTO user_sessions VALUES (?, ?, ?)',
                           (self.token_hash(token), row[0], now + SESSION_SECONDS))
            return self.user(row), token
        finally:
            self.login_slots.release()

    @staticmethod
    def token_hash(token):
        return sha256(token.encode('ascii')).digest() if token else None

    @staticmethod
    def user(row):
        return {'id': row[0], 'name': row[1], 'admin': bool(row[2])}

    def session_user(self, token):
        if not token:
            return None
        with closing(connect(self.path)) as db:
            row = db.execute("""
                SELECT u.id, u.display_name, u.admin FROM user_sessions AS s
                JOIN users AS u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
            """, (self.token_hash(token), int(time.time()))).fetchone()
        return self.user(row) if row else None

    def logout(self, token):
        if token:
            with closing(connect(self.path)) as db, db:
                db.execute('DELETE FROM user_sessions WHERE token_hash = ?', (self.token_hash(token),))
