Repository Guidelines

## Project Structure & Module Organization

This repository is a small, single-account Python CLI for LinkedIn outreach. The
planned top-level layout is:

- `cli.py` — command-line entry point (`search`, `connect`, `check-replies`,
  `send-messages`, and `status`).
- `browser_actions.py` — Playwright browser operations and LinkedIn selectors.
- `config.json` — target companies, titles, caps, delays, and message template.
- `state.db` — local SQLite prospect state; do not commit it.
- `tests/` — automated tests, especially for state transitions and non-browser
  logic.

Keep browser interaction isolated from persistence and CLI orchestration. Add
supporting modules only when they have a clear responsibility.

## Build, Test, and Development Commands

Run commands from the repository root:

```bash
python cli.py status
python cli.py search
python -m pytest
python -m compileall .
```

`status` is a safe smoke test; `search` reads LinkedIn and updates the queue;
`pytest` runs the test suite; `compileall` catches Python syntax errors. Run
outreach actions manually during development—do not schedule them until the
flows have been validated.

## Coding Style & Naming Conventions

Use Python 3, four-space indentation, and PEP 8 naming: `snake_case` for
functions, variables, and modules; `PascalCase` for classes; and uppercase names
for constants. Prefer type hints, small functions, and explicit error handling.
Format or lint with the repository-configured tools when they are added (for
example, `ruff format .` and `ruff check .`). Keep selectors and Playwright
wrappers in `browser_actions.py`, not in command handlers.

## Testing Guidelines

Use `pytest`; name files `test_*.py` and tests `test_<behavior>`. Unit-test SQLite
schema changes, queue/status transitions, cap enforcement, delay calculation,
and message personalization without opening a browser. Browser tests should be
opt-in and use a dedicated test account or mocked pages. Never test against a
real account by default.

## Security & Configuration

Never commit Chrome profile paths, cookies, credentials, message recipients, or
`state.db`. Keep local settings in an ignored file or environment variables.
If LinkedIn shows a CAPTCHA or checkpoint, stop and resolve it manually; do not
add code intended to bypass security controls.

## Commits & Pull Requests

No commit history exists yet, so no established convention can be inferred. Use
short, imperative subjects such as `Add SQLite prospect queue`, and keep each
commit focused. Pull requests should explain behavior changes, include test
commands/results, document configuration changes, and call out any browser-flow
risk. Do not include personal account data or screenshots containing private
profiles.
