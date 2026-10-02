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
from site_audit.conftest import SKIP_LINKS, SiteMap, same_site, short, show
from ui_automation.compliance import Monitor, as_dicts, check_page

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
    where = f" under {audit.area}" if audit.area else ""
    print(f"Found {len(audit.pages)} page(s){where} and {len(audit.links)} link(s)")
    if audit.skipped_public:
        print(f"Pages the logged-out visitor already checked were not opened again ({audit.skipped_public}).")
    assert audit.pages, "the home page could not be opened"
    problems = [f"{short(u, audit.home)}: {why}" for u, why in audit.problems.items()]
    if audit.role != "public" and len(audit.pages) == 1:
        # Signed-in pages reached through buttons (not <a href> links) cannot be found by following links.
        problems.append(f"only the start page {short(audit.pages[0], audit.home)} was found for role "
                        f"'{audit.role}': it has no ordinary links to other signed-in pages, so the rest of the "
                        f"app was not checked. List those pages in auth.roles.{audit.role}.pages")
    report(problems, "crawl problem(s)")


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
        if r.blank_ms:
            problems.append(f"{where}: still blank or loading after {r.blank_ms / 1000:g} s, so its content could "
                            "not be checked (a slow or failed API call? audit.content_wait_ms waits longer)")
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


def test_no_network_errors(audit: SiteMap, request: pytest.FixtureRequest) -> None:
    """Every request a page makes (scripts, styles, images, fonts, API calls) must get an answer below HTTP 400."""
    problems = [f"{short(r.url, audit.home)}: {err}" for r in loaded(audit) for err in r.network_errors]
    show(request, audit, [r.url for r in loaded(audit) if r.network_errors])
    report(problems, "failed network request(s)")


def test_no_broken_links(site: BasePage, site_map: SiteMap) -> None:
    home = site_map.home
    # Logout / delete links are never requested: they could end the role's session or change data.
    internal = [u for u in site_map.links if same_site(u, home) and not SKIP_LINKS.search(urlparse(u).path)]
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


def test_role_is_refused_restricted_pages(audit: SiteMap) -> None:
    """auth.roles.<role>.must_not_access: these pages must refuse this role (HTTP 401/403/404 or the login page)."""
    opened = [f"{path}: {what}" for path, what in audit.refused.items() if what]
    print("\n".join(f"{path}: refused" for path, what in audit.refused.items() if not what))
    report(opened, f"page(s) that role '{audit.role}' should not be able to open")


def test_no_broken_images(audit: SiteMap, request: pytest.FixtureRequest) -> None:
    problems = [f"{short(r.url, audit.home)}: {src[:150]}" for r in loaded(audit) for src in r.broken_images]
    show(request, audit, [r.url for r in loaded(audit) if r.broken_images])
    report(problems, "broken image(s)")


def test_pages_fit_every_screen_size(audit: SiteMap, settings, request: pytest.FixtureRequest) -> None:
    """WCAG 1.4.10 (reflow) and responsive layout: at every size in audit.screens (320 px phone to desktop)
    nothing may need sideways scrolling. A screenshot is kept at each size where a page does not fit."""
    sizes = ", ".join(f"{s.name} {s.width}px" for s in settings.audit.screens)
    print(f"Checked at: {sizes}")
    problems = []
    for r in loaded(audit):
        if not r.overflow:
            continue
        sizes = "; ".join(f"on {w['screen']} it is {w['width']}px wide (widest element: {w['name']})"
                          for w in r.overflow.values())
        problems.append(f"{short(r.url, audit.home)}: {sizes}")
        for wide in r.overflow.values():
            if wide.get("shot"):
                request.node.step_screenshots.append((f"{short(r.url, audit.home)} on {wide['screen']}", wide["shot"]))
    report(problems, "page(s) that need sideways scrolling")


def test_works_on_mobile_devices(audit: SiteMap, settings, playwright, run_dir, request: pytest.FixtureRequest) -> None:
    """Every page on real phone profiles (audit.mobile_devices: iPhone, Android): set up for phones, no sideways
    scrolling, readable text, buttons big enough to tap, and no errors. Devices take turns by day like browsers."""
    from site_audit.conftest import check_on_device
    from ui_automation.browsers import browsers_for_run

    devices = browsers_for_run(list(settings.audit.mobile_devices), settings.browser_rotation)
    print(f"Checked on: {', '.join(devices)}")
    problems: list[str] = []
    for device in devices:
        found, gallery = check_on_device(playwright, settings, audit, device,
                                         run_dir / "screenshots" / f"mobile-{audit.role}")
        problems += found
        request.node.step_screenshots.extend(gallery)
    report(problems, "mobile problem(s)")


def test_pages_load_quickly(audit: SiteMap, settings, request: pytest.FixtureRequest) -> None:
    budget = settings.audit.load_budget_ms
    timed = [r for r in loaded(audit) if r.ready_ms is not None]
    print("\n".join(f"{short(r.url, audit.home)}: usable after {r.ready_ms} ms" for r in timed))
    slow = [r for r in timed if r.ready_ms > budget]
    show(request, audit, [r.url for r in slow])
    report([f"{short(r.url, audit.home)}: usable after {r.ready_ms} ms (budget {budget} ms)" for r in slow],
           "slow page(s)")


def test_served_securely(site: BasePage, site_map: SiteMap, role: str) -> None:
    home = site_map.home  # (left out of runs it does not apply to: see conftest._not_applicable)
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
    from ui_automation.accessibility import at_or_above

    cfg = settings.accessibility
    pages = loaded(audit)
    request.node.accessibility = [
        {"url": r.url, "standard": cfg.standard, "violations": [v.__dict__ for v in r.accessibility]} for r in pages
    ]
    # Grouped by issue, not by page: the same issue on most pages is one fix in a shared header, footer or style.
    by_rule: dict[str, dict] = {}
    if cfg.fail_on != "none":
        for r in pages:
            for v in at_or_above(r.accessibility, cfg.fail_on):
                entry = by_rule.setdefault(v.rule, {"v": v, "pages": []})
                entry["pages"].append(short(r.url, audit.home))
    failures = []
    for entry in sorted(by_rule.values(), key=lambda e: -len(e["pages"])):
        v, where = entry["v"], entry["pages"]
        crit = f" (WCAG {', '.join(v.criteria)})" if v.criteria else ""
        listed = ", ".join(where[:5]) + (f" and {len(where) - 5} more" if len(where) > 5 else "")
        shared = (" - on most pages, so probably one fix in a shared header, footer or style"
                  if len(pages) > 2 and len(where) >= 0.6 * len(pages) else "")
        example = f"; e.g. {v.targets[0]}" if v.targets else ""
        failures.append(f"[{v.impact}] {v.help}{crit}: {len(where)} page(s): {listed}{example}{shared}")
    show(request, audit, [r.url for r in pages if r.accessibility])
    report(failures, f"accessibility issue type(s) at or above '{cfg.fail_on}' (fixes are in the report)")


def test_meets_website_requirements(site: BasePage, site_map: SiteMap, request, role: str) -> None:
    """Irish / EU website requirements on the home page, checked before any cookie consent is given."""
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
