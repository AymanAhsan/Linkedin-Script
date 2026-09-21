"""Playwright wrappers for driving LinkedIn from a saved session.

Auth is a storage_state JSON file (cookies + localStorage), not a Chrome
user-data-dir. That keeps the automation off your real profile entirely: it
runs in a throwaway temp profile, so your normal Chrome can stay open, and
nothing depends on Windows DPAPI-encrypted profile internals.

The session is seeded once by `save_login`, where you sign in by hand.
Scripted credential entry is what LinkedIn fingerprints hardest — it blocks
the login page far more aggressively than ordinary browsing — so that step
stays manual on purpose.
"""

import asyncio
import random
from pathlib import Path
from cli import load_config
from db import add_prospects, get_conn
from env_config import STORAGE_STATE

# Pinned so every run presents the same device. Randomizing these per run is a
# stronger signal than a boring stable fingerprint: real machines don't change
# screen size or timezone daily.
CONTEXT_KWARGS = {
    "viewport": {"width": 1920, "height": 1080},
    "locale": "en-US",
    "timezone_id": "America/New_York",
}

# Drops the navigator.webdriver tell. Deliberately the only flag here —
# --no-sandbox, --disable-web-security and friends are each fingerprintable in
# their own right, so adding them makes us more identifiable, not less.
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]

LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"


class CheckpointError(RuntimeError):
    """LinkedIn served a CAPTCHA or security checkpoint.

    Always fatal. Solve it by hand in a normal browser, then re-run
    `save_login` to refresh the session. Never automate past this, and treat
    it as a signal to lower the daily cap rather than to resume at the same
    rate — a warning ignored is how a warning becomes a restriction.
    """


def _is_checkpoint(url: str) -> bool:
    return "checkpoint" in url or "challenge" in url


async def launch(playwright, headless: bool = False):
    """Open a browser + context carrying the saved session.

    Returns (browser, context). No user_data_dir is passed, so Playwright
    creates a temp profile per run and never contends with your real Chrome.
    """
    if not STORAGE_STATE.exists():
        raise RuntimeError(
            f"No saved session at {STORAGE_STATE}.\n"
            "Run `python cli.py login` once to sign in and create it."
        )

    browser = await playwright.chromium.launch(
        channel="chrome",  # real Chrome, not Playwright's bundled Chromium
        headless=headless,
        args=LAUNCH_ARGS,
    )
    context = await browser.new_context(
        storage_state=str(STORAGE_STATE), **CONTEXT_KWARGS
    )
    return browser, context


async def assert_logged_in(page) -> None:
    """Fail loudly if the session has expired. Never re-auths on its own."""
    await page.goto(FEED_URL, wait_until="domcontentloaded")

    if _is_checkpoint(page.url):
        raise CheckpointError(
            f"LinkedIn served a checkpoint at {page.url}. Solve it manually in "
            "your normal browser, then re-run `python cli.py login`."
        )
    if "/login" in page.url or "/uas/login" in page.url:
        raise RuntimeError(
            "Saved session has expired. Re-run `python cli.py login`."
        )


async def save_login(playwright) -> None:
    """One-time interactive sign-in; writes STORAGE_STATE.

    Headed, on its own temp profile, so you do not need to close Chrome. You
    type the credentials and any 2FA code yourself — real keystrokes, not a
    scripted form fill.
    """
    browser = await playwright.chromium.launch(
        channel="chrome", headless=False, args=LAUNCH_ARGS
    )
    context = await browser.new_context(**CONTEXT_KWARGS)
    page = await context.new_page()
    await page.goto(LOGIN_URL)

    print(
        "\nA Chrome window is open. Sign in to LinkedIn by hand, finish any 2FA,\n"
        "and wait until you land on the feed.\n"
    )
    await asyncio.to_thread(input, "Press Enter here once you're signed in... ")

    if _is_checkpoint(page.url):
        await browser.close()
        raise CheckpointError(
            f"Still sitting on a checkpoint ({page.url}). Session not saved."
        )

    await context.storage_state(path=str(STORAGE_STATE))
    await browser.close()
    print(f"Session saved to {STORAGE_STATE}")
    print("This file is your logged-in account — keep it out of version control.")

