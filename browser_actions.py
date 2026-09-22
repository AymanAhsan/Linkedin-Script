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
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from cli import load_config
from db import add_prospects, find_queued_prospects, get_conn, mark_connection_sent, mark_messaged
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
CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/"


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

def _vanity_name(url: str) -> str | None:
    """The profile slug from /in/<slug>/, read from the post-redirect URL."""
    m = re.search(r"/in/([^/?#]+)", url)
    return unquote(m.group(1)) if m else None


_CONNECTION_CARD = '[componentkey^="ConnectionCard_"]'

# The connections list uses the same hashed-class markup as search results, but
# each card carries a stable componentkey ("ConnectionCard_<n>-<vanity>"), which
# is a far more reliable anchor than the class soup. Name is the first <p>; its
# next sibling is the headline. Picking headline by "first <span> in the card"
# looked right when checked by eye (images already loaded) but broke on a fresh
# load, where an unloaded avatar inserts a stray leading <span> and shifts
# everything — nextElementSibling of the name is stable regardless.
_CONNECTIONS_JS = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  return [...document.querySelectorAll('[componentkey^="ConnectionCard_"]')].flatMap((card) => {
    const link = card.querySelector('a[href*="/in/"]');
    const nameP = card.querySelector('p');
    if (!link || !nameP) return [];
    const ps = [...card.querySelectorAll('p')].map((p) => clean(p.innerText));
    const headlineText = clean(nameP.nextElementSibling?.innerText);
    const connectedP = ps.find((t) => /^Connected on/.test(t));
    return [{
      name: clean(nameP.innerText) || null,
      headline: /^Connected on/.test(headlineText) ? '' : headlineText,
      connected_on: connectedP ? connectedP.replace(/^Connected on /, '') : null,
      profile_url: link.href.split('?')[0],
    }];
  });
}
"""


def _parse_connected_on(text: str | None):
    """"September 19, 2026" -> date(2026, 9, 19); None if unparseable."""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%B %d, %Y").date()
    except ValueError:
        return None


async def get_connections(
    page, limit: int | None = None, stop_before=None
) -> dict[str, dict]:
    """Scrape /mynetwork/invite-connect/connections/, sorted "Recently added".

    Cards load lazily on genuine scroll input — a wheel event, not a jump to
    document.body.scrollHeight, which LinkedIn silently ignores here (unlike the
    search results pager, there is no "Next" control to click instead). Stops
    once `limit` people are collected, once every card on a scraped batch is
    older than `stop_before` (a `date`; the list is chronological so nothing
    further down can be newer), or after three scrolls in a row add nobody new.

    Returns a dict keyed by vanity name (LinkedIn's `check-replies` matching key
    per CLAUDE.md) with 'name', 'headline', 'connected_on' (e.g. "September 19,
    2026", LinkedIn's own display string, not parsed to a date) and
    'profile_url'.
    """
    await page.goto(CONNECTIONS_URL, wait_until="domcontentloaded")
    if _is_checkpoint(page.url):
        raise CheckpointError(f"Checkpoint hit while loading {CONNECTIONS_URL}.")

    await page.wait_for_selector(_CONNECTION_CARD, timeout=25000)
    # The infinite-scroll trigger only fires once the mouse has actually been
    # placed over the page; a wheel event at Playwright's default (0, 0) is
    # silently ignored, unlike a real user's cursor which is never there.
    await page.mouse.move(640, 400)

    collected: dict[str, dict] = {}

    async def _scrape_and_merge() -> bool:
        """Merge new cards; returns True once a card is older than stop_before."""
        cards = await page.evaluate(_CONNECTIONS_JS)
        past_boundary = False
        for u in cards:
            if stop_before is not None:
                d = _parse_connected_on(u["connected_on"])
                if d is not None and d < stop_before:
                    past_boundary = True
                    continue
            vanity = _vanity_name(u["profile_url"])
            if vanity and vanity not in collected:
                collected[vanity] = u
        return past_boundary and bool(cards)

    if await _scrape_and_merge():
        return collected

    stall = 0
    while stall < 3 and (limit is None or len(collected) < limit):
        before = len(collected)
        await page.mouse.wheel(0, 3000)
        await asyncio.sleep(random.uniform(1.0, 2.0))
        if await _scrape_and_merge():
            break
        stall = stall + 1 if len(collected) == before else 0

    if limit is not None and len(collected) > limit:
        collected = dict(list(collected.items())[:limit])
    return collected


async def _find_connect(page, vanity: str):
    """Locate this profile's own Connect control, or None if it has none.

    Connect is an <a componentkey="ConnectButton…" href="/preload/custom-invite/
    ?vanityName=<slug>"> — not a <button>. The profile top card has three layouts:
    Connect inline (1st/2nd), Connect after a Follow button (creator profiles),
    or Connect only inside the "More" menu (Follow is primary). The href is keyed
    to the slug so the "More profiles for you" sidebar, which has Connect links
    for other people, can't match; `main` + `:visible` excludes the hidden
    sticky-header copy that sits under the nav bar and swallows clicks.
    """
    attrs = f'[componentkey^="ConnectButton"][href*="vanityName={vanity}" i]'
    inline = page.locator(f"main a{attrs}:visible")
    more = page.locator("main").get_by_role("button", name="More", exact=True)

    # Hydration order isn't guaranteed; the More button is present in all three
    # layouts, so once it (or Connect) exists the top card has rendered.
    try:
        await inline.or_(more).first.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeoutError:
        return None

    if await inline.count():
        return inline.first

    # The button is visible before its handler attaches, so an early click can
    # be a silent no-op. Retry, but never click a menu that is already open.
    menu_item = page.locator(f'a[role="menuitem"]{attrs}')
    for _ in range(3):
        if await more.first.get_attribute("aria-expanded") != "true":
            await more.first.click()
        try:
            await menu_item.first.wait_for(state="visible", timeout=2500)
            return menu_item.first
        except PlaywrightTimeoutError:
            await asyncio.sleep(1)

    await page.keyboard.press("Escape")  # already connected/pending/etc.
    return None


async def connect_users(page, users: list[dict]) -> None:
    """Connect with a list of users on LinkedIn.

    The caller supplies the already-open page so connection requests reuse the
    current browser/context. Each user is a dict with keys 'profile_url' and
    optionally 'message'. Browser lifetime is owned by the caller.
    """
    for n, user in enumerate(users):
        profile_url = user["profile_url"]
        message = user.get("message", "")

        if n:
            await asyncio.sleep(random.uniform(20, 60))

        await page.goto(profile_url, wait_until="domcontentloaded")

        if _is_checkpoint(page.url):
            raise CheckpointError(f"Checkpoint hit while loading {profile_url}.")

        vanity = _vanity_name(page.url)
        connect = await _find_connect(page, vanity) if vanity else None
        if connect is None:
            print(f"Could not find Connect button for {profile_url}")
            continue

        await connect.click()

        # The invite dialog is rendered in a shadow root; Playwright's role and
        # CSS locators pierce it. Step 1 offers "Add a note" / "Send without a
        # note"; the note textarea and "Send invitation" only exist after Add.
        if message:
            await page.get_by_role("button", name="Add a note").click()
            await page.locator("textarea[name='message']").fill(message)
            send = page.get_by_role("button", name="Send invitation")
        else:
            send = page.get_by_role("button", name="Send without a note")

        try:
            await send.click(timeout=8000)
        except Exception:
            print(f"Could not find Send button for {profile_url}")
            continue
        mark_connection_sent(user["id"])
        print(f"Sent connection request to {profile_url}")

_COMPANY_CHIP = 'main a[href*="/company/"]'
_MESSAGE_TEXT = re.compile(r"^Message$")
_COMPOSE_BOX = ".msg-form__contenteditable"


async def _current_company(page) -> str:
    """Scrape the profile top card's "current company" chip.

    This is a distinct, structured element (aria-label "View company: <Name>"),
    unlike the free-text headline where company isn't cleanly separable. It's
    always the first `/company/` link in DOM order -- later ones belong to the
    Experience section further down the page.
    """
    chip = page.locator(_COMPANY_CHIP).first
    if not await chip.count():
        return ""
    label = await chip.get_attribute("aria-label")
    if label and label.startswith("View company: "):
        return label[len("View company: "):]
    return (await chip.inner_text()).strip()


async def _find_message(page):
    """Locate this profile's own Message control, or None if it has none.

    Message is an <a>, not a <button>, with accessible name exactly "Message" --
    matched exactly so this never picks up the unrelated "Message with Premium"
    upsell link elsewhere on the page. As with Connect, there's a hidden
    sticky-header duplicate outside `main` and another hidden copy inside it;
    `main` + `:visible` leaves exactly the real one.
    """
    link = page.locator("main a:visible", has_text=_MESSAGE_TEXT)
    try:
        await link.first.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeoutError:
        return None
    return link.first


async def message_users(page, users: list[dict], message_template: str) -> None:
    """Send the one personalized follow-up message to each connected user.

    The caller supplies the already-open page so messages reuse the current
    browser/context. Each user is a dict with keys 'id', 'name', 'profile_url'.
    Browser lifetime is owned by the caller.
    """
    for n, user in enumerate(users):
        profile_url = user["profile_url"]

        if n:
            await asyncio.sleep(random.uniform(20, 60))

        await page.goto(profile_url, wait_until="domcontentloaded")

        if _is_checkpoint(page.url):
            raise CheckpointError(f"Checkpoint hit while loading {profile_url}.")

        company = await _current_company(page)
        first_name = user["name"].split()[0]
        message = message_template.format(first_name=first_name, company=company)

        link = await _find_message(page)
        if link is None:
            print(f"Could not find Message button for {profile_url}")
            continue

        await link.click()

        # The compose overlay is rendered in an open shadow root (a site-wide
        # messaging widget); Playwright locators pierce it transparently, same as
        # the invite-note dialog above.
        box = page.locator(_COMPOSE_BOX)
        try:
            await box.wait_for(state="visible", timeout=10000)
            await box.fill(message)
            await page.get_by_role("button", name="Send", exact=True).click(timeout=8000)
        except Exception:
            print(f"Could not send message for {profile_url}")
            continue

        mark_messaged(user["id"], company)
        print(f"Sent message to {profile_url}")


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

        print("Would you like to continue the sequence? (y/n): ", end="")
        choice = await asyncio.to_thread(input)
        if choice.lower() == "y":
            await find_queued_prospects()
        else:
            print("Sequence aborted. You can run the 'continue' command later to connect with queued users.")
        await asyncio.Event().wait()
    finally:
        await browser.close()



    
