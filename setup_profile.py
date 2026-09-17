"""
One-time setup: seed chrome-automation-profile with a real, logged-in Chrome
profile so Playwright never has to drive a LinkedIn (or Google) sign-in itself.

Signing in from inside a Playwright-controlled window is what triggers the
"browser type" rejection — LinkedIn/Google fingerprint the DevTools protocol
connection and block login attempts specifically, much more aggressively than
they block normal browsing. Copying over an already-authenticated profile
sidesteps that: the automated browser opens already signed in.

Run this again any time you want to refresh the session (e.g. after LinkedIn
logs you out or you 2FA'd again in your real browser).

Usage:
    python setup_profile.py
    python setup_profile.py --profile "Profile 1"
"""

import argparse
import subprocess
import sys
from pathlib import Path

CHROME_USER_DATA = Path(r"C:\Users\Admin\AppData\Local\Google\Chrome\User Data")
AUTOMATION_PROFILE_ROOT = Path(r"C:\Users\Admin\chrome-automation-profile")

# Directories that are large, purely cache, and safe to skip.
EXCLUDE_DIRS = [
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnCache",
    "DawnGraphiteCache",
    "GrShaderCache",
    "ShaderCache",
    "Crashpad",
    "blob_storage",
]

# Files that must NOT come along: they encode the PID/session of the browser
# that created them and confuse Chrome into thinking it's already running.
EXCLUDE_FILES = [
    "Singleton*",
    "lockfile",
    "*.tmp",
]


def chrome_is_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
        capture_output=True,
        text=True,
    )
    return "chrome.exe" in result.stdout.lower()


def copy_profile(profile_name: str) -> int:
    source = CHROME_USER_DATA / profile_name
    dest = AUTOMATION_PROFILE_ROOT / profile_name

    if not source.exists():
        print(f"Source profile not found: {source}")
        return 1

    dest.mkdir(parents=True, exist_ok=True)

    cmd = [
        "robocopy",
        str(source),
        str(dest),
        "/E",       # copy subdirectories, including empty ones
        "/XJ",      # skip junctions (avoids self-referential loops)
        "/R:1",     # retry once on a locked file, don't hang
        "/W:1",
        "/NFL", "/NDL", "/NJH",  # quiet: no file/dir lists or job header
    ]
    for d in EXCLUDE_DIRS:
        cmd += ["/XD", str(source / d)]
    for f in EXCLUDE_FILES:
        cmd += ["/XF", f]

    result = subprocess.run(cmd)
    # robocopy exit codes 0-7 are success variants; 8+ means real failure.
    return 0 if result.returncode < 8 else result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default="Default",
        help="Chrome profile folder name to copy (default: 'Default'). "
             "Must match --profile-directory in browser_actions.py.",
    )
    args = parser.parse_args()

    if chrome_is_running():
        print(
            "Chrome is currently running. Quit it completely (all windows, "
            "check the system tray too) and run this script again — the "
            "profile directory is locked while Chrome has it open."
        )
        sys.exit(1)

    print(f"Copying profile '{args.profile}' into {AUTOMATION_PROFILE_ROOT} ...")
    code = copy_profile(args.profile)
    if code != 0:
        print(f"robocopy failed (exit code {code}).")
        sys.exit(code)

    print("Done. Your automation profile now carries your real session cookies.")
    print("Run the script (e.g. python main.py) — it should open already signed in.")


if __name__ == "__main__":
    main()
