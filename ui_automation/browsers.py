"""Making sure every browser a run needs is installed (Chromium, Firefox, WebKit), installing what is missing."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime
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


def irish_today() -> date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Dublin")).date()
    except Exception:  # noqa: BLE001 - no time zone data (some Windows installs): the computer's date
        return date.today()


def browsers_for_run(browsers: tuple[str, ...] | list[str], rotation: str, today: date | None = None) -> list[str]:
    """The browsers this run uses. With browser_rotation: daily, one of them, taking turns by the day,
    unless WEB_UI_ALL_BROWSERS=1 (the --all-browsers option) asks for all of them."""
    names = list(browsers)
    if rotation != "daily" or len(names) < 2 or os.environ.get("WEB_UI_ALL_BROWSERS") == "1":
        return names
    day = today or irish_today()
    return [names[day.toordinal() % len(names)]]
