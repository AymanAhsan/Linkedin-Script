"""Entry point.

    python cli.py login     one-time manual sign-in; saves the session
    python cli.py search          open the configured search with that session
    python cli.py daily-sequence  queue prospects, connect, check replies, and message
"""

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from config import link_builder
from db import (
    PROSPECT_STATUSES,
    check_replies,
    find_queued_prospects,
    get_conn,
    list_prospects,
    send_due_messages,
)


def load_config() -> dict:
    with open(Path(__file__).with_name("config.json")) as f:
        return json.load(f)


async def cmd_login() -> None:
    # Imported here, not at module level: browser_actions imports load_config
    # back from this module, so importing it before load_config is defined
    # would deadlock the cycle.
    import browser_actions

    async with async_playwright() as playwright:
        await browser_actions.save_login(playwright)



async def cmd_search() -> None:
    import browser_actions

    config = load_config()
    url = link_builder(
        config["target_companies"],
        config["titles"],
        connected=config.get("connected", False),
    )
    async with async_playwright() as playwright:
        await browser_actions.run(playwright, url)


async def cmd_collect_prospects() -> None:
    """Run the search stage without waiting for interactive confirmation.

    ``search`` remains an interactive command for inspecting results. The daily
    sequence uses this version so it can continue directly into the capped
    connect, reply-check, and message stages.
    """
    import browser_actions

    config = load_config()
    url = link_builder(
        config["target_companies"],
        config["titles"],
        connected=config.get("connected", False),
    )

    async with async_playwright() as playwright:
        browser, context = await browser_actions.launch(playwright)
        try:
            page = await context.new_page()
            await browser_actions.assert_logged_in(page)
            await page.goto(url, wait_until="domcontentloaded")
            if browser_actions._is_checkpoint(page.url):
                raise browser_actions.CheckpointError(
                    f"Checkpoint hit while loading {url}."
                )
            browser_actions.queue_users(await browser_actions.get_users(page))
        finally:
            await browser.close()


async def cmd_daily_sequence() -> None:
    """Run the complete daily outreach workflow using the configured limits."""
    stages = (
        ("Search and queue prospects", cmd_collect_prospects),
        ("Send connection requests", find_queued_prospects),
        ("Check accepted connections", check_replies),
        ("Send due follow-up messages", send_due_messages),
    )

    for label, stage in stages:
        print(f"\n== {label} ==")
        await stage()

    print("\nDaily sequence complete.")


def _format_queue(rows) -> str:
    """Format prospect rows as a readable terminal table."""
    columns = (
        ("ID", "id"),
        ("Name", "name"),
        ("Status", "status"),
        ("Company", "company"),
        ("Title", "title"),
        ("Profile URL", "profile_url"),
        ("Connection sent", "connection_sent_at"),
        ("Connected", "connected_at"),
        ("Messaged", "messaged_at"),
    )
    values = [
        [str(row[key]) if row[key] is not None else "" for _, key in columns]
        for row in rows
    ]
    widths = [
        max(len(header), *(len(row[index]) for row in values))
        for index, (header, _) in enumerate(columns)
    ]

    def format_row(values):
        return " | ".join(
            value.ljust(width) for value, width in zip(values, widths)
        )

    header = format_row([header for header, _ in columns])
    separator = "-+-".join("-" * width for width in widths)
    return "\n".join([header, separator, *(format_row(row) for row in values)])


async def cmd_view_queue(statuses=None) -> None:
    """Print the local prospect database, optionally filtered by status."""
    conn = get_conn(path="state.db")
    try:
        rows = list_prospects(conn, statuses)
    finally:
        conn.close()

    if not rows:
        if statuses:
            print(f"No prospects found with status: {', '.join(statuses)}.")
        else:
            print("The prospect queue is empty.")
        return

    print(_format_queue(rows))
    print(f"\n{len(rows)} prospect(s) shown.")


def main() -> None:
    import browser_actions

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="sign in by hand and save the session")
    sub.add_parser("search", help="open the configured LinkedIn search")
    sub.add_parser("continue", help="runs the current sequence")
    sub.add_parser("check-replies", help="mark pending invites as connected once accepted")
    sub.add_parser("send-messages", help="send the one follow-up message to due connections")
    sub.add_parser(
        "daily-sequence",
        help="run search, connect, check-replies, and send-messages in order",
    )
    view_queue = sub.add_parser("view_queue", help="show prospects in the local queue")
    view_queue.add_argument(
        "--status",
        action="append",
        choices=PROSPECT_STATUSES,
        help="only show this status; repeat to include multiple statuses",
    )

    args = parser.parse_args()
    try:
        if args.command == "view_queue":
            asyncio.run(cmd_view_queue(args.status))
        else:
            handler = {
                "login": cmd_login,
                "search": cmd_search,
                "continue": find_queued_prospects,
                "check-replies": check_replies,
                "send-messages": send_due_messages,
                "daily-sequence": cmd_daily_sequence,
            }[args.command]
            asyncio.run(handler())
    except KeyboardInterrupt:
        pass
    except browser_actions.CheckpointError as exc:
        raise SystemExit(f"\nSTOPPED: {exc}")
    except RuntimeError as exc:
        raise SystemExit(f"\n{exc}")


if __name__ == "__main__":
    main()
