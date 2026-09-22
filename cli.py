"""Entry point.

    python cli.py login     one-time manual sign-in; saves the session
    python cli.py search    open the configured search with that session
"""

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

import browser_actions
from config import link_builder
from db import find_queued_prospects


def load_config() -> dict:
    with open(Path(__file__).with_name("config.json")) as f:
        return json.load(f)


async def cmd_login() -> None:
    async with async_playwright() as playwright:
        await browser_actions.save_login(playwright)



async def cmd_search() -> None:
    config = load_config()
    url = link_builder(
        config["target_companies"],
        config["titles"],
        connected=config.get("connected", False),
    )
    async with async_playwright() as playwright:
        await browser_actions.run(playwright, url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="sign in by hand and save the session")
    sub.add_parser("search", help="open the configured LinkedIn search")
    sub.add_parser("continue", help="runs the current sequence")

    
    args = parser.parse_args()
    handler = {"login": cmd_login, "search": cmd_search, "continue": find_queued_prospects}[args.command]

    try:
        asyncio.run(handler())
    except KeyboardInterrupt:
        pass
    except browser_actions.CheckpointError as exc:
        raise SystemExit(f"\nSTOPPED: {exc}")
    except RuntimeError as exc:
        raise SystemExit(f"\n{exc}")


if __name__ == "__main__":
    main()
