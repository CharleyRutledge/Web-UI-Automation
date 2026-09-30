from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, expect

from ui_automation.config import Settings

WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]


def _slug(label: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\-]+", "_", label.strip(), flags=re.UNICODE)
    s = s.strip("_") or "step"
    return s[:max_len]


class BasePage:
    """
    Base Page Object aligned with Playwright guidance:
    - Semantic locators (get_by_role, get_by_label, …)
    - Web-first assertions via expect()
    - Relative navigation with context base_url
    - No arbitrary sleeps; rely on auto-waiting
    """

    def __init__(
        self,
        page: Page,
        settings: Settings,
        request: pytest.FixtureRequest,
        artifacts_dir: Path,
    ) -> None:
        self.page = page
        self.settings = settings
        self._request = request
        self._artifacts_dir = artifacts_dir
        self._step_index = 0

    def locator_by_role(
        self,
        role: str,
        *,
        name: str | re.Pattern[str] | None = None,
        exact: bool = False,
    ) -> Locator:
        return self.page.get_by_role(role, name=name, exact=exact)

    def locator_by_label(self, text: str | re.Pattern[str], *, exact: bool = False) -> Locator:
        return self.page.get_by_label(text, exact=exact)

    def locator_by_test_id(self, test_id: str) -> Locator:
        return self.page.get_by_test_id(test_id)

    def step(self, label: str) -> None:
        """Record a human-readable step and capture a screenshot for the HTML report."""
        filename = f"{self._step_index:03d}_{_slug(label)}.png"
        self._step_index += 1
        if self.page.url == "about:blank":
            return  # nothing to capture yet; recent Chromium builds refuse to screenshot a blank page
        path = self._artifacts_dir / filename
        try:
            self.page.screenshot(
                path=str(path),
                full_page=self.settings.screenshot_full_page,
            )
        except PlaywrightError as exc:
            # A diagnostic screenshot must never fail the test itself.
            print(f"Step screenshot skipped for {label!r}: {exc.message.splitlines()[0]}")
            return
        self._request.node.step_screenshots.append((label, str(path)))

    def goto_path(self, path: str) -> None:
        path = path if path.startswith("/") else f"/{path}"
        self.step(f"Navigate to {path}")
        self.page.goto(
            path,
            wait_until=self.settings.navigation_wait_until,  # type: ignore[arg-type]
        )

    def click_role(
        self,
        role: str,
        *,
        name: str | re.Pattern[str] | None = None,
        exact: bool = False,
        step_label: str | None = None,
    ) -> None:
        if step_label:
            self.step(step_label)
        self.locator_by_role(role, name=name, exact=exact).click()

    def fill_label(
        self,
        label: str | re.Pattern[str],
        value: str,
        *,
        step_label: str | None = None,
    ) -> None:
        if step_label:
            self.step(step_label)
        self.locator_by_label(label).fill(value)

    def expect_visible(self, locator: Locator, *, step_label: str | None = None) -> None:
        if step_label:
            self.step(step_label)
        expect(locator).to_be_visible()

    def expect_title_contains(self, text: str) -> None:
        self.step(f"Assert title contains {text!r}")
        expect(self.page).to_have_title(
            re.compile(re.escape(text), re.IGNORECASE),
        )

    def expect_heading(self, name: str) -> None:
        self.expect_visible(
            self.locator_by_role("heading", name=name),
            step_label=f"Assert heading {name!r} is visible",
        )
