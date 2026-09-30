"""Every way a browser test can end: pass, assertion failure, timeouts, HTTP/network/TLS errors."""

from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import expect

from pages.base_page import BasePage


def test_passes(site: BasePage) -> None:
    site.goto_path("/")
    site.expect_title_contains("Playwright")


def test_missing_element(site: BasePage) -> None:
    site.goto_path("/missing")
    site.expect_heading("Installation")


def test_slow_page_times_out(site: BasePage) -> None:
    site.page.set_extra_http_headers({"X-Delay": "6"})
    site.goto_path("/slow")  # timeout_ms in the scenario config is 3000


def test_server_error_page(site: BasePage) -> None:
    site.step("Open a page that answers 500")
    response = site.page.goto("/error500")
    assert response is not None and response.status == 200, f"HTTP {response.status if response else '?'}"


def test_redirect_loop(site: BasePage) -> None:
    site.goto_path("/loop")


def test_offline(site: BasePage) -> None:
    site.goto_path("/")
    site.page.context.set_offline(True)
    site.goto_path("/docs/intro")


def test_dns_failure(site: BasePage) -> None:
    site.step("Open a host that does not exist")
    site.page.goto("http://does-not-exist.invalid/")


def test_tls_certificate_error(site: BasePage) -> None:
    site.step("Open an HTTPS site with an untrusted certificate")
    site.page.goto(os.environ["SCENARIO_TLS_URL"])


def test_throttled_network_still_passes(site: BasePage) -> None:
    cdp = site.page.context.new_cdp_session(site.page)
    cdp.send("Network.enable")
    cdp.send(
        "Network.emulateNetworkConditions",
        {"offline": False, "latency": 1500, "downloadThroughput": 50_000, "uploadThroughput": 50_000},
    )
    site.goto_path("/tall")
    expect(site.page.get_by_role("heading", name="Installation")).to_be_visible()


def test_setup_error(site: BasePage, broken_fixture: None) -> None:
    site.goto_path("/")


def test_skipped() -> None:
    pytest.skip("not relevant on this platform")


@pytest.mark.xfail(reason="known bug", strict=True)
def test_expected_failure() -> None:
    assert False


@pytest.mark.parametrize("name", ["<script>alert(1)</script>"])
def test_hostile_names(site: BasePage, name: str) -> None:
    site.goto_path("/")
    raise AssertionError(f'<img src=x onerror="alert(2)"> {name} & "quotes"')


def test_title_regex_escaping(site: BasePage) -> None:
    site.goto_path("/")
    # expect_title_contains must treat regex characters literally.
    site.expect_title_contains("Playwright test site")
    assert re.escape("a.b") == r"a\.b"
