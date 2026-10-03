"""Whole-site audit of the configured base_url: pages are found by following the site's own links.

Each page is opened once. While it is open, everything the tests need is measured (status, title and
heading, script errors, broken images, phone layout, load time, accessibility) and a screenshot is taken;
each test then reports from those results. That keeps a 25-page audit to one visit per page instead of
one per page per test.

Point it at any site: python -m ui_automation --config config/<site>.yaml -- site_audit
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urldefrag, urlparse

import pytest
from playwright.sync_api import Browser, Error as PlaywrightError, Page

from pages.base_page import BasePage
from ui_automation.blocking import blocked_reason
from ui_automation.config import Settings, accepts_self_signed, role_area

# Links to files rather than pages: checked as links, never opened as pages.
FILE_EXTENSIONS = (".pdf", ".zip", ".csv", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx", ".json", ".xml",
                   ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".mp4", ".mp3", ".ics", ".txt", ".rss")

LINKS_JS = """() => [...document.querySelectorAll('a[href]')]
    .map(a => a.href).filter(h => h.startsWith('http'))"""

# The page shows real content: some visible text, and no loading indicator (a busy region, a progress bar or
# spinner, or text that only says "Loading..."). A blank page or a spinner is still being built.
CONTENT_JS = """() => {
    const body = document.body;
    if (!body) return false;
    const text = (body.innerText || '').trim();
    if (!text || /^(loading|please wait|laden|chargement|cargando)[\\s.\u2026]*$/i.test(text)) return false;
    const busy = [...document.querySelectorAll('[aria-busy="true"], [role="progressbar"]')]
        .some(el => el.getClientRects().length > 0);
    return !busy;
}"""

# Then wait until the page stops changing (no DOM changes for 500 ms, at most 5 s): data that arrives
# after the first content (lists, tables, menus) is in place before anything is measured.
STABLE_JS = """() => new Promise(resolve => {
    let quiet;
    const done = () => { observer.disconnect(); clearTimeout(quiet); resolve(true); };
    const observer = new MutationObserver(() => { clearTimeout(quiet); quiet = setTimeout(done, 500); });
    observer.observe(document.documentElement, {childList: true, subtree: true, characterData: true});
    quiet = setTimeout(done, 500);
    setTimeout(done, 5000);
})"""

# What a phone shows: set up for phones (viewport tag), no sideways scrolling and readable text (12px+).
# Tap target size (WCAG 2.5.8) is axe-core's own target-size rule, run on the phone's layout.
MOBILE_JS = """() => {
    const meta = document.querySelector('meta[name="viewport"]');
    const viewport = !!meta && /width\\s*=\\s*device-width/i.test(meta.content || '');
    const w = document.documentElement.clientWidth;
    const overflow = document.documentElement.scrollWidth > w + 1 ? document.documentElement.scrollWidth : 0;
    const shown = el => { const r = el.getBoundingClientRect(), st = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
    const label = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
        + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\\s+/)[0] : '');
    const small = [];
    for (const el of document.querySelectorAll('body *')) {
        if (small.length >= 5) break;
        const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
        const size = parseFloat(getComputedStyle(el).fontSize);
        if (own && size < 12 && shown(el)) small.push(label(el) + ' (' + size + 'px)');
    }
    return {viewport, overflow, small};
}"""

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

# Never followed while crawling: they would log the role out or change data. (Only links are followed,
# never forms, but some apps act on a plain link.)
SKIP_LINKS = re.compile(r"(log-?out|log-?off|sign-?out|delete|remove|destroy|unsubscribe|deactivate)", re.I)


@dataclass
class PageResult:
    """Everything measured on one page during its single visit."""
    url: str
    status: int | None = None
    final_url: str = ""  # where the browser ended up (differs after a redirect, e.g. to the login page)
    load_error: str = ""  # set when the page could not be opened at all
    title: str = ""
    h1_count: int = 0
    js_errors: list[str] = field(default_factory=list)
    network_errors: list[str] = field(default_factory=list)  # requests that failed or got an error status
    blank_ms: int = 0  # set when the page still showed no content after waiting this long
    broken_images: list[str] = field(default_factory=list)
    # screen name -> {"screen": "tablet (768px)", "width": px, "name": widest element, "shot": path}, for every
    # screen size (audit.screens) at which the page needs sideways scrolling
    overflow: dict = field(default_factory=dict)
    ready_ms: int | None = None
    accessibility: list = field(default_factory=list)  # axe Violation objects
    screenshot: str = ""


@dataclass
class ApiCall:
    """One API call (fetch / XHR) a page made while crawling. Kept in memory only: `auth` is the request's
    Authorization header (to replay it as this role) and is never written to a report or a file."""
    method: str
    url: str = field(repr=False)  # full address (the query is needed to replay it; reports show only the path)
    status: int
    ms: int
    page: str  # the page that made it
    body_hash: str = ""  # fingerprint of a successful response, to compare replays (the data itself is not kept)
    auth: str = field(default="", repr=False)


@dataclass
class SiteMap:
    home: str
    pages: list[str] = field(default_factory=list)  # same-site pages, home first
    links: dict[str, str] = field(default_factory=dict)  # every linked URL -> first page that links to it
    blocked: str = ""  # why the site refused this browser, if it did
    problems: dict[str, str] = field(default_factory=dict)  # page -> what went wrong while crawling
    results: dict[str, PageResult] = field(default_factory=dict)  # page -> what was measured on it
    role: str = "public"
    # The role's logged-in session (cookies, local storage). Never shown: pytest prints a failing test's
    # arguments, and this would put session cookies into the report.
    state: dict | None = field(default=None, repr=False)
    login_error: str = ""  # set when logging in as the role failed
    refused: dict[str, str] = field(default_factory=dict)  # must_not_access page -> what happened ("" = refused)
    area: str = ""  # the part of the site this role's crawl stayed in ("" = everywhere)
    skipped_public: int = 0  # pages left out because the logged-out visitor already checked them
    api: list[ApiCall] = field(default_factory=list, repr=False)  # every distinct API call (addresses can hold tokens)


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
    for screen in settings.audit.screens:  # every screen size: nothing may need sideways scrolling
        page.set_viewport_size({"width": screen.width, "height": screen.height})
        page.wait_for_timeout(250)  # re-layout at this size
        wide = page.evaluate(OVERFLOW_JS)
        if wide:
            wide["screen"] = f"{screen.name} ({screen.width}px)"
            if shots is not None:
                shot = shots / f"{index:03d}_{re.sub(r'[^A-Za-z0-9]+', '_', screen.name)}_{screen.width}.png"
                try:
                    page.screenshot(path=str(shot))
                    wide["shot"] = str(shot)
                except PlaywrightError:
                    pass
            result.overflow[screen.name] = wide
    page.set_viewport_size({"width": settings.viewport_width, "height": settings.viewport_height})
    result.js_errors = list(js_errors)  # errors thrown while loading and during the checks


def crawl(browser: Browser, home: str, max_pages: int, wait_until: str, *, self_signed_ok: bool = False,
          settings: Settings | None = None, shots: Path | None = None, storage_state: dict | None = None,
          start: str | None = None, area: str = "", extra: tuple[str, ...] = (),
          already_checked: frozenset[str] = frozenset()) -> SiteMap:
    """Follow the site's links breadth-first; with `settings`, also measure every page on the same visit.
    `storage_state` is a logged-in session; the crawl then starts at `start` (default: the home page).
    `area` keeps the crawl to pages under that path (like /app); `extra` pages are checked as well.
    `already_checked` pages (seen by the logged-out visitor) are not opened again."""
    site = SiteMap(home=home)
    viewport = {"width": settings.viewport_width, "height": settings.viewport_height} if settings else None
    context = browser.new_context(ignore_https_errors=self_signed_ok, viewport=viewport, storage_state=storage_state)
    page = context.new_page()
    if settings:
        page.set_default_timeout(settings.timeout_ms)
    js_errors: list[str] = []
    page.on("pageerror", lambda err: js_errors.append(err.message.splitlines()[0][:200] if err.message else str(err)))
    network_errors: list[str] = []

    def where(url: str) -> str:  # never the query string: it can carry tokens
        u = urlparse(url)
        return (u.path or "/") if same_site(url, home) else f"{u.netloc}{u.path or '/'}"

    def on_response(response) -> None:  # noqa: ANN001 - Playwright Response
        # (the page itself is covered by the page and link checks)
        if response.status >= 400 and not response.request.is_navigation_request() and len(network_errors) < 50:
            network_errors.append(f"{response.request.method} {where(response.url)} -> HTTP {response.status}")

    def on_failed(request) -> None:  # noqa: ANN001 - Playwright Request
        failure = request.failure or "failed"
        # ERR_ABORTED: the browser cancelled it because the crawler moved on to the next page, not a fault.
        if "ERR_ABORTED" not in failure and not request.is_navigation_request() and len(network_errors) < 50:
            network_errors.append(f"{request.method} {where(request.url)} -> no answer ({failure})")

    page.on("response", on_response)
    page.on("requestfailed", on_failed)

    # API calls: method, address, status, time and a fingerprint of the answer (for the API checks).
    started: dict[int, float] = {}
    seen_calls: set[tuple[str, str]] = set()

    def on_request(request) -> None:  # noqa: ANN001 - Playwright Request
        if request.resource_type in ("fetch", "xhr"):
            started[id(request)] = time.monotonic()

    def on_finished(request) -> None:  # noqa: ANN001 - Playwright Request
        begun = started.pop(id(request), None)
        key = (request.method, request.url)
        if begun is None or key in seen_calls or len(site.api) >= 200:
            return
        seen_calls.add(key)
        try:
            response = request.response()
            if response is None:
                return
            body_hash = ""
            if response.ok and request.method == "GET":
                body_hash = hashlib.sha256(response.body()).hexdigest()
            auth = request.all_headers().get("authorization", "")
        except PlaywrightError:
            return
        site.api.append(ApiCall(request.method, request.url, response.status,
                                int((time.monotonic() - begun) * 1000), page.url, body_hash, auth))

    page.on("request", on_request)
    page.on("requestfinished", on_finished)
    def in_area(url: str) -> bool:
        path = urlparse(url).path
        return not area or path == area or path.startswith(area.rstrip("/") + "/")

    first = start or home
    queue, seen = [first], {normalise(first)}
    for url in extra:
        if normalise(url) not in seen:
            seen.add(normalise(url))
            queue.append(normalise(url))
    try:
        while queue and len(site.pages) < max_pages:
            url = queue.pop(0)
            result = PageResult(url)
            js_errors.clear()
            network_errors.clear()
            try:
                response = page.goto(url, wait_until=wait_until)  # type: ignore[arg-type]
                page.wait_for_load_state("load", timeout=15_000)
                try:  # content and links that pages add after loading (React, Vue, ...)
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except PlaywrightError:
                    pass
                content_wait = settings.audit.content_wait_ms if settings else 15_000
                try:
                    page.wait_for_function(CONTENT_JS, timeout=max(content_wait, 1))
                    page.evaluate(STABLE_JS)
                except PlaywrightError:
                    result.blank_ms = content_wait  # still blank or loading: measured as it is, and said so
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
            result.final_url = page.url
            for href in page.evaluate(LINKS_JS):
                site.links.setdefault(urldefrag(href)[0], url)
                key = normalise(href)
                if (same_site(href, home) and is_page(href) and key not in seen and in_area(key)
                        and key not in already_checked and not SKIP_LINKS.search(urlparse(href).path)):
                    seen.add(key)
                    queue.append(key)
            if settings is not None:
                try:
                    _measure(page, result, settings, shots, len(site.pages), js_errors)
                    result.network_errors = list(dict.fromkeys(network_errors))
                except PlaywrightError as exc:  # a page that breaks mid-check is reported, the audit goes on
                    site.problems[url] = f"could not be checked: {exc.message.splitlines()[0]}"
    finally:
        context.close()
    return site


def roles_to_audit(settings: Settings) -> list[str]:
    """'public' (logged out) and every role in auth.roles."""
    names = [r.name for r in settings.auth.roles]
    return (["public"] if settings.auth.include_public or not names else []) + names


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "role" in metafunc.fixturenames:
        from ui_automation.config import load_settings

        roles = roles_to_audit(load_settings(metafunc.config.getoption("--config")))
        metafunc.parametrize("role", roles, ids=roles, scope="session")


# Checks that do not depend on the browser (HTTP status of links, HTTPS headers, the site's legal pages) run in
# the first browser only; everything measured on the page itself runs in every browser.
# The phones use their own browsers, so the mobile check also runs once per run.
_ONCE_PER_SITE = ("test_no_broken_links", "test_served_securely", "test_meets_website_requirements",
                  "test_works_on_mobile_devices", "test_api_calls_work_and_are_fast",
                  "test_api_refuses_logged_out_requests", "test_api_keeps_roles_apart")


def _not_applicable(name: str, role: str, settings: Settings, browser: str = "", first_browser: str = "") -> str:
    """Why a test does not apply to this run ('' = it does). Such tests are not run at all, rather than skipped.
    `first_browser` is the first browser of this run (with daily rotation: today's browser)."""
    from ui_automation.local import is_local

    if name in _ONCE_PER_SITE and browser and first_browser and browser != first_browser:
        return ""  # silently: checked once, in the run's first browser
    if name in ("test_api_refuses_logged_out_requests", "test_api_keeps_roles_apart") and role == "public":
        return ""  # silently: these check what a logged-in role's API calls give away
    if name == "test_api_keeps_roles_apart" and len(settings.auth.roles) < 2:
        return "API keeps roles apart: needs at least two roles in auth.roles"
    if name == "test_works_on_mobile_devices" and not settings.audit.mobile_devices:
        return "Works on mobile devices: audit.mobile_devices is empty"
    if name in ("test_served_securely", "test_meets_website_requirements") and not first_role(settings, role):
        return ""  # silently: the site-wide checks run once, for the first role
    if name == "test_served_securely":
        mode = settings.audit.security_checks
        if mode == "off":
            return "Served securely: audit.security_checks is off"
        if mode == "auto" and is_local(settings.base_url):
            return ("Served securely: a local app has no HTTPS; it is checked on the deployed site "
                    "(audit.security_checks: on checks it here too)")
    if name == "test_meets_website_requirements" and not settings.compliance.enabled:
        return "Meets website requirements: compliance.enabled is false"
    if name == "test_pages_are_accessible" and not settings.accessibility.enabled:
        return "Pages are accessible: accessibility.enabled is false"
    if name == "test_role_is_refused_restricted_pages":
        spec = next((r for r in settings.auth.roles if r.name == role), None)
        if spec is None:
            return ""  # silently: the logged-out visitor has nothing to be refused
        if not spec.must_not_access:
            return (f"Role is refused restricted pages ({role}): no pages listed in "
                    f"auth.roles.{role}.must_not_access, so access control is not tested for this role")
    return "-"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tests that do not apply are left out (listed at the end of the run), so nothing is ever skipped."""
    from ui_automation.config import load_settings

    settings = load_settings(config.getoption("--config"))
    first_browser = (config.getoption("--browser") or [settings.browser])[0]
    keep: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    reasons: list[str] = []
    for item in items:
        params = item.callspec.params if hasattr(item, "callspec") else {}
        role = params.get("role")
        name = getattr(item, "originalname", item.name)
        why = (_not_applicable(name, role or "public", settings, params.get("browser_name", ""), first_browser)
               if role else "-")
        if why == "-":
            keep.append(item)
        else:
            dropped.append(item)
            if why and why not in reasons:
                reasons.append(why)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep
    config._site_audit_not_run = reasons  # type: ignore[attr-defined]


def pytest_terminal_summary(terminalreporter, config: pytest.Config) -> None:  # noqa: ANN001
    reasons = getattr(config, "_site_audit_not_run", [])
    if reasons:
        terminalreporter.section("Not run (does not apply to this site or its settings)")
        for why in reasons:
            terminalreporter.write_line(f"- {why}")


_MAPS: dict[tuple[str, str], SiteMap] = {}  # (browser, role) -> its audited site


def _audit_role(browser: Browser, settings: Settings, run_dir: Path, role: str) -> SiteMap:
    from urllib.parse import urljoin

    from ui_automation.login import LoginError, is_refused, log_in

    home = settings.base_url.rstrip("/") + "/"
    shots = run_dir / "screenshots" / f"site-audit-pages-{role}-{browser.browser_type.name}"
    shots.mkdir(parents=True, exist_ok=True)
    state, start = None, None
    spec = next((r for r in settings.auth.roles if r.name == role), None)
    if spec is not None:
        try:
            state = log_in(browser, settings, spec)
        except LoginError as exc:
            return SiteMap(home=home, role=role, login_error=str(exc))
        start = urljoin(home, spec.start.lstrip("/")) if spec.start else None
    area = role_area(spec) if spec is not None else ""
    extra = tuple(urljoin(home, p.lstrip("/")) for p in spec.pages) if spec is not None else ()
    already_checked: frozenset[str] = frozenset()
    if spec is not None and settings.auth.include_public:
        # Pages the logged-out visitor opened (not redirected away from) were checked already: a role's pages
        # go to its own part of the app instead.
        public = _map_for(browser, settings, run_dir, "public")
        already_checked = frozenset(
            normalise(u) for u, r in public.results.items()
            if r.final_url and normalise(r.final_url) == normalise(u) and not r.load_error)
    site = crawl(browser, home, settings.audit.max_pages, settings.navigation_wait_until,
                 self_signed_ok=accepts_self_signed(settings), settings=settings, shots=shots,
                 storage_state=state, start=start, area=area, extra=extra, already_checked=already_checked)
    site.area = area
    site.skipped_public = len(already_checked)
    site.role = role
    site.state = state
    if spec is not None and spec.must_not_access:
        login_url = urljoin(home, settings.auth.login_url.lstrip("/"))
        context = browser.new_context(ignore_https_errors=accepts_self_signed(settings), storage_state=state)
        page = context.new_page()
        try:
            for path in spec.must_not_access:
                url = urljoin(home, path.lstrip("/"))
                try:
                    response = page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(500)
                    password_box = page.locator("input[type='password']").count() > 0
                    status = response.status if response else None
                    site.refused[path] = "" if is_refused(status, page.url, password_box, login_url) else (
                        f"opened for role '{role}' (HTTP {status}, ended at {short(page.url, home)})")
                except PlaywrightError as exc:
                    site.refused[path] = ""  # the browser could not open it at all: refused
                    _ = exc
        finally:
            context.close()
    return site


def _map_for(browser: Browser, settings: Settings, run_dir: Path, role: str) -> SiteMap:
    key = (browser.browser_type.name, role)
    if key not in _MAPS:
        _MAPS[key] = _audit_role(browser, settings, run_dir, role)
    return _MAPS[key]


@pytest.fixture(scope="session")
def site_map(browser: Browser, settings: Settings, run_dir: Path, role: str) -> SiteMap:
    return _map_for(browser, settings, run_dir, role)


@pytest.fixture
def audit(site_map: SiteMap, request: pytest.FixtureRequest) -> SiteMap:
    """The measured site, for tests that only report (no browser of their own)."""
    if site_map.login_error:
        pytest.fail(f"Could not log in: {site_map.login_error}", pytrace=False)
    if site_map.blocked:
        pytest.skip(f"Not tested: {site_map.blocked}")
    return site_map


@pytest.fixture
def site(page: Page, settings: Settings, request: pytest.FixtureRequest, test_artifacts_dir,
         site_map: SiteMap) -> BasePage:
    """A browser page, for the tests that still need one (links, security, website requirements)."""
    if site_map.login_error:
        pytest.fail(f"Could not log in: {site_map.login_error}", pytrace=False)
    if site_map.blocked:
        pytest.skip(f"Not tested: {site_map.blocked}")
    if site_map.state:  # the role's session, so link checks reach pages behind the login
        page.context.add_cookies(site_map.state.get("cookies", []))
    return BasePage(page, settings, request, test_artifacts_dir)


def check_on_device(playwright, settings: Settings, site: SiteMap, device: str,  # noqa: ANN001
                    shots: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """Open every page the crawl found on a real phone profile (as the same role). Returns the problems found
    and (label, screenshot) pairs: the first page, plus every page with a problem."""
    from ui_automation.accessibility import scan_rules

    if device not in playwright.devices:
        near = [d for d in playwright.devices if device.split()[0].lower() in d.lower()][:8]
        return [f"{device}: not a known device{' (did you mean: ' + ', '.join(near) + '?)' if near else ''}"], []
    profile = dict(playwright.devices[device])
    engine = profile.pop("default_browser_type")
    browser = getattr(playwright, engine).launch(headless=settings.headless)
    context = browser.new_context(**profile, storage_state=site.state, ignore_https_errors=accepts_self_signed(settings))
    page = context.new_page()
    page.set_default_timeout(settings.timeout_ms)
    errors: list[str] = []
    page.on("pageerror", lambda err: errors.append(f"JavaScript error: {(err.message or str(err)).splitlines()[0][:150]}"))
    page.on("response", lambda r: errors.append(f"{r.request.method} {urlparse(r.url).path or '/'} -> HTTP {r.status}")
            if r.status >= 400 and not r.request.is_navigation_request() else None)
    problems: list[str] = []
    gallery: list[tuple[str, str]] = []
    shots.mkdir(parents=True, exist_ok=True)
    try:
        for n, url in enumerate(u for u in site.pages if not site.results.get(u, PageResult(u)).load_error):
            errors.clear()
            where = short(url, site.home)
            try:
                page.goto(url, wait_until=settings.navigation_wait_until)  # type: ignore[arg-type]
                try:
                    page.wait_for_function(CONTENT_JS, timeout=max(settings.audit.content_wait_ms, 1))
                    page.evaluate(STABLE_JS)
                except PlaywrightError:
                    pass
                found = page.evaluate(MOBILE_JS)
            except PlaywrightError as exc:
                problems.append(f"{device} {where}: could not be opened ({exc.message.splitlines()[0][:120]})")
                continue
            here = []
            if not found["viewport"]:
                here.append("not set up for phones (no <meta name=viewport content=\"width=device-width\">), "
                            "so the phone shows a shrunken desktop page")
            if found["overflow"]:
                here.append(f"scrolls sideways: {found['overflow']}px wide on a {page.viewport_size['width']}px screen")
            if found["small"]:
                here.append("text smaller than 12px: " + ", ".join(found["small"]))
            for v in scan_rules(page, ["target-size"]):  # WCAG 2.5.8, on the phone's own layout
                here.append(f"hard to tap: {v.count} button(s) or link(s) smaller than 24x24px and too close to "
                            f"others (WCAG 2.5.8), e.g. {', '.join(v.targets[:3])}")
            here += list(dict.fromkeys(errors))[:5]
            problems += [f"{device} {where}: {p}" for p in here]
            if here or n == 0:
                shot = shots / f"{re.sub(r'[^A-Za-z0-9]+', '_', device)}_{n:03d}.png"
                try:
                    page.screenshot(path=str(shot))
                    gallery.append((f"{device} {where}", str(shot)))
                except PlaywrightError:
                    pass
    finally:
        context.close()
        browser.close()
    return problems, gallery


def first_role(settings: Settings, role: str) -> bool:
    """Site-wide checks (HTTPS, website requirements) run once: logged out, or as the first role."""
    return role == roles_to_audit(settings)[0]


def show(request: pytest.FixtureRequest, site_map: SiteMap, urls: list[str]) -> None:
    """Put these pages' screenshots in this test's report, in order."""
    for url in urls:
        result = site_map.results.get(url)
        if result and result.screenshot:
            request.node.step_screenshots.append((short(url, site_map.home), result.screenshot))
