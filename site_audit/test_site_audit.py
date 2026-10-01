"""Site audit: every discovered page loads, has no broken links/images or script errors, works on a
phone, is quick enough, is served securely, and is scanned for accessibility (WCAG).

Every page is opened once (see conftest.crawl) and measured; each test lists all problems it finds there,
so one run shows the whole picture.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest
from playwright.sync_api import Error as PlaywrightError

from pages.base_page import BasePage
from site_audit.conftest import SiteMap, same_site, short, show
from ui_automation.compliance import Monitor, as_dicts, check_page
from ui_automation.local import is_local

# Sites that refuse automated link checks (they answer bots with these) are "unverified", not broken.
UNVERIFIABLE = {401, 403, 405, 406, 429, 999}
MAX_EXTERNAL_LINKS = 60


def report(problems: list[str], what: str) -> None:
    if problems:
        raise AssertionError(f"{len(problems)} {what}:\n" + "\n".join(f"- {p}" for p in problems))


def loaded(site_map: SiteMap) -> list:
    """Results of the pages that opened (in crawl order)."""
    return [r for u in site_map.pages if (r := site_map.results.get(u)) and not r.load_error]


def test_crawl_found_the_site(audit: SiteMap, request: pytest.FixtureRequest) -> None:
    show(request, audit, audit.pages)  # every page found, as a gallery in the report
    print(f"Found {len(audit.pages)} page(s) and {len(audit.links)} link(s)")
    assert audit.pages, "the home page could not be opened"
    problems = [f"{short(u, audit.home)}: {why}" for u, why in audit.problems.items()]
    report(problems, "page(s) could not be crawled")


def test_every_page_loads_with_a_title_and_heading(audit: SiteMap, request: pytest.FixtureRequest) -> None:
    problems: list[str] = []
    bad: list[str] = []
    for url in audit.pages:
        r = audit.results.get(url)
        where = short(url, audit.home)
        if r is None:
            continue
        if r.load_error:
            problems.append(f"{where}: did not load ({r.load_error})")
            continue
        if r.status is not None and r.status >= 400:
            problems.append(f"{where}: HTTP {r.status}")
            bad.append(url)
            continue
        if not r.title:
            problems.append(f"{where}: no page title (WCAG 2.4.2)")
            bad.append(url)
        if r.h1_count == 0:
            problems.append(f"{where}: no main heading (h1)")
            bad.append(url)
    show(request, audit, list(dict.fromkeys(bad)))
    report(problems, "page problem(s)")


def test_no_javascript_errors(audit: SiteMap, request: pytest.FixtureRequest) -> None:
    problems = [f"{short(r.url, audit.home)}: {err}" for r in loaded(audit) for err in r.js_errors]
    show(request, audit, [r.url for r in loaded(audit) if r.js_errors])
    report(problems, "uncaught JavaScript error(s)")


def test_no_broken_links(site: BasePage, site_map: SiteMap) -> None:
    home = site_map.home
    internal = [u for u in site_map.links if same_site(u, home)]
    external = [u for u in site_map.links if not same_site(u, home)][:MAX_EXTERNAL_LINKS]
    if not site.settings.audit.check_external_links:
        external = []
    site.goto_path(home)
    site.step(f"Check {len(internal)} internal and {len(external)} external link(s)")
    broken, unverified = [], []
    for url in internal + external:
        try:
            r = site.page.request.get(url, timeout=20_000, max_redirects=10, fail_on_status_code=False)
            status = r.status
        except PlaywrightError as exc:
            broken.append(f"{url} (linked from {short(site_map.links[url], home)}): {exc.message.splitlines()[0][:120]}")
            continue
        if status in UNVERIFIABLE and not same_site(url, home):
            unverified.append(f"{url}: HTTP {status}")
        elif status >= 400:
            broken.append(f"{url} (linked from {short(site_map.links[url], home)}): HTTP {status}")
    if unverified:
        print("Links that refuse automated checks (not counted as broken):\n" + "\n".join(unverified))
    report(broken, "broken link(s)")


def test_no_broken_images(audit: SiteMap, request: pytest.FixtureRequest) -> None:
    problems = [f"{short(r.url, audit.home)}: {src[:150]}" for r in loaded(audit) for src in r.broken_images]
    show(request, audit, [r.url for r in loaded(audit) if r.broken_images])
    report(problems, "broken image(s)")


def test_pages_work_on_a_phone(audit: SiteMap, request: pytest.FixtureRequest) -> None:
    """WCAG 1.4.10 (reflow): at 320-375 px wide nothing should need sideways scrolling."""
    wide = [r for r in loaded(audit) if r.phone_overflow]
    problems = [f"{short(r.url, audit.home)}: page is {r.phone_overflow['width']}px wide on a 375px phone "
                f"(widest element: {r.phone_overflow['name']})" for r in wide]
    show(request, audit, [r.url for r in wide])
    report(problems, "page(s) that scroll sideways on a phone")


def test_pages_load_quickly(audit: SiteMap, settings, request: pytest.FixtureRequest) -> None:
    budget = settings.audit.load_budget_ms
    timed = [r for r in loaded(audit) if r.ready_ms is not None]
    print("\n".join(f"{short(r.url, audit.home)}: usable after {r.ready_ms} ms" for r in timed))
    slow = [r for r in timed if r.ready_ms > budget]
    show(request, audit, [r.url for r in slow])
    report([f"{short(r.url, audit.home)}: usable after {r.ready_ms} ms (budget {budget} ms)" for r in slow],
           "slow page(s)")


def test_served_securely(site: BasePage, site_map: SiteMap) -> None:
    home = site_map.home
    mode = site.settings.audit.security_checks
    if mode == "off" or (mode == "auto" and is_local(home)):
        pytest.skip("local app: HTTPS and security headers are checked on the deployed site "
                    "(audit.security_checks: on checks them here too)")
    problems: list[str] = []
    host = urlparse(home).hostname
    site.step("Check HTTPS and security headers")
    if home.startswith("https://"):
        try:
            r = site.page.request.get(f"http://{host}/", max_redirects=5, fail_on_status_code=False, timeout=20_000)
            if not r.url.startswith("https://"):
                problems.append(f"http://{host}/ does not redirect to HTTPS (ended at {r.url})")
        except PlaywrightError as exc:
            problems.append(f"http://{host}/ could not be checked: {exc.message.splitlines()[0][:120]}")
    else:
        problems.append("the site is not served over HTTPS")
    headers = {k.lower(): v for k, v in site.page.request.get(home, fail_on_status_code=False).headers.items()}
    wanted = {
        "strict-transport-security": "HSTS (keeps browsers on HTTPS)",
        "x-content-type-options": "X-Content-Type-Options: nosniff",
        "referrer-policy": "Referrer-Policy",
    }
    for header, label in wanted.items():
        if header not in headers:
            problems.append(f"missing {label} header")
    if "content-security-policy" not in headers and "x-frame-options" not in headers:
        problems.append("no clickjacking protection (Content-Security-Policy frame-ancestors or X-Frame-Options)")
    report(problems, "security finding(s)")


def test_pages_are_accessible(audit: SiteMap, settings, request: pytest.FixtureRequest) -> None:
    """Every page scanned with axe-core; findings (with fixes) are in the report's Accessibility section."""
    from ui_automation.accessibility import at_or_above, describe

    cfg = settings.accessibility
    if not cfg.enabled:
        pytest.skip("accessibility checks are off (accessibility.enabled: false)")
    pages = loaded(audit)
    request.node.accessibility = [
        {"url": r.url, "standard": cfg.standard, "violations": [v.__dict__ for v in r.accessibility]} for r in pages
    ]
    failures = []
    if cfg.fail_on != "none":
        for r in pages:
            blocking = at_or_above(r.accessibility, cfg.fail_on)
            if blocking:
                failures.append(f"{short(r.url, audit.home)}: " + describe(blocking).splitlines()[0])
    show(request, audit, [r.url for r in pages if r.accessibility])
    report(failures, "page(s) with accessibility problems at or above the configured level")


def test_meets_website_requirements(site: BasePage, site_map: SiteMap, request) -> None:
    """Irish / EU website requirements on the home page, checked before any cookie consent is given."""
    if not site.settings.compliance.enabled:
        pytest.skip("website requirement checks are off (compliance.enabled: false)")
    monitor = Monitor(site.page)  # before navigation: nothing may track before consent
    site.goto_path(site_map.home)
    site.page.wait_for_load_state("load")
    site.wait_until_settled()
    site.step("Check website requirements (before any consent is given)")
    results = check_page(site.page, monitor, site.settings.compliance.checks)
    request.node.compliance = [{"url": site.page.url, "results": as_dicts(results)}]
    failed = [f"{r.title}: {r.detail} ({r.law})" for r in results if not r.passed]
    if not site.settings.compliance.report_only:
        report(failed, "website requirement(s) not met")
