"""Initial-run test: open a search URL, scrape everyone, queue them, check the DB.

Runs against a local HTML page that mimics LinkedIn's search-results markup, so
it needs no login and sends nothing to LinkedIn. The queue lives in a throwaway
SQLite file (never the real state.db), and is emptied and deleted when the test
ends, pass or fail.

    python test/test_initial_run.py
    pytest test/test_initial_run.py
"""

import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from playwright.async_api import async_playwright

import browser_actions
from db import get_conn

PEOPLE = [
    ("Ada Lovelace", "https://www.linkedin.com/in/ada-lovelace", "Software Engineer at Stripe", "New York, NY", "connect"),
    ("Grace Hopper", "https://www.linkedin.com/in/grace-hopper", "SWE Intern at Datadog", "Boston, MA", "connect"),
    ("Alan Turing", "https://www.linkedin.com/in/alan-turing", "Software Engineer at Ramp", "New York, NY", "pending"),
]


def _card(name, url, headline, location, state):
    return f"""
    <div role="listitem">
      <p><a href="{url}?miniProfileUrn=abc">{name}</a> &bull; 2nd</p>
      <p>{headline}</p>
      <p>{location}</p>
      <a componentkey="ConnectButton_{state}" href="#">{state}</a>
    </div>"""


SEARCH_PAGE = "<html><body><main>{}</main></body></html>".format(
    "".join(_card(*p) for p in PEOPLE)
)


@pytest.fixture
def db_path():
    """Temp queue database, cleared and removed however the test ends."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        conn = get_conn(path)
        try:
            with conn:
                conn.execute("DELETE FROM prospects")
            leftover = conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
        finally:
            conn.close()
        Path(path).unlink(missing_ok=True)
        assert leftover == 0, "database was not cleared"


@pytest.fixture
def search_url(tmp_path):
    page = tmp_path / "search.html"
    page.write_text(SEARCH_PAGE, encoding="utf-8")
    return page.as_uri()


async def _scrape(url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            assert not browser_actions._blocked(page.url)
            return await browser_actions.get_users(page)
        finally:
            await browser.close()


def test_initial_run_queues_everyone(search_url, db_path):
    users = asyncio.run(_scrape(search_url))
    assert len(users) == len(PEOPLE)

    browser_actions.queue_users(users, db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM prospects ORDER BY id").fetchall()
    finally:
        conn.close()

    assert len(rows) == len(PEOPLE)
    assert {r["profile_url"] for r in rows} == {p[1] for p in PEOPLE}
    for row, (name, url, headline, *_) in zip(rows, PEOPLE):
        assert row["name"] == name
        assert row["title"] == headline
        assert row["status"] == "queued"
        assert row["connected_at"] is None
        assert row["messaged_at"] is None


def test_queueing_twice_does_not_duplicate(search_url, db_path):
    users = asyncio.run(_scrape(search_url))
    browser_actions.queue_users(users, db_path)
    browser_actions.queue_users(users, db_path)

    conn = get_conn(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
    finally:
        conn.close()
    assert count == len(PEOPLE)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
