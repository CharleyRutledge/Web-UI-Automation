"""Site audit: every discovered page loads, has no broken links/images or script errors, works on a
phone, is quick enough, is served securely, and is scanned for accessibility (WCAG).

Each test visits every page and lists all problems at the end, so one run shows the whole picture.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from pages.base_page import BasePage
from site_audit.conftest import SiteMap, same_site, short

# Sites that refuse automated link checks (they answer bots with these) are "unverified", not broken.
UNVERIFIABLE = {401, 403, 405, 406, 429, 999}
MAX_EXTERNAL_LINKS = 60


def visit(site: BasePage, url: str, home: str, problems: list[str]):
    site.step(f"Open {short(url, home)}")
    try:
        response = site.page.goto(url, wait_until=site.settings.navigation_wait_until)  # type: ignore[arg-type]
        site.page.wait_for_load_state("load", timeout=15_000)
    except PlaywrightError as exc:
        problems.append(f"{short(url, home)}: did not load ({exc.message.splitlines()[0]})")
        return None
    return response


def report(problems: list[str], what: str) -> None:
    if problems:
        raise AssertionError(f"{len(problems)} {what}:\n" + "\n".join(f"- {p}" for p in problems))


def test_crawl_found_the_site(site: BasePage, site_map: SiteMap) -> None:
    site.goto_path(site_map.home)
    site.step(f"Found {len(site_map.pages)} page(s) and {len(site_map.links)} link(s)")
    assert site_map.pages, "the home page could not be opened"
    problems = [f"{short(u, site_map.home)}: {why}" for u, why in site_map.problems.items()]
    report(problems, "page(s) could not be crawled")


def test_every_page_loads_with_a_title_and_heading(site: BasePage, site_map: SiteMap) -> None:
    problems: list[str] = []
    for url in site_map.pages:
        response = visit(site, url, site_map.home, problems)
        if response is None:
            continue
        where = short(url, site_map.home)
        if response.status >= 400:
            problems.append(f"{where}: HTTP {response.status}")
            continue
        if not site.page.title().strip():
            problems.append(f"{where}: no page title (WCAG 2.4.2)")
        if site.page.locator("h1").count() == 0:
            problems.append(f"{where}: no main heading (h1)")
    report(problems, "page problem(s)")


def test_no_javascript_errors(site: BasePage, site_map: SiteMap) -> None:
    problems: list[str] = []
    current = {"url": ""}
    site.page.on("pageerror", lambda err: problems.append(f"{short(current['url'], site_map.home)}: {err.message.splitlines()[0][:200]}"))
    for url in site_map.pages:
        current["url"] = url
        visit(site, url, site_map.home, problems)
        site.page.wait_for_timeout(500)  # errors thrown just after load
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


def test_no_broken_images(site: BasePage, site_map: SiteMap) -> None:
    problems: list[str] = []
    for url in site_map.pages:
        if visit(site, url, site_map.home, problems) is None:
            continue
        site.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # load lazy images
        site.page.wait_for_timeout(800)
        broken = site.page.evaluate("""() => [...document.images]
            .filter(i => i.complete && i.naturalWidth === 0 && i.getAttribute('src') && i.loading !== 'lazy'
                         && getComputedStyle(i).display !== 'none')
            .map(i => i.currentSrc || i.src)""")
        problems += [f"{short(url, site_map.home)}: {src[:150]}" for src in broken]
    report(problems, "broken image(s)")


def test_pages_work_on_a_phone(site: BasePage, site_map: SiteMap) -> None:
    """WCAG 1.4.10 (reflow): at 320-375 px wide nothing should need sideways scrolling."""
    site.page.set_viewport_size({"width": 375, "height": 812})
    problems: list[str] = []
    for url in site_map.pages:
        if visit(site, url, site_map.home, problems) is None:
            continue
        overflow = site.page.evaluate("""() => {
            const w = document.documentElement.clientWidth;
            if (document.documentElement.scrollWidth <= w + 1) return null;
            const wide = [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > w + 1
                && getComputedStyle(e).position !== 'fixed');
            const leaf = wide.filter(e => !wide.some(o => o !== e && e.contains(o)))[0];
            const name = leaf ? leaf.tagName.toLowerCase() + (leaf.id ? '#' + leaf.id : '')
                + (leaf.className && typeof leaf.className === 'string' ? '.' + leaf.className.trim().split(/\\s+/)[0] : '') : '?';
            return {width: document.documentElement.scrollWidth, name};
        }""")
        if overflow:
            problems.append(f"{short(url, site_map.home)}: page is {overflow['width']}px wide on a 375px phone "
                            f"(widest element: {overflow['name']})")
    report(problems, "page(s) that scroll sideways on a phone")


def test_pages_load_quickly(site: BasePage, site_map: SiteMap) -> None:
    budget = site.settings.audit.load_budget_ms
    problems: list[str] = []
    timings: list[str] = []
    for url in site_map.pages:
        start = time.monotonic()
        if visit(site, url, site_map.home, problems) is None:
            continue
        nav = site.page.evaluate("""() => { const n = performance.getEntriesByType('navigation')[0];
            return n ? {ready: n.domContentLoadedEventEnd, load: n.loadEventEnd} : null; }""")
        ready = round(nav["ready"]) if nav else round((time.monotonic() - start) * 1000)
        timings.append(f"{short(url, site_map.home)}: usable after {ready} ms")
        if ready > budget:
            problems.append(f"{short(url, site_map.home)}: usable after {ready} ms (budget {budget} ms)")
    print("\n".join(timings))
    report(problems, "slow page(s)")


def test_served_securely(site: BasePage, site_map: SiteMap) -> None:
    home = site_map.home
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


def test_pages_are_accessible(site: BasePage, site_map: SiteMap) -> None:
    """Every page scanned with axe-core; findings (with fixes) are in the report's Accessibility section."""
    problems: list[str] = []
    failures: list[str] = []
    for url in site_map.pages:
        if visit(site, url, site_map.home, problems) is None:
            continue
        try:
            site.expect_accessible()
        except AssertionError as exc:
            failures.append(str(exc).splitlines()[0])
    report(problems + failures, "page(s) with accessibility problems at or above the configured level")
