"""Every site: it loads, answers with a success status, has a title, and its WCAG 2.1 AA scan is reported."""

from __future__ import annotations

import pytest

from practice_sites.conftest import SITES, PracticeSite


@pytest.mark.parametrize("url", list(SITES.values()), ids=list(SITES))
def test_home_page_loads_and_is_scanned(site: PracticeSite, url: str) -> None:
    response = site.goto_path(url)
    assert response is not None, f"{url}: no response"
    assert response.status < 400, f"{url}: HTTP {response.status}"
    site.step("Page loaded")
    assert site.page.title().strip(), f"{url}: the page has no <title> (WCAG 2.4.2)"
    site.expect_accessible(fail_on="none")  # other people's sites: report the findings, don't fail on them
