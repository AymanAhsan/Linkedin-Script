import sqlite3
from datetime import datetime, timedelta

PROSPECT_STATUSES = (
    "queued",
    "connection_sent",
    "connected",
    "messaged",
    "replied",
)

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


def list_prospects(conn, statuses=None):
    """Return prospects, optionally limited to one or more lifecycle statuses."""
    query = (
        "SELECT id, name, profile_url, company, title, status, "
        "connection_sent_at, connected_at, messaged_at FROM prospects"
    )
    parameters = []

    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        query += f" WHERE status IN ({placeholders})"
        parameters.extend(statuses)

    query += " ORDER BY id"
    return conn.execute(query, parameters).fetchall()


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

def mark_connection_sent(prospect_id, db_path="state.db"):
    """Record that an invite was just sent; does not mean it was accepted."""
    now = datetime.now().isoformat()
    conn = get_conn(path=db_path)
    conn.execute(
        "UPDATE prospects SET status = 'connection_sent', connection_sent_at = ? WHERE id = ?",
        (now, prospect_id)
    )
    conn.commit()
    conn.close()


def mark_connected(prospect_id, db_path="state.db"):
    """Record that `check-replies` confirmed a pending invite was accepted."""
    now = datetime.now().isoformat()
    conn = get_conn(path=db_path)
    conn.execute(
        "UPDATE prospects SET status = 'connected', connected_at = ? WHERE id = ?",
        (now, prospect_id)
    )
    conn.commit()
    conn.close()


def select_pending_connections(conn):
    """Prospects whose invite was sent but not yet confirmed accepted.

    Oldest send first, since that date is the boundary `check-replies` scans
    the connections page down to.
    """
    return conn.execute(
        "SELECT id, name, profile_url, connection_sent_at FROM prospects "
        "WHERE status = 'connection_sent' "
        "ORDER BY connection_sent_at ASC"
    ).fetchall()


async def check_replies(db_path="state.db"):
    """Promote 'connection_sent' prospects to 'connected' once LinkedIn confirms.

    Walks the connections page (sorted "Recently added" by LinkedIn) and
    matches against prospects awaiting confirmation, by vanity name. Stops
    scanning once every card is older than the oldest pending invite's send
    date -- nothing further down the list can be a match.
    """
    # Imported here: cli and browser_actions import this module at load time.
    from playwright.async_api import async_playwright
    import browser_actions

    conn = get_conn(path=db_path)
    pending = select_pending_connections(conn)
    if not pending:
        print("Nothing pending: no prospects with status='connection_sent'.")
        return

    by_vanity = {}
    for row in pending:
        vanity = browser_actions._vanity_name(row["profile_url"])
        if vanity:
            by_vanity[vanity] = row

    oldest_sent = min(
        datetime.fromisoformat(row["connection_sent_at"]).date() for row in pending
    )

    async with async_playwright() as playwright:
        browser, context = await browser_actions.launch(playwright)
        try:
            page = await context.new_page()
            await browser_actions.assert_logged_in(page)
            connections = await browser_actions.get_connections(
                page, stop_before=oldest_sent
            )
        finally:
            await browser.close()

    matched = 0
    for vanity, row in by_vanity.items():
        if vanity in connections:
            mark_connected(row["id"], db_path=db_path)
            matched += 1
            print(f"Connected: {row['name']} ({vanity})")

    print(f"{matched} of {len(pending)} pending invites confirmed connected.")

def select_prospects_to_message(conn, daily_limit, wait_days, now=None):
    """Return connected prospects who are due their one follow-up message.

    Only 'connected' rows with no messaged_at qualify, and only once they've been
    connected at least wait_days -- matches CLAUDE.md's connect -> wait -> message
    sequence. Oldest connection first, capped at whatever is left of daily_limit
    after the messages already sent since midnight today.
    """
    now = now or datetime.now()
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = now - timedelta(days=wait_days)

    sent_today = conn.execute(
        "SELECT COUNT(*) FROM prospects WHERE messaged_at >= ?",
        (start_of_today.isoformat(),),
    ).fetchone()[0]

    remaining = daily_limit - sent_today
    if remaining <= 0:
        return []
    return conn.execute(
        "SELECT id, name, profile_url, title FROM prospects "
        "WHERE status = 'connected' AND messaged_at IS NULL AND connected_at <= ? "
        "ORDER BY connected_at ASC LIMIT ?",
        (cutoff.isoformat(), remaining),
    ).fetchall()


def mark_messaged(prospect_id, company, db_path="state.db"):
    """Record that the one follow-up message was just sent.

    Also backfills company from the freshly scraped profile chip, without
    clobbering an existing value if this particular scrape came back empty.
    """
    now = datetime.now().isoformat()
    conn = get_conn(path=db_path)
    conn.execute(
        "UPDATE prospects SET status = 'messaged', messaged_at = ?, "
        "company = COALESCE(NULLIF(?, ''), company) WHERE id = ?",
        (now, company, prospect_id)
    )
    conn.commit()
    conn.close()


async def send_due_messages():
    """Send the one follow-up message to every connection that's due it."""
    # Imported here: cli and browser_actions import this module at load time.
    from playwright.async_api import async_playwright
    import browser_actions
    from cli import load_config

    config = load_config()
    conn = get_conn(path="state.db")
    daily_limit = config.get("daily_message_limit", 5)
    wait_days = config.get("wait_days_before_message", 2)
    message_template = config["message_template"]

    rows = select_prospects_to_message(conn, daily_limit, wait_days)
    if not rows:
        print(f"Nothing to send: daily limit of {daily_limit} reached or nobody due yet.")
        return

    users = [dict(row) for row in rows]

    async with async_playwright() as playwright:
        browser, context = await browser_actions.launch(playwright)
        try:
            page = await context.new_page()
            await browser_actions.assert_logged_in(page)
            await browser_actions.message_users(page, users, message_template)
        finally:
            await browser.close()
