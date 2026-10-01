"""Whole-site audit of the configured base_url: pages are found by following the site's own links.

Each page is opened once. While it is open, everything the tests need is measured (status, title and
heading, script errors, broken images, phone layout, load time, accessibility) and a screenshot is taken;
each test then reports from those results. That keeps a 25-page audit to one visit per page instead of
one per page per test.

Point it at any site: python -m ui_automation --config config/<site>.yaml -- site_audit
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urldefrag, urlparse

import pytest
from playwright.sync_api import Browser, Error as PlaywrightError, Page

from pages.base_page import BasePage
from ui_automation.blocking import blocked_reason
from ui_automation.config import Settings, accepts_self_signed

# Links to files rather than pages: checked as links, never opened as pages.
FILE_EXTENSIONS = (".pdf", ".zip", ".csv", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx", ".json", ".xml",
                   ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".mp4", ".mp3", ".ics", ".txt", ".rss")

LINKS_JS = """() => [...document.querySelectorAll('a[href]')]
    .map(a => a.href).filter(h => h.startsWith('http'))"""

BROKEN_IMAGES_JS = """() => [...document.images]
    .filter(i => i.complete && i.naturalWidth === 0 && i.getAttribute('src') && i.loading !== 'lazy'
                 && getComputedStyle(i).display !== 'none')
    .map(i => i.currentSrc || i.src)"""

OVERFLOW_JS = """() => {
    const w = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth <= w + 1) return null;
    const wide = [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > w + 1
        && getComputedStyle(e).position !== 'fixed');
    const leaf = wide.filter(e => !wide.some(o => o !== e && e.contains(o)))[0];
    const name = leaf ? leaf.tagName.toLowerCase() + (leaf.id ? '#' + leaf.id : '')
        + (leaf.className && typeof leaf.className === 'string' ? '.' + leaf.className.trim().split(/\\s+/)[0] : '') : '?';
    return {width: document.documentElement.scrollWidth, name};
}"""

TIMING_JS = """() => { const n = performance.getEntriesByType('navigation')[0];
    return n ? Math.round(n.domContentLoadedEventEnd) : null; }"""

PHONE = {"width": 375, "height": 812}


@dataclass
class PageResult:
    """Everything measured on one page during its single visit."""
    url: str
    status: int | None = None
    load_error: str = ""  # set when the page could not be opened at all
    title: str = ""
    h1_count: int = 0
    js_errors: list[str] = field(default_factory=list)
    broken_images: list[str] = field(default_factory=list)
    phone_overflow: dict | None = None  # {"width": px, "name": widest element} when it scrolls sideways
    ready_ms: int | None = None
    accessibility: list = field(default_factory=list)  # axe Violation objects
    screenshot: str = ""


@dataclass
class SiteMap:
    home: str
    pages: list[str] = field(default_factory=list)  # same-site pages, home first
    links: dict[str, str] = field(default_factory=dict)  # every linked URL -> first page that links to it
    blocked: str = ""  # why the site refused this browser, if it did
    problems: dict[str, str] = field(default_factory=dict)  # page -> what went wrong while crawling
    results: dict[str, PageResult] = field(default_factory=dict)  # page -> what was measured on it


def same_site(url: str, home: str) -> bool:
    a, b = urlparse(url).hostname or "", urlparse(home).hostname or ""
    return a.removeprefix("www.") == b.removeprefix("www.")


def is_page(url: str) -> bool:
    return not urlparse(url).path.lower().endswith(FILE_EXTENSIONS)


def normalise(url: str) -> str:
    url = urldefrag(url)[0]
    parsed = urlparse(url)
    return parsed._replace(query="", path=parsed.path or "/").geturl()


def short(url: str, home: str) -> str:
    """'https://site.ie/about/' -> '/about/' for same-site URLs; other sites stay in full."""
    return (urlparse(url).path or "/") if same_site(url, home) else url


def _measure(page: Page, result: PageResult, settings: Settings, shots: Path | None, index: int,
             js_errors: list[str]) -> None:
    """All per-page checks, while the page is open."""
    from ui_automation.accessibility import scan

    result.title = page.title().strip()
    result.h1_count = page.locator("h1").count()
    result.ready_ms = page.evaluate(TIMING_JS)
    if shots is not None:
        path = shots / f"{index:03d}_{re.sub(r'[^A-Za-z0-9]+', '_', urlparse(result.url).path).strip('_') or 'home'}.png"
        try:
            page.screenshot(path=str(path))
            result.screenshot = str(path)
        except PlaywrightError:
            pass
    if settings.accessibility.enabled:
        result.accessibility = scan(page, settings.accessibility.standard)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # load lazy images
    page.wait_for_timeout(500)
    result.broken_images = page.evaluate(BROKEN_IMAGES_JS)
    page.set_viewport_size(PHONE)
    page.wait_for_timeout(250)  # re-layout at phone width
    result.phone_overflow = page.evaluate(OVERFLOW_JS)
    page.set_viewport_size({"width": settings.viewport_width, "height": settings.viewport_height})
    result.js_errors = list(js_errors)  # errors thrown while loading and during the checks


def crawl(browser: Browser, home: str, max_pages: int, wait_until: str, *, self_signed_ok: bool = False,
          settings: Settings | None = None, shots: Path | None = None) -> SiteMap:
    """Follow the site's links breadth-first; with `settings`, also measure every page on the same visit."""
    site = SiteMap(home=home)
    viewport = {"width": settings.viewport_width, "height": settings.viewport_height} if settings else None
    context = browser.new_context(ignore_https_errors=self_signed_ok, viewport=viewport)
    page = context.new_page()
    if settings:
        page.set_default_timeout(settings.timeout_ms)
    js_errors: list[str] = []
    page.on("pageerror", lambda err: js_errors.append(err.message.splitlines()[0][:200] if err.message else str(err)))
    queue, seen = [home], {normalise(home)}
    try:
        while queue and len(site.pages) < max_pages:
            url = queue.pop(0)
            result = PageResult(url)
            js_errors.clear()
            try:
                response = page.goto(url, wait_until=wait_until)  # type: ignore[arg-type]
                page.wait_for_load_state("load", timeout=15_000)
                try:  # content and links that pages add after loading (React, Vue, ...)
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except PlaywrightError:
                    pass
            except PlaywrightError as exc:
                site.problems[url] = exc.message.splitlines()[0]
                result.load_error = site.problems[url]
                site.pages.append(url)
                site.results[url] = result
                continue
            reason = blocked_reason(response, page)
            if reason:
                if not site.pages:
                    site.blocked = reason
                    return site
                site.problems[url] = f"blocked: {reason}"
                continue
            site.pages.append(url)
            site.results[url] = result
            result.status = response.status if response else None
            for href in page.evaluate(LINKS_JS):
                site.links.setdefault(urldefrag(href)[0], url)
                key = normalise(href)
                if same_site(href, home) and is_page(href) and key not in seen:
                    seen.add(key)
                    queue.append(key)
            if settings is not None:
                try:
                    _measure(page, result, settings, shots, len(site.pages), js_errors)
                except PlaywrightError as exc:  # a page that breaks mid-check is reported, the audit goes on
                    site.problems[url] = f"could not be checked: {exc.message.splitlines()[0]}"
    finally:
        context.close()
    return site