async def connect_users(page, users: list[dict]) -> None:
    """Connect with a list of users on LinkedIn.

    The caller supplies the already-open page so connection requests reuse the
    current browser/context. Each user is a dict with keys 'profile_url' and
    optionally 'message'. Browser lifetime is owned by the caller.
    """
    for user in users:
        profile_url = user["profile_url"]
        message = user.get("message", "")

        await page.goto(profile_url, wait_until="domcontentloaded")

        if _is_checkpoint(page.url):
            raise CheckpointError(f"Checkpoint hit while loading {profile_url}.")

        # Click the Connect button
        connect_button = await page.query_selector("button[aria-label='Connect']")
        if connect_button:
            await connect_button.click()
            # If there's a message box, fill it in
            message_box = await page.query_selector("textarea[name='message']")
            if message_box and message:
                await message_box.fill(message)
            # Click the Send button
            send_button = await page.query_selector("button[aria-label='Send now']")
            if send_button:
                await send_button.click()
                print(f"Sent connection request to {profile_url}")
            else:
                print(f"Could not find Send button for {profile_url}")
        else:
            print(f"Could not find Connect button for {profile_url}")

# LinkedIn's search UI is server-driven with hashed class names, so this keys off
# ARIA roles and semantic attributes only. Card links are nested inside each
# other and mutual-connection avatars also link to /in/, so everything is read
# from the listitem: the person's own link is the first /in/ link inside a <p>,
# and headline/location are the next two <p> after it.
_SEARCH_RESULTS_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  return [...document.querySelectorAll('main [role="listitem"]')].flatMap((li) => {
    const nameLink = li.querySelector('p a[href*="/in/"]');
    if (!nameLink) return [];
    const ps = [...li.querySelectorAll('p')];
    const i = ps.indexOf(nameLink.closest('p'));
    const degree = clean(ps[i].innerText.replace(nameLink.innerText, ''))
      .match(/(1st|2nd|3rd\\+?)/)?.[1] ?? null;
    const action = li.querySelector('a[componentkey^="ConnectButton"]');
    return [{
      name: clean(nameLink.innerText),
      profile_url: nameLink.href.split('?')[0],
      degree,
      headline: clean(ps[i + 1]?.innerText),
      location: clean(ps[i + 2]?.innerText),
      connect_state: action?.getAttribute('componentkey').match(/_(\\w+)$/)?.[1] ?? null,
    }];
  });
}
"""


_RESULT_CARD = 'main [role="listitem"]'
DEBUG_DIR = Path(__file__).with_name("debug")


def _dbg(msg: str) -> None:
    print(f"[get_users] {msg}")


async def _dump_debug(page, tag: str) -> None:
    """Save a screenshot + HTML so an empty result can be inspected by eye."""
    DEBUG_DIR.mkdir(exist_ok=True)
    await page.screenshot(path=str(DEBUG_DIR / f"{tag}.png"))
    (DEBUG_DIR / f"{tag}.html").write_text(await page.content(), encoding="utf-8")
    _dbg(f"saved {DEBUG_DIR / tag}.png and .html")


_CONNECT_ACTION = 'main [role="listitem"] a[componentkey^="ConnectButton"]'


async def _settle_actions(page) -> None:
    """Wait for the Connect/Pending controls to finish rendering.

    They hydrate a beat after the cards themselves, so reading right after the
    cards appear returns connect_state=None for everyone. Polls until the count
    is non-zero and unchanged across two polls, capped at ~5s (1st-degree-only
    pages have no controls, so zero is a legitimate final answer).
    """
    last = -1
    for _ in range(10):
        n = await page.locator(_CONNECT_ACTION).count()
        if n and n == last:
            break
        last = n
        await asyncio.sleep(0.5)
    _dbg(f"{last} connect/pending controls rendered")


_NEXT_BUTTON = (
    'main button[aria-label="Next"], '
    'main button:has-text("Next"), '
    'main a[aria-label="Next"]'
)


def _blocked(url: str) -> bool:
    return (
        _is_checkpoint(url)
        or "/login" in url
        or "/uas/" in url
        or "authwall" in url
    )


async def _scrape_page(page) -> list[dict]:
    """Extract every person on the current search results page."""
    try:
        await page.wait_for_selector(_RESULT_CARD, timeout=25000)
    except Exception as e:
        _dbg(f"no cards: {type(e).__name__} waiting for {_RESULT_CARD!r}")
        _dbg(f"url now {page.url}")
        await _dump_debug(page, "get_users_empty")
        return []

    cards = await page.locator(_RESULT_CARD).count()
    await _settle_actions(page)
    users = await page.evaluate(_SEARCH_RESULTS_JS)
    _dbg(f"{cards} cards on page, {len(users)} extracted")
    if len(users) != cards:
        _dbg(
            f"MISMATCH: {cards - len(users)} card(s) had no /in/ name link "
            "(ad/promo card, or LinkedIn changed the markup)"
        )
    if not users:
        await _dump_debug(page, "get_users_empty")
    return users


async def _go_next_page(page) -> bool:
    """Click 'Next' on the results pager. False when there is no further page."""
    # The pager sits below the fold and may not render until scrolled to.
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(random.uniform(1.0, 2.0))

    next_btn = page.locator(_NEXT_BUTTON).first
    if not await next_btn.count() or not await next_btn.is_enabled():
        return False

    before = page.url
    await next_btn.click()
    try:
        await page.wait_for_url(lambda u: u != before, timeout=15000)
    except Exception:
        _dbg("URL did not change after clicking Next")
        return False
    return True


async def get_users(page) -> list[dict]:
    """Collect people from LinkedIn search results, paging until enough are found.

    Reads the target from config["find_users"]; when unset, only the current page
    is scraped. Otherwise keeps scraping and clicking 'Next' until that many
    unique people are collected or the results run out. Returns at most that many
    dicts with keys 'name', 'profile_url', 'degree' ('1st'/'2nd'/'3rd+'),
    'headline', 'location' and 'connect_state' ('connect', 'pending', or None
    when there is no Connect control, e.g. 1st degree). Company is not a separate
    field on the page; it lives in the free-text headline.
    """
    target = load_config().get("find_users")
    _dbg(f"target={target if target is not None else 'current page only'}")

    collected: dict[str, dict] = {}
    page_no = 1
    while True:
        _dbg(f"--- page {page_no}: url={page.url}")
        if _blocked(page.url):
            _dbg("on a login/checkpoint page, not search results")
            await _dump_debug(page, "get_users_blocked")
            break

        new = 0
        for u in await _scrape_page(page):
            if u["profile_url"] not in collected:
                collected[u["profile_url"]] = u
                new += 1
        _dbg(f"{new} new, {len(collected)} total")

        if target is None or len(collected) >= target:
            break
        if not new:
            _dbg("page added nobody new; stopping")
            break
        if not await _go_next_page(page):
            _dbg(f"no more pages; got {len(collected)} of {target}")
            break

        page_no += 1
        await asyncio.sleep(random.uniform(2.0, 5.0))

    users = list(collected.values())
    if target is not None:
        users = users[:target]

    for n, u in enumerate(users, 1):
        empty = [k for k in ("name", "profile_url", "headline", "location") if not u[k]]
        flag = f"  <-- EMPTY: {', '.join(empty)}" if empty else ""
        _dbg(
            f"{n:>2}. {u['name']!a} | {u['degree']} | {u['connect_state']} | "
            f"{u['headline'][:50]!a} | {u['profile_url']}{flag}"
        )
    return users

def queue_users(users: list[dict], db_path: str = "state.db") -> None:
    """Add scraped users to the prospect queue (duplicates by URL are ignored)."""
    prospects = [
        (
            user["name"],
            user["profile_url"],
            None,  # Company is not a separate field on the page
            user["headline"],  # Using headline as title
        )
        for user in users
    ]
    conn = get_conn(db_path)
    try:
        add_prospects(conn, prospects)
    finally:
        conn.close()


async def run(
    playwright, url: str, users: list[dict] | None = None
) -> None:
    """Open `url` with the saved session and hold the window open."""
    browser, context = await launch(playwright)

    try:
        page = await context.new_page()
        await assert_logged_in(page)
        await page.goto(url, wait_until="domcontentloaded")

        if _is_checkpoint(page.url):
            raise CheckpointError(f"Checkpoint hit while loading {url}.")

        # Reuse the page opened for search; connect_users does not launch or
        # close a browser of its own.
        users = await get_users(page)
        queue_users(users)

        print("Signed in. Press Ctrl+C in the terminal to close.")
        await asyncio.Event().wait()
    finally:
        await browser.close()
