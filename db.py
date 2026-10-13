"""SQLite persistence: accounts, sessions, ledger, tables, hand history, seated stacks."""
import hashlib
import json
import os
import secrets
import sqlite3
import time

DB_PATH = os.environ.get("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "poker.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

_db = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA journal_mode=WAL")
_db.executescript("""
CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY COLLATE NOCASE, salt TEXT, pw TEXT, created REAL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, name TEXT, created REAL);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, name TEXT, table_id TEXT, table_name TEXT,
    kind TEXT, amount INTEGER, ts REAL, settled INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tables(id TEXT PRIMARY KEY, config TEXT, creator TEXT, created REAL);
CREATE TABLE IF NOT EXISTS hands(id INTEGER PRIMARY KEY, table_id TEXT, hand_no INTEGER, log TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS hands_by_table ON hands(table_id, id);
CREATE TABLE IF NOT EXISTS seated(table_id TEXT, table_name TEXT, name TEXT, stack INTEGER,
    PRIMARY KEY(table_id, name));
""")


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 100_000).hex()


def login(name, pw):
    """Log in, creating the account on first use. Returns (canonical_name, token)."""
    row = _db.execute("SELECT name, salt, pw FROM users WHERE name=?", (name,)).fetchone()
    if row:
        if not secrets.compare_digest(_hash(pw, row["salt"]), row["pw"]):
            raise ValueError("Wrong password for that name")
        name = row["name"]
    else:
        salt = secrets.token_hex(16)
        _db.execute("INSERT INTO users VALUES(?,?,?,?)", (name, salt, _hash(pw, salt), time.time()))
    token = secrets.token_urlsafe(24)
    _db.execute("INSERT INTO sessions VALUES(?,?,?)", (token, name, time.time()))
    return name, token


def user_for_token(token):
    if not token:
        return None
    row = _db.execute("SELECT name FROM sessions WHERE token=?", (token,)).fetchone()
    return row["name"] if row else None


# ---- ledger
def add_ledger(name, table_id, table_name, kind, amount):
    _db.execute("INSERT INTO ledger(name, table_id, table_name, kind, amount, ts) VALUES(?,?,?,?,?,?)",
                (name, table_id, table_name, kind, int(amount), time.time()))


def ledger_summary():
    rows = _db.execute("""SELECT name,
        SUM(CASE WHEN kind='buyin' THEN amount ELSE 0 END) AS b,
        SUM(CASE WHEN kind='cashout' THEN amount ELSE 0 END) AS c
        FROM ledger WHERE settled=0 GROUP BY name""").fetchall()
    return {r["name"]: (r["b"] or 0, r["c"] or 0) for r in rows}


def settle():
    _db.execute("UPDATE ledger SET settled=1 WHERE settled=0")


# ---- tables
def save_table(tid, config, creator):
    _db.execute("INSERT OR REPLACE INTO tables VALUES(?,?,?,?)", (tid, json.dumps(config), creator, time.time()))


def load_tables():
    return [(r["id"], json.loads(r["config"]), r["creator"])
            for r in _db.execute("SELECT * FROM tables ORDER BY created")]


def delete_table(tid):
    _db.execute("DELETE FROM tables WHERE id=?", (tid,))


# ---- hand history
def save_hand(tid, hand_no, log):
    _db.execute("INSERT INTO hands(table_id, hand_no, log, ts) VALUES(?,?,?,?)", (tid, hand_no, log, time.time()))


def recent_hands(tid, limit=30):
    rows = _db.execute("SELECT hand_no, log, ts FROM hands WHERE table_id=? ORDER BY id DESC LIMIT ?",
                       (tid, limit)).fetchall()
    return [dict(r) for r in rows]


def max_hand_no(tid):
    r = _db.execute("SELECT MAX(hand_no) AS m FROM hands WHERE table_id=?", (tid,)).fetchone()
    return r["m"] or 0


# ---- seated stacks (so a server restart cashes everyone out instead of losing chips)
def snapshot(tid, table_name, rows):
    _db.execute("DELETE FROM seated WHERE table_id=?", (tid,))
    _db.executemany("INSERT INTO seated VALUES(?,?,?,?)", [(tid, table_name, n, s) for n, s in rows])


def take_orphans():
    rows = [dict(r) for r in _db.execute("SELECT * FROM seated")]
    _db.execute("DELETE FROM seated")
    return rows
