"""Website compliance (Ireland / EU): one test per page in settings.yaml -> compliance.pages.

Off by default (compliance.enabled: false): company and cookie rules depend on who runs the site.
Checks what is missing or misbehaving; the wording of policies still needs a person (and legal advice).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from pages.base_page import BasePage
from ui_automation.compliance import Monitor, as_dicts, check_page
from ui_automation.config import Settings, load_settings


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "compliance_path" not in metafunc.fixturenames:
        return
    cfg = load_settings(metafunc.config.getoption("--config")).compliance
    if cfg.enabled:
        metafunc.parametrize("compliance_path", cfg.pages, ids=list(cfg.pages))
    else:
        metafunc.parametrize("compliance_path", [pytest.param("", marks=pytest.mark.skip(reason="compliance checks are off"))])


def test_page_meets_website_requirements(
    compliance_path: str, page: Page, settings: Settings, request: pytest.FixtureRequest, test_artifacts_dir
) -> None:
    monitor = Monitor(page)  # before navigation: tracking must not happen before consent
    site = BasePage(page, settings, request, test_artifacts_dir)
    site.goto_path(compliance_path)
    page.wait_for_load_state("load")
    site.wait_until_settled()  # footers and cookie banners are often added after the page loads
    site.step("Check website requirements (before any consent is given)")
    results = check_page(page, monitor, settings.compliance.checks)
    request.node.compliance = [{"url": page.url, "results": as_dicts(results)}]
    failed = [r for r in results if not r.passed]
    if failed:
        raise AssertionError(f"{page.url}: " + "; ".join(f"{r.title}: {r.detail}" for r in failed))
