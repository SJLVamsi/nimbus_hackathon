import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    patient_id TEXT,
    type TEXT NOT NULL,
    actor TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    state_after TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_patient ON events(patient_id, ts, id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type, ts);

CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'walkin',
    reason_text TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    owner TEXT,
    entered_state_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    blocked INTEGER NOT NULL DEFAULT 0,
    block_reason TEXT,
    blocked_since TEXT,
    prev_state TEXT,
    prev_owner TEXT,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL,
    from_role TEXT,
    to_role TEXT NOT NULL,
    item TEXT NOT NULL,
    initiated_at TEXT NOT NULL,
    accepted_at TEXT,
    accepted_by TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    sla_minutes REAL
);
CREATE INDEX IF NOT EXISTS idx_handoffs_patient ON handoffs(patient_id, id);
CREATE INDEX IF NOT EXISTS idx_handoffs_status ON handoffs(status);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    patient_id TEXT,
    handoff_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@contextmanager
def tx():
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM patients")
        conn.execute("DELETE FROM handoffs")
        conn.execute("DELETE FROM alerts")
        try:
            conn.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass


def table_count(table: str) -> int:
    with tx() as conn:
        return conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
