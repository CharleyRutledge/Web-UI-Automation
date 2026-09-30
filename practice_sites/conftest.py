"""Public practice websites for UI automation (the list from placementpreparation.io).

These are other people's demo sites: a failure here is either a real bug on that site, a change to it,
or the site being down / blocking automated browsers. The message in the report says which.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, Response

from pages.base_page import BasePage
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



def blocked_reason(response: Response | None, page: Page) -> str:
    """Why the site refused to serve an automated browser, or '' when it did not.

    Shared CI runners are often rate-limited or challenged by anti-bot services. That says nothing about
    the site or the tests, so it is reported as a skip with the reason, never as a pass or a failure.
    Ordinary errors (404, 500, ...) are not blocks and still fail.
    """
    final = page.url
    if "google.com/sorry" in final:
        return f"Google's 'unusual traffic' check blocked this runner ({final.split('?')[0]})"
    if response is not None and response.status == 429:
        return f"the site rate-limited this runner (HTTP 429 at {response.url.split('?')[0]})"
    if response is not None and response.status in (403, 503):
        title = page.title().lower()
        if "just a moment" in title or "attention required" in title or "captcha" in title:
            return f"an anti-bot challenge blocked this runner (HTTP {response.status}, page '{page.title()}')"
    return ""


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
