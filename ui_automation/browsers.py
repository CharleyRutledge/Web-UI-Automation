"""Making sure every browser a run needs is installed (Chromium, Firefox, WebKit), installing what is missing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def missing_browsers(names: list[str]) -> list[str]:
    """The browsers in `names` whose Playwright build is not on this computer."""
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    missing: list[str] = []
    try:
        with sync_playwright() as p:
            for name in names:
                try:
                    if Path(getattr(p, name).executable_path).exists():
                        continue
                    # Not where expected: headless runs can use a separate build, so try starting it.
                    getattr(p, name).launch(headless=True).close()
                except PlaywrightError as exc:
                    if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
                        missing.append(name)
                except AttributeError:
                    missing.append(name)
    except PlaywrightError:
        return []  # Playwright itself cannot start: the run reports that clearly on its own
    return missing


def ensure_browsers(names: list[str]) -> str:
    """Install the missing ones with Playwright's own installer. '' when all are there, else what went wrong."""
    missing = missing_browsers(list(dict.fromkeys(names)))
    if not missing:
        return ""
    print(f"Installing {', '.join(missing)} for this run (a one-time download) ...", flush=True)
    done = subprocess.run([sys.executable, "-m", "playwright", "install", *missing], check=False)
    if done.returncode != 0:
        return (f"Could not install {', '.join(missing)}. Run: python -m playwright install {' '.join(missing)}"
                + (" (on Linux add --with-deps)" if sys.platform.startswith("linux") else ""))
    return ""
