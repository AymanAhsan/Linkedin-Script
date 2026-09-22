"""Selection logic for `connect`: which queued prospects get picked today.

Uses a seeded throwaway SQLite DB; no browser, no LinkedIn.

    pytest test/test_select_prospects.py
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from db import get_conn, select_prospects_to_connect

NOW = datetime(2026, 9, 21, 15, 0, 0)
TODAY = NOW.replace(hour=9).isoformat()
YESTERDAY = (NOW - timedelta(days=1)).replace(hour=23, minute=59).isoformat()


def url(n):
    return f"https://www.linkedin.com/in/person-{n}"


def seed(conn, queued=0, connected=0, sent_today=0, sent_yesterday=0):
    """Insert rows; returns the urls of the queued ones in insertion order."""
    n = 0

    def insert(status, sent_at=None):
        nonlocal n
        n += 1
        conn.execute(
            "INSERT INTO prospects (name, profile_url, status, connection_sent_at) "
            "VALUES (?, ?, ?, ?)",
            (f"Person {n}", url(n), status, sent_at),
        )
        return url(n)

    for _ in range(connected):
        insert("connected", YESTERDAY)
    for _ in range(sent_yesterday):
        insert("connection_sent", YESTERDAY)
    for _ in range(sent_today):
        insert("connection_sent", TODAY)
    queued_urls = [insert("queued") for _ in range(queued)]
    conn.commit()
    return queued_urls


@pytest.fixture
def conn(tmp_path):
    c = get_conn(str(tmp_path / "test_state.db"))
    yield c
    c.close()


def picked(conn, cap):
    return [r["profile_url"] for r in select_prospects_to_connect(conn, cap, now=NOW)]


def test_picks_exactly_cap_when_queue_is_larger(conn):
    queued = seed(conn, queued=10)
    assert picked(conn, 4) == queued[:4]


def test_picks_all_when_queue_smaller_than_cap(conn):
    queued = seed(conn, queued=3)
    assert picked(conn, 8) == queued


def test_never_selects_connected_or_already_sent(conn):
    queued = seed(conn, queued=5, connected=4, sent_yesterday=3)
    assert picked(conn, 20) == queued


def test_sent_today_counts_against_cap(conn):
    queued = seed(conn, queued=10, sent_today=3)
    assert picked(conn, 5) == queued[:2]


def test_cap_already_reached_selects_nobody(conn):
    seed(conn, queued=10, sent_today=5)
    assert picked(conn, 5) == []


def test_sent_today_over_cap_selects_nobody(conn):
    seed(conn, queued=10, sent_today=9)
    assert picked(conn, 5) == []


def test_yesterdays_sends_do_not_count(conn):
    queued = seed(conn, queued=10, sent_yesterday=6, connected=2)
    assert picked(conn, 5) == queued[:5]


def test_midnight_boundary(conn):
    queued = seed(conn, queued=3)
    midnight = NOW.replace(hour=0, minute=0, second=0).isoformat()
    just_before = (NOW.replace(hour=0, minute=0, second=0) - timedelta(seconds=1)).isoformat()
    conn.execute(
        "INSERT INTO prospects (name, profile_url, status, connection_sent_at) VALUES "
        "('a', 'u-midnight', 'connection_sent', ?), ('b', 'u-before', 'connection_sent', ?)",
        (midnight, just_before),
    )
    conn.commit()
    # Only the send at exactly 00:00 counts, leaving 1 of the cap of 2.
    assert picked(conn, 2) == queued[:1]


def test_oldest_queued_first(conn):
    queued = seed(conn, queued=6)
    assert picked(conn, 3) == queued[:3]


def test_empty_queue(conn):
    seed(conn, connected=3)
    assert picked(conn, 5) == []


def test_zero_cap(conn):
    seed(conn, queued=5)
    assert picked(conn, 0) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
