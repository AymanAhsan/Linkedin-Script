
import asyncio
from browser_actions import run
from playwright.async_api import async_playwright
import json
from config import link_builder


async def main():
    async with async_playwright() as playwright:
        with open("config.json") as f:
            config = json.load(f)
        url = link_builder(config["target_companies"], config["titles"], connected=config.get("connected", False))
        await run(playwright, url)

if __name__ == "__main__":
    asyncio.run(main())