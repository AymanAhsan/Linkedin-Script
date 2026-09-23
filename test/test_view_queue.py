"""Tests for viewing local prospect records without opening a browser."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import _format_queue
from db import get_conn, list_prospects


def seed_prospect(conn, name, status):
    conn.execute(
        "INSERT INTO prospects (name, profile_url, company, title, status) "
        "VALUES (?, ?, 'Example Co', 'Engineer', ?)",
        (name, f"https://www.linkedin.com/in/{name.lower()}", status),
    )
    conn.commit()


def test_list_prospects_returns_all_records_in_queue_order(tmp_path):
    conn = get_conn(str(tmp_path / "test_state.db"))
    try:
        seed_prospect(conn, "Ada", "queued")
        seed_prospect(conn, "Grace", "connected")

        rows = list_prospects(conn)
    finally:
        conn.close()

    assert [row["name"] for row in rows] == ["Ada", "Grace"]


def test_list_prospects_filters_by_multiple_statuses(tmp_path):
    conn = get_conn(str(tmp_path / "test_state.db"))
    try:
        seed_prospect(conn, "Ada", "queued")
        seed_prospect(conn, "Grace", "connected")
        seed_prospect(conn, "Lin", "messaged")

        rows = list_prospects(conn, ["queued", "messaged"])
    finally:
        conn.close()

    assert [row["name"] for row in rows] == ["Ada", "Lin"]


def test_format_queue_includes_all_displayed_prospect_fields(tmp_path):
    conn = get_conn(str(tmp_path / "test_state.db"))
    try:
        seed_prospect(conn, "Ada", "queued")
        output = _format_queue(list_prospects(conn))
    finally:
        conn.close()

    assert "Profile URL" in output
    assert "https://www.linkedin.com/in/ada" in output
    assert "Ada" in output
    assert "queued" in output
