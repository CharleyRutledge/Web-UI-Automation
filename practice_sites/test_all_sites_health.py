"""Every site: it loads, answers with a success status, has a title, and its WCAG 2.1 AA scan is reported."""

from __future__ import annotations

import pytest

from practice_sites.conftest import SITES, PracticeSite
from ui_automation.compliance import Monitor, as_dicts, check_page


@pytest.mark.parametrize("url", list(SITES.values()), ids=list(SITES))
def test_home_page_loads_and_is_scanned(site: PracticeSite, url: str, request) -> None:
    monitor = Monitor(site.page)  # before navigation: nothing may track before consent
    response = site.goto_path(url)
    assert response is not None, f"{url}: no response"
    assert response.status < 400, f"{url}: HTTP {response.status}"
    site.step("Page loaded")
    assert site.page.title().strip(), f"{url}: the page has no <title> (WCAG 2.4.2)"
    site.expect_accessible(fail_on="none")  # other people's sites: report the findings, don't fail on them
    if not site.settings.compliance.enabled:
        return
    site.step("Check website requirements (before any consent is given)")
    results = check_page(site.page, monitor, site.settings.compliance.checks)
    request.node.compliance = [{"url": site.page.url, "results": as_dicts(results)}]  # reported, never failed
