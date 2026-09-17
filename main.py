
import asyncio
from browser_actions import run
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as playwright:
        await run(playwright)

if __name__ == "__main__":
    asyncio.run(main())