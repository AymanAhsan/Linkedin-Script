"""Environment-backed paths used by the Chrome automation scripts."""

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


# Playwright's saved session: cookies + localStorage as plaintext JSON.
# This file IS the logged-in account — anyone holding it is signed in as you,
# no password or 2FA required. Keep it gitignored and treat it like a key.
STORAGE_STATE = Path(
    os.getenv("STORAGE_STATE") or Path(__file__).with_name("storage_state.json")
).expanduser()