@pytest.fixture(scope="session")
def site_map(browser: Browser, settings: Settings, run_dir: Path) -> SiteMap:
    home = settings.base_url.rstrip("/") + "/"
    shots = run_dir / "screenshots" / "site-audit-pages"
    shots.mkdir(parents=True, exist_ok=True)
    return crawl(browser, home, settings.audit.max_pages, settings.navigation_wait_until,
                 self_signed_ok=accepts_self_signed(settings), settings=settings, shots=shots)


@pytest.fixture
def audit(site_map: SiteMap, request: pytest.FixtureRequest) -> SiteMap:
    """The measured site, for tests that only report (no browser of their own)."""
    if site_map.blocked:
        pytest.skip(f"Not tested: {site_map.blocked}")
    return site_map


@pytest.fixture
def site(page: Page, settings: Settings, request: pytest.FixtureRequest, test_artifacts_dir,
         site_map: SiteMap) -> BasePage:
    """A browser page, for the tests that still need one (links, security, website requirements)."""
    if site_map.blocked:
        pytest.skip(f"Not tested: {site_map.blocked}")
    return BasePage(page, settings, request, test_artifacts_dir)


def show(request: pytest.FixtureRequest, site_map: SiteMap, urls: list[str]) -> None:
    """Put these pages' screenshots in this test's report, in order."""
    for url in urls:
        result = site_map.results.get(url)
        if result and result.screenshot:
            request.node.step_screenshots.append((short(url, site_map.home), result.screenshot))
