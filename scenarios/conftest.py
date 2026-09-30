"""Scenario suites driven by the self-tests through the real CLI (never part of the normal UI run)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from pages.base_page import BasePage
from ui_automation.config import Settings


@pytest.fixture
def site(page: Page, settings: Settings, request: pytest.FixtureRequest, test_artifacts_dir) -> BasePage:
    return BasePage(page, settings, request, test_artifacts_dir)


@pytest.fixture
def broken_fixture() -> None:
    raise RuntimeError("fixture exploded during setup")
