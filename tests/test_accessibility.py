"""Accessibility of the site under test: one test per page in settings.yaml -> accessibility.pages.

Checks the configured WCAG standard with axe-core (WCAG 2.1 AA = EN 301 549, required by the EU Web
Accessibility Directive and the European Accessibility Act). Automated checks are one part of a
WCAG-EM evaluation; the report lists what still needs a person to check.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from pages.base_page import BasePage
from ui_automation.config import Settings, load_settings


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "a11y_path" not in metafunc.fixturenames:
        return
    cfg = load_settings(metafunc.config.getoption("--config")).accessibility
    if cfg.enabled and cfg.pages:
        metafunc.parametrize("a11y_path", cfg.pages, ids=list(cfg.pages))
    else:
        metafunc.parametrize("a11y_path", [pytest.param("", marks=pytest.mark.skip(reason="accessibility checks are off"))])


def test_page_is_accessible(
    a11y_path: str, page: Page, settings: Settings, request: pytest.FixtureRequest, test_artifacts_dir
) -> None:
    site = BasePage(page, settings, request, test_artifacts_dir)
    site.goto_path(a11y_path)
    site.expect_accessible()
