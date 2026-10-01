"""Public practice websites for UI automation (the list from placementpreparation.io).

These are other people's demo sites: a failure here is either a real bug on that site, a change to it,
or the site being down / blocking automated browsers. The message in the report says which.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, Response

from pages.base_page import BasePage
from ui_automation.blocking import blocked_reason
from ui_automation.config import Settings

SITES = {
    "the-internet": "https://the-internet.herokuapp.com/",
    "saucedemo": "https://www.saucedemo.com/",
    "practicetestautomation": "https://practicetestautomation.com/practice-test-login/",
    "expandtesting": "https://practice.expandtesting.com/",
    "uitestingplayground": "http://uitestingplayground.com/",
    "demoqa": "https://demoqa.com/",
    "automationexercise": "https://automationexercise.com/",
    "demoblaze": "https://www.demoblaze.com/",
    "orangehrm": "https://opensource-demo.orangehrmlive.com/",
    "parabank": "https://parabank.parasoft.com/parabank/index.htm",
    "testautomationpractice": "https://testautomationpractice.blogspot.com/",
}


class PracticeSite(BasePage):
    def goto_path(self, path: str) -> Response | None:
        self.step(f"Navigate to {path}")
        response = self.page.goto(path, wait_until=self.settings.navigation_wait_until)  # type: ignore[arg-type]
        reason = blocked_reason(response, self.page)
        if reason:
            self.step("Blocked by the site")
            pytest.skip(f"Not tested: {reason}")
        return response


@pytest.fixture
def site(page: Page, settings: Settings, request: pytest.FixtureRequest, test_artifacts_dir) -> PracticeSite:
    return PracticeSite(page, settings, request, test_artifacts_dir)
