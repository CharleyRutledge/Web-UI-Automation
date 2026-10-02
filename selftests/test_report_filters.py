"""The phone report's filters: pressing a count tile or a role / browser chip shows only those tests, in every
section, and pressing it again shows everything. Checked in a real browser, with the report's own CSP."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from ui_automation.reporting.report_html import render_summary_html
from ui_automation.reporting.summary import RunSummary, TestResult


def _report(tmp_path: Path) -> Path:
    def t(name: str, role: str, outcome: str) -> TestResult:
        return TestResult(f"site_audit/test_site_audit.py::{name}[{role}-chromium]", outcome, 0.5,
                          message="1 problem" if outcome == "failed" else "",
                          accessibility=[{"url": f"http://app/{role}/{name}", "standard": "wcag21aa", "violations": []}]
                          if name == "test_pages_are_accessible" else [])

    tests = [t("test_pages_are_accessible", r, "passed") for r in ("public", "depositor", "beneficiary")]
    tests += [t("test_no_broken_links", r, "passed") for r in ("public", "depositor")]
    tests += [t("test_no_broken_links", "beneficiary", "failed"), t("test_no_network_errors", "depositor", "failed")]
    summary = RunSummary(exit_status=1, passed=5, failed=2, run_dir=tmp_path, tests=tests,
                         base_url="http://app", duration=2.0, name="Filter test")
    path = tmp_path / "summary.html"
    path.write_text(render_summary_html(summary), encoding="utf-8")
    return path


@pytest.fixture
def page(tmp_path: Path) -> Iterator[Page]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        errors: list[str] = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(_report(tmp_path).as_uri())
        yield pg
        assert not errors, errors  # e.g. the CSP refusing the script
        browser.close()


def rows(page: Page) -> list[str]:
    return [el.inner_text().split("\n")[0] for el in page.locator("ul.tests li:visible .name").all()]


def test_outcome_tile_filters_every_section_and_toggles_off(page: Page) -> None:
    assert len(rows(page)) == 7
    failed = page.get_by_role("button", name="2 Failed")
    failed.click()
    expect(failed).to_have_attribute("aria-pressed", "true")
    assert len(rows(page)) == 2 and all(r.startswith(("No broken links", "No network errors")) for r in rows(page))
    expect(page.locator("#filter-status")).to_contain_text("Showing 2 of 7 tests · failed")
    expect(page.locator(".card[data-outcome='passed']:visible")).to_have_count(0)  # accessibility cards too
    failed.click()  # again: everything is back
    expect(failed).to_have_attribute("aria-pressed", "false")
    assert len(rows(page)) == 7
    expect(page.locator("#filter-status")).to_have_text("")


def test_role_chip_combines_with_the_outcome(page: Page) -> None:
    page.get_by_role("button", name="depositor", exact=True).click()
    assert len(rows(page)) == 3
    expect(page.locator(".card:has-text('http://app/depositor/'):visible")).to_have_count(1)
    expect(page.locator(".card:has-text('http://app/public/'):visible")).to_have_count(0)
    page.get_by_role("button", name="2 Failed").click()
    assert len(rows(page)) == 1 and rows(page)[0].startswith("No network errors")
    expect(page.locator("#filter-status")).to_contain_text("Showing 1 of 7 tests · failed · depositor")


def test_buttons_are_usable_by_keyboard(page: Page) -> None:
    tile = page.get_by_role("button", name="5 Passed")
    tile.focus()
    page.keyboard.press("Enter")
    expect(tile).to_have_attribute("aria-pressed", "true")
    assert len(rows(page)) == 5
