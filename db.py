import sqlite3
from datetime import datetime

# Create a table to store the state

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    profile_url  TEXT NOT NULL UNIQUE,
    company      TEXT,
    title        TEXT,
    status       TEXT NOT NULL DEFAULT 'queued',
    connection_sent_at TEXT,
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
    print(f"Adding {len(prospects)} prospects to the database...")
    conn.executemany(
        "INSERT OR IGNORE INTO prospects (name, profile_url, company, title) "
        "VALUES (?, ?, ?, ?)",
        prospects,
    )
    conn.commit()


def select_prospects_to_connect(conn, daily_limit, now=None):
    """Return the queued prospects that may be connected with right now.

    Only 'queued' rows qualify, oldest first, capped at whatever is left of
    daily_limit after the requests already sent since midnight today.
    """
    now = now or datetime.now()
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    sent_today = conn.execute(
        "SELECT COUNT(*) FROM prospects WHERE connection_sent_at >= ?",
        (start_of_today.isoformat(),),
    ).fetchone()[0]

    remaining = daily_limit - sent_today
    if remaining <= 0:
        return []
    return conn.execute(
        "SELECT id, profile_url FROM prospects "
        "WHERE status = 'queued' "
        "ORDER BY id LIMIT ?",
        (remaining,),
    ).fetchall()


async def find_queued_prospects():
    """Selects users from the database."""
    # Imported here: cli and browser_actions import this module at load time.
    from playwright.async_api import async_playwright
    import browser_actions
    from cli import load_config

    config = load_config()
    conn = get_conn(path="state.db")
    daily_limit = config.get("daily_connection_limit", 10)

    rows = select_prospects_to_connect(conn, daily_limit)
    if not rows:
        print(f"Nothing to send: daily limit of {daily_limit} reached or queue empty.")
        return

    # sqlite3.Row has no .get(), which connect_users relies on.
    users = [dict(row) for row in rows]

    async with async_playwright() as playwright:
        browser, context = await browser_actions.launch(playwright)
        try:
            page = await context.new_page()
            await browser_actions.assert_logged_in(page)
            await browser_actions.connect_users(page, users)
        finally:
            await browser.close()

def set_connection_status(prospect_id):
    """Update the status of a prospect in the database."""

    # Get current datetime
    now = datetime.now().isoformat()
    conn = get_conn(path="state.db")
    conn.execute(
        "UPDATE prospects SET status = 'connected', connected_at = ? WHERE id = ?",
        (now, prospect_id)
    )
    conn.commit()