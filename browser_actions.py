import asyncio
from playwright.async_api import async_playwright



async def run(playwright, url):

    # A copy of the real profile, NOT the default Chrome user-data-dir: Chrome
    # refuses remote debugging (which Playwright needs) against the default
    # profile path, so we drive this copy instead. Run setup_profile.py first
    # to seed it with your real session cookies — signing in from inside this
    # automated window gets blocked by LinkedIn/Google's login-page bot checks.
    user_data_dir = "C:\\Users\\Admin\\chrome-automation-profile"
    try:
        context = await playwright.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                channel="chrome",  # Forces Playwright to use your official Chrome installation
                args=["--profile-directory=Default"]  # Change to "Profile 1" if not using Default
            )
    except Exception as exc:
        if "Connection closed while reading from the driver" in str(exc):
            raise RuntimeError(
                "Could not launch Chrome against your real profile because Chrome is "
                "already running with it. Quit Chrome completely (all windows) and "
                "run this script again."
            ) from exc
        raise
    page = context.pages[0] if context.pages else await context.new_page()
    
    await page.goto(url)
    
    # Keep the browser open for interaction
    print("Browser is open with your settings. Press Ctrl+C in the terminal to close.")
    await asyncio.Event().wait() 
    
    await context.close()