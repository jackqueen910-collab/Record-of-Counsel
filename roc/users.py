"""Local ROC identities, independent of PACER credentials and billing accounts."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

from .common import RocError


def email_address(value):
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value.strip()):
        raise RocError('Enter your email address.')
    return value.strip().lower()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest


def check_password(password, encoded):
    if not isinstance(password, str) or len(password) > 128:
        return False
    return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)


def new_password(values):
    password = values.get('password')
    if not isinstance(password, str) or not 5 <= len(password) <= 128:
        raise RocError('Use a password of 5–128 characters. No special-character rules.')
    if password != values.get('confirmation'):
        raise RocError('The passwords do not match.')
    return password


class Users:
    def __init__(self, root):
        self.path = root / 'users.sqlite3'
        self.dummy = password_hash(secrets.token_urlsafe(32))
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
                    role TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS attempts (scope TEXT, subject TEXT, stamp REAL);
                CREATE TABLE IF NOT EXISTS resets (digest TEXT PRIMARY KEY, user_id TEXT, version INTEGER, expires REAL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def throttle(self, scope, subject, maximum=6, seconds=300):
        stamp = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE stamp < ?', (stamp - 3600,))
            count = db.execute('SELECT COUNT(*) FROM attempts WHERE scope=? AND subject=? AND stamp>?',
                               (scope, subject, stamp-seconds)).fetchone()[0]
            if count >= maximum:
                raise RocError('Too many attempts. Wait a few minutes before trying again.')
            db.execute('INSERT INTO attempts VALUES (?,?,?)', (scope, subject, stamp))

    def create(self, values):
        email = email_address(values.get('email'))
        password = new_password(values)
        self.throttle('signup', '', 10)
        encoded = password_hash(password)
        try:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                role = 'member' if db.execute('SELECT 1 FROM users LIMIT 1').fetchone() else 'owner'
                identity = secrets.token_hex(16)
                db.execute('INSERT INTO users VALUES (?,?,?,?,1)', (identity, email, encoded, role))
        except sqlite3.IntegrityError:
            raise RocError('An account cannot be created with that email. Try signing in or password recovery.') from None
        return self.get(identity)

    def get(self, identity):
        with self.db() as db:
            row = db.execute('SELECT id,email,role,version FROM users WHERE id=?', (identity,)).fetchone()
            return dict(row) if row else None

    def login(self, values):
        email = email_address(values.get('email'))
        self.throttle('login-global', '', 40)
        self.throttle('login', email)
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        accepted = check_password(values.get('password'), row['password'] if row else self.dummy)
        if not accepted or not row:
            raise RocError('Email or password was not accepted. Correct it and try again.')
        with self.db() as db:
            db.execute('DELETE FROM attempts WHERE scope=? AND subject=?', ('login', email))
        return self.get(row['id'])

    def reset_request(self, value):
        email = email_address(value)
        self.throttle('reset-global', '', 10)
        self.throttle('reset', email, 3, 900)
        with self.db() as db:
            row = db.execute('SELECT id,version FROM users WHERE email=?', (email,)).fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM resets WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO resets VALUES (?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), row['id'], row['version'], time.time()+1800))
        return {'email': email, 'token': token}

    def reset(self, values):
        password = new_password(values)
        token = values.get('token', '')
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise RocError('This recovery link is invalid or expired.')
        self.throttle('reset-submit', '', 20)
        encoded = password_hash(password)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT r.*,u.version AS current_version FROM resets r JOIN users u ON u.id=r.user_id WHERE digest=?',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if not row or row['expires'] < time.time() or row['version'] != row['current_version']:
                raise RocError('This recovery link is invalid or expired.')
            db.execute('UPDATE users SET password=?,version=version+1 WHERE id=?', (encoded, row['user_id']))
            db.execute('DELETE FROM resets WHERE user_id=?', (row['user_id'],))
        return row['user_id']
