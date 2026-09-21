import sqlite3

# Create a table to store the state

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    profile_url  TEXT NOT NULL UNIQUE,
    company      TEXT,
    title        TEXT,
    status       TEXT NOT NULL DEFAULT 'queued',
    connected_at TEXT,
    messaged_at  TEXT
);
"""

def get_conn(path="state.db"):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn

def add_prospect(conn, name, profile_url, company=None, title=None):
    conn.execute(
        "INSERT OR IGNORE INTO prospects (name, profile_url, company, title) VALUES (?, ?, ?, ?)",
        (name, profile_url, company, title)
    )
    conn.commit()


def add_prospects(conn, prospects):
    """Insert multiple prospects in one transaction."""
    conn.executemany(
        "INSERT OR IGNORE INTO prospects (name, profile_url, company, title) "
        "VALUES (?, ?, ?, ?)",
        prospects,
    )
    conn.commit()

