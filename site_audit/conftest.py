"""Whole-site audit of the configured base_url: pages are found by following the site's own links.

Point it at any site: python -m ui_automation --config config/<site>.yaml -- site_audit
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

import pytest
from playwright.sync_api import Browser, Error as PlaywrightError, Page

from pages.base_page import BasePage
from ui_automation.blocking import blocked_reason
from ui_automation.config import Settings

# Links to files rather than pages: checked as links, never opened as pages.
FILE_EXTENSIONS = (".pdf", ".zip", ".csv", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx", ".json", ".xml",
                   ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".mp4", ".mp3", ".ics", ".txt", ".rss")

LINKS_JS = """() => [...document.querySelectorAll('a[href]')]
    .map(a => a.href).filter(h => h.startsWith('http'))"""


@dataclass
class SiteMap:
    home: str
    pages: list[str] = field(default_factory=list)  # same-site pages, home first
    links: dict[str, str] = field(default_factory=dict)  # every linked URL -> first page that links to it
    blocked: str = ""  # why the site refused this browser, if it did
    problems: dict[str, str] = field(default_factory=dict)  # page -> what went wrong while crawling


def same_site(url: str, home: str) -> bool:
    a, b = urlparse(url).hostname or "", urlparse(home).hostname or ""
    return a.removeprefix("www.") == b.removeprefix("www.")


def is_page(url: str) -> bool:
    return not urlparse(url).path.lower().endswith(FILE_EXTENSIONS)


def normalise(url: str) -> str:
    url = urldefrag(url)[0]
    parsed = urlparse(url)
    return parsed._replace(query="", path=parsed.path or "/").geturl()


def crawl(browser: Browser, home: str, max_pages: int, wait_until: str) -> SiteMap:
    site = SiteMap(home=home)
    context = browser.new_context()
    page = context.new_page()
    queue, seen = [home], {normalise(home)}
    try:
        while queue and len(site.pages) < max_pages:
            url = queue.pop(0)
            try:
                response = page.goto(url, wait_until=wait_until)  # type: ignore[arg-type]
                page.wait_for_load_state("load", timeout=15_000)
                try:  # links that pages add after loading (React, Vue, ...)
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except PlaywrightError:
                    pass
            except PlaywrightError as exc:
                site.problems[url] = exc.message.splitlines()[0]
                site.pages.append(url)
                continue
            reason = blocked_reason(response, page)
            if reason:
                if not site.pages:
                    site.blocked = reason
                    return site
                site.problems[url] = f"blocked: {reason}"
                continue
            site.pages.append(url)
            for href in page.evaluate(LINKS_JS):
                site.links.setdefault(urldefrag(href)[0], url)
                key = normalise(href)
                if same_site(href, home) and is_page(href) and key not in seen:
                    seen.add(key)
                    queue.append(key)
    finally:
        context.close()
    return site


@pytest.fixture(scope="session")
def site_map(browser: Browser, settings: Settings) -> SiteMap:
    home = settings.base_url.rstrip("/") + "/"
    return crawl(browser, home, settings.audit.max_pages, settings.navigation_wait_until)


@pytest.fixture
def site(page: Page, settings: Settings, request: pytest.FixtureRequest, test_artifacts_dir,
         site_map: SiteMap) -> BasePage:
    if site_map.blocked:
        pytest.skip(f"Not tested: {site_map.blocked}")
    return BasePage(page, settings, request, test_artifacts_dir)


def short(url: str, home: str) -> str:
    """'https://site.ie/about/' -> '/about/' for same-site URLs; other sites stay in full."""
    return (urlparse(url).path or "/") if same_site(url, home) else url
