# LinkedIn Outreach CLI

A personal, single-user CLI that automates LinkedIn connection requests plus a
single follow-up message to SWE/eng employees at specific target companies, to
get referrals.

Low volume by design (single-digit connects/day). Not a SaaS product — no
multi-tenancy, no billing, no per-user proxies. See [CLAUDE.md](CLAUDE.md) for
the full design rationale and constraints.

## Quick start

1. **Install dependencies**

   ```bash
   pip install playwright python-dotenv pytest
   playwright install chrome
   ```

2. **Sign in once, by hand**

   ```bash
   python cli.py login
   ```

   Opens a headed Chrome window. Sign in and complete any 2FA yourself —
   the login form is never scripted, since LinkedIn fingerprints automated
   sign-ins far more aggressively than ordinary browsing. On success this
   writes `storage_state.json` (cookies + localStorage), which every other
   command reuses. That file *is* your logged-in session — keep it out of
   version control and treat it like a credential.

3. **Configure targeting**

   Create `config.json` in the repo root (gitignored):

   ```json
   {
     "target_companies": ["Acme Corp"],
     "titles": ["Software Engineer", "SWE Intern"],
     "daily_connection_limit": 8,
     "daily_message_limit": 1,
     "wait_days_before_message": 2,
     "find_users": 5,
     "message_template": "Hi {first_name}, I noticed you're on the {company} eng team — ..."
   }
   ```

4. **Run the daily sequence**

   ```bash
   python cli.py daily-sequence
   ```

   Runs search → connect → check-replies → send-messages in order, each
   respecting the caps in `config.json`.

## CLI surface

| Command | What it does |
|---|---|
| `python cli.py login` | One-time manual sign-in; saves the session to `storage_state.json`. |
| `python cli.py search` | Opens the configured LinkedIn search interactively and queues new prospects. |
| `python cli.py daily-sequence` | Runs the full workflow: collect prospects → send connection requests → check for accepted invites → send due follow-ups. |
| `python cli.py continue` | Sends connection requests to queued prospects (respects the daily cap). |
| `python cli.py check-replies` | Scans your Connections page and promotes accepted invites to `connected`. |
| `python cli.py send-messages` | Sends the one follow-up message to connections that are past `wait_days_before_message`. |
| `python cli.py view_queue [--status STATUS ...]` | Prints the local prospect queue as a table; repeat `--status` to filter. |

## How it works

- **Browser automation**: [browser_actions.py](browser_actions.py) wraps
  Playwright, driving a real Chrome install (`channel="chrome"`) with a
  pinned fingerprint (fixed viewport/locale/timezone) rather than the
  bundled Chromium. Auth rides on `storage_state.json`, not a copied Chrome
  profile — profile copying was ruled out because Chrome's cookie
  encryption is DPAPI-bound to the Windows account and, from Chrome 127+,
  further protected by App-Bound Encryption.
- **State**: [db.py](db.py) is a small SQLite layer (`state.db`, gitignored)
  tracking each prospect through a status pipeline:

  ```
  queued → connection_sent → connected → messaged → replied
  ```

  Daily caps are enforced by counting rows already timestamped since
  midnight, not by an in-memory counter, so the cap survives restarts.
- **Search targeting**: [config.py](config.py) builds a LinkedIn people-search
  URL by ORing every `title × company` combination from `config.json`.
- **Safety stop**: any CAPTCHA or security checkpoint raises
  `browser_actions.CheckpointError` and halts immediately — the tool never
  attempts to solve or bypass one. Resolve it by hand in a normal browser,
  then re-run `python cli.py login`.

## Configuration reference (`config.json`)

| Key | Purpose |
|---|---|
| `target_companies` | List of company names to search for. |
| `titles` | List of job titles to search for; combined with companies via OR. |
| `daily_connection_limit` | Max connection requests sent per calendar day. |
| `daily_message_limit` | Max follow-up messages sent per calendar day. |
| `wait_days_before_message` | Days to wait after a connection is accepted before sending the follow-up. |
| `find_users` | How many search results to pull per `search`/`daily-sequence` run. |
| `message_template` | Follow-up message text; supports `{first_name}` / `{company}` placeholders. |

`config.json`, `state.db`, and `storage_state.json` all hold personal data and
are gitignored — never commit them.

## Testing

```bash
python -m pytest
```

Tests in [test/](test) cover URL building, prospect selection/cap logic, and
queue formatting without touching a real browser or account. Add new tests
alongside state-transition and non-browser logic first — browser-driving
tests should stay opt-in against a dedicated test account.

## Ban-avoidance constraints

This automates actions against LinkedIn's User Agreement, accepted as a known
personal risk. To keep the risk in check, any change touching LinkedIn must
keep:

- connection caps low (start at 3–5/day)
- randomized, jittered delays between actions — never fixed intervals
- actions spread across the day, not batched
- an immediate, hard stop on any CAPTCHA or checkpoint

See [CLAUDE.md](CLAUDE.md) for the complete list of constraints and the
reasoning behind each one.
