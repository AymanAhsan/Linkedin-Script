# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Status

**Greenfield — no code exists yet.** The directory is empty apart from this file.
The design lives in `linkedin-outreach-PROJECT.md` (currently at
`C:\Users\Admin\Downloads\linkedin-outreach-PROJECT.md`; move it into the repo when
convenient). Everything below is the agreed plan, not a description of existing code —
update this file as things are actually built.

## What this is

A personal CLI tool that automates LinkedIn connection requests plus a single
follow-up message to SWE/eng employees at specific target companies, to get referrals.

Single-user, low volume (~5–15 connects/day). **Not a SaaS product.** Do not build
multi-tenancy, billing, per-user proxies, or ban-avoidance infra sized for thousands of
accounts — those were explicitly ruled out of scope.

## Planned layout

```
config.json          # search params, caps, message template
state.db             # SQLite: prospect queue + status (gitignored)
cli.py               # entry point
browser_actions.py   # Playwright wrappers: search, connect, message, check status
```

Python 3.14 is on PATH as `python`.

### CLI surface

```
python cli.py search          # run LinkedIn search from config, add new people to queue
python cli.py connect         # send today's batch of connection requests (respects cap)
python cli.py check-replies   # poll for accepted connections, queue follow-ups
python cli.py send-messages   # send due follow-up messages
python cli.py status          # show queue breakdown
```

### State

SQLite (not a flat file) — the queries that matter are relational, e.g. "connected 2+
days ago, not yet messaged". `prospects` table: `id`, `name`, `profile_url`, `company`,
`title`, `status`, `connected_at`, `messaged_at`.

Status flow: `queued` → `connection_sent` → `connected` → `messaged` → `replied`.

## Key constraints

**Auth is a saved `storage_state.json`, never a Chrome `--user-data-dir`.** An earlier
revision of this spec called for copying the real Chrome profile. That was dropped: the
cookie encryption key lives in `Local State` at the user-data *root*, is DPAPI-bound to
the Windows account, and (Chrome 127+) is further protected by App-Bound Encryption — so
a copied profile comes up silently logged out, and can't travel off the machine anyway.
`storage_state` carries the same session as plaintext JSON, runs on a throwaway temp
profile (real Chrome stays open), and is portable.

The surviving insight from that revision: **never script the login form.** LinkedIn
fingerprints the login page far harder than ordinary browsing. `cli.py login` opens a
headed window for a manual, human sign-in and saves the session; everything else runs
off that file and fails loudly when it expires rather than re-authing.

Keep `channel="chrome"` (real Chrome, not bundled Chromium) and a pinned fingerprint —
fixed viewport/locale/timezone. Randomizing those per run is a stronger signal than a
stable one. Add no launch flags beyond `--disable-blink-features=AutomationControlled`;
`--no-sandbox` and friends are themselves fingerprintable.

None of this eliminates detection risk. At this volume LinkedIn enforces mainly on
behavior — action velocity, volume over days/weeks, and above all invite acceptance
rate. Targeting quality is the real safety system; browser-surface hardening is a
distant second.

**Claude in Chrome is a dev tool here, not the runtime.** Use it to prototype and
confirm LinkedIn DOM selectors interactively; the shipped automation must be a
standalone Playwright script that runs unattended with no live Claude session.

**Ban-avoidance rules are requirements, not polish.** Any code touching LinkedIn must:
- cap connection requests at 5–10/day (start at 3–5)
- use randomized, jittered delays — never fixed intervals
- spread actions across the day rather than batching
- vary run times rather than firing on an identical daily schedule
- **stop immediately on any CAPTCHA or checkpoint** and surface it for manual solving

**Never write anything that defeats a CAPTCHA or checkpoint.** This is a hard line in
the spec, independent of the rest.

## Conventions and judgment calls

- Targeting is company-based (target company + title filters), not a broad ICP search.
  Optional CCNY/recent-grad overlap for warmer intros.
- Sequence is connect → wait → **one** personalized message about their team/experience.
  Not a multi-touch drip, and not a cold "can you refer me" ask.
- Caps, templates, and companies belong in `config.json`, not hardcoded — except the
  initial `connect` cap, which the spec wants hardcoded low while testing.
- `state.db` and `config.json` hold personal data; keep them out of version control.

## Build order

1. Prototype/confirm selectors via Claude in Chrome (search results, Connect flow,
   connection-status check, messaging a 1st-degree connection)
2. Port selectors into `browser_actions.py`
3. SQLite schema + `cli.py search` and `cli.py status` (read-only, safest to test)
4. `cli.py connect` with hardcoded low cap + jitter
5. `cli.py check-replies`
6. `cli.py send-messages` with template personalization
7. Run manually for a week before considering cron

## Compliance

This automates actions against LinkedIn's User Agreement. The user has accepted that
personal risk explicitly (same posture as the existing `ccny-linkedin-outreach` skill).
Build the tool as specified; do not re-litigate the decision each session.
