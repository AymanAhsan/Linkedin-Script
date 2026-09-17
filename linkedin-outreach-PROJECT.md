# LinkedIn Outreach Tool — Project Spec

## Goal
A personal tool to automate LinkedIn connection requests + follow-up messages to
SWE/eng employees at specific target companies, for the purpose of getting referrals.
Not a product, not multi-tenant — built for one account (mine), low volume.

---

## Key Decisions

### 1. Scope: personal tool, not a SaaS product
Skipping everything a multi-tenant tool like Expandi/Waalaxy needs (per-user dedicated
IPs, billing, reverse-engineered API fallback, ban-avoidance infra for thousands of
accounts). At low volume (targeting specific companies, ~5-15 connects/day) this is a
weekend-scale project, not a startup.

### 2. Automation approach: Playwright on my real Chrome profile
Standard `chromium.launch()` gets fingerprinted and blocked fast (fresh context, no
cookie/history, `navigator.webdriver` flags, etc.). Launching Playwright with
`--user-data-dir` pointed at my actual Chrome profile inherits my real cookies,
extensions, fingerprint history, and session — much closer to a real logged-in human
browser than a fresh automated context.

**This reduces detection risk. It does not eliminate it.** LinkedIn's detection is
also behavioral, not just fingerprint-based — this matters regardless of which browser
identity is used:
- Action velocity (fixed-interval clicking is a signature)
- Volume/pattern over time (account-level anomaly detection across days/weeks)
- DOM interaction patterns (real humans scroll/hover irregularly)
- Checkpoint/CAPTCHA challenges triggered by risk score, independent of fingerprint

### 3. Claude in Chrome vs. standalone Playwright
Claude in Chrome only runs live during an active chat turn — no scheduling, no cron,
no unattended background runs. It's useful as a **dev tool** for prototyping LinkedIn
DOM selectors and flows interactively, but it can't be the persistent automation
itself.

**Decision:** Use Claude in Chrome to work out selectors/flows during development.
Build the actual persistent tool as a standalone Playwright CLI script pointed at my
real Chrome profile, runnable via cron with no live Claude session required.

### 4. State: SQLite, not a flat file
Need to query things like "connected 2+ days ago, not yet messaged" — trivial in SQL,
annoying in JSON once the queue passes ~20 prospects.

### 5. Targeting: company-based, not broad "ideal customer" search
Search LinkedIn (Sales Navigator if available, else standard search) filtered by
target company + title (e.g. "Software Engineer", "SWE Intern") rather than a generic
ICP search. Optionally filter toward CCNY/recent-grad overlap for warmer intros.

### 6. Sequence: single follow-up, not a multi-step drip
For referral asks, the pattern that works is: connect → wait for acceptance → one
personalized message a couple days later asking about their team/experience — not a
cold "can you refer me" ask, and not a multi-touch drip campaign (that's for sales
outreach, not referral asks).

### 7. Compliance line
This automates actions against LinkedIn's User Agreement — accepted personal risk,
same as the existing `ccny-linkedin-outreach` skill. Explicitly **not** building
anything to defeat CAPTCHAs/checkpoints — if one appears, the tool stops and I solve
it manually.

---

## Architecture

```
linkedin-outreach/
├── config.json          # search params, caps, message template
├── state.db             # SQLite: prospect queue + status
├── cli.py               # entry point
└── browser_actions.py   # Playwright wrappers (search, connect, message, check status)
```

### config.json (example)
```json
{
  "target_companies": ["Stripe", "Datadog", "Ramp"],
  "titles": ["Software Engineer", "SWE Intern"],
  "daily_connect_cap": 8,
  "wait_days_before_message": 2,
  "message_template": "Hi {first_name}, I noticed you're on the {company} eng team — I'm a CS student at CCNY..."
}
```

### state.db schema (prospects table)
- `id`, `name`, `profile_url`, `company`, `title`
- `status`: `queued` → `connection_sent` → `connected` → `messaged` → `replied`
- `connected_at`, `messaged_at` timestamps

### CLI commands
```bash
python cli.py search          # run LinkedIn search from config, add new people to queue
python cli.py connect         # send today's batch of connection requests (respects cap)
python cli.py check-replies   # poll for accepted connections, queue follow-ups
python cli.py send-messages   # send due follow-up messages
python cli.py status          # show queue breakdown
```

---

## Ban-Avoidance Practices (non-negotiable, not optional polish)
- Conservative daily cap: 5-10 connection requests/day, well under LinkedIn's own soft limits
- Randomized delays between actions (seconds, jittered) — never fixed intervals
- Spread actions across the day, not batched
- Don't run on a fixed identical schedule every day (vary the cron time)
- Mix with normal manual LinkedIn usage on the same account/session
- Stop immediately on any CAPTCHA/checkpoint and resolve manually — never automate past one

---

## Build Order / Next Steps
1. Use Claude in Chrome to prototype and confirm selectors for: search results page,
   "Connect" button flow, connection-status check, messaging a 1st-degree connection
2. Port confirmed selectors into `browser_actions.py` using Playwright with
   `--user-data-dir` set to real Chrome profile
3. Build SQLite schema + `cli.py search` and `cli.py status` first (read-only, safest to test)
4. Add `cli.py connect` with hardcoded low cap (start at 3-5/day) and delay jitter
5. Add `cli.py check-replies` (poll connections list, diff against state)
6. Add `cli.py send-messages` with template personalization
7. Run manually for a week before considering cron; watch for any checkpoint prompts
