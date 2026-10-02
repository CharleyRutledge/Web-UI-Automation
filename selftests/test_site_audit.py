"""The whole-site audit (site_audit/) through the real CLI, against a local site with one planted
problem of each kind, and a clean one. Every problem must be found; nothing else may be reported."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from harness import CliRun, base_config, invoke_cli


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{title}</title></head><body><main>{body}</main></body></html>')


FLAWED = {
    "/": _page("Home", '<h1>Home</h1><nav><a href="/about">About</a> <a href="/data">Data</a> <a href="/wide">Wide</a> '
                       '<a href="/js-error">JS</a> <a href="/gone">Gone</a> <a href="/report.pdf">Report</a> '
                       '<a href="/about#team">Team</a> <a href="/about?utm=x">About again</a></nav>'),
    "/about": _page("About", '<h1>About</h1><img src="/missing.png" alt="Missing"><a href="/">Home</a>'),
    "/data": _page("Data", '<p>No heading here</p><script>fetch("/api/data?token=s3cret")</script>'),  # API 404
    "/wide": _page("Wide", '<h1>Wide</h1><div class="table-wrap" style="width:1200px">wide</div>'),
    "/js-error": _page("JS", "<h1>JS</h1><script>undefinedFunction()</script>"),
    "/report.pdf": "%PDF-1.4",
}
# Everything an Irish website needs (privacy, cookies, accessibility statement, company and contact details).
_FOOTER = ('<footer><a href="/privacy">Privacy notice</a> <a href="/accessibility">Accessibility statement</a> '
           '<a href="/contact">Contact us</a><p>Example Ltd, registered in Ireland, company number 654321. Registered '
           'office: 1 Main Street, Dublin 2. Email: <a href="mailto:hi@example.ie">hi@example.ie</a></p></footer>')
# Links big enough to tap on a phone (WCAG 2.5.8), as a well-built site has them.
_TAP = "<style>a{display:inline-block;min-width:24px;min-height:24px;margin:2px}</style>"
CLEAN = {
    "/": _page("Home", _TAP + '<h1>Home</h1><a href="/about">About</a>' + _FOOTER),
    "/privacy": _page("Privacy", "<h1>Privacy notice</h1>"),
    "/accessibility": _page("Accessibility", "<h1>Accessibility statement</h1>"),
    "/contact": _page("Contact", "<h1>Contact us</h1>"),
    # Built in the browser after a slow request, like a React/Vue page: the audit must wait for it.
    "/about": _page("About", _TAP + '<div id="app">Loading…</div><script>fetch("/slow").then(() => {'
                             'document.getElementById("app").innerHTML = \'<h1>About</h1><a href="/">Home</a>\';})</script>'),
    "/slow": "{}",
}
SECURE_HEADERS = {"Strict-Transport-Security": "max-age=31536000", "X-Content-Type-Options": "nosniff",
                  "Referrer-Policy": "strict-origin", "Content-Security-Policy": "frame-ancestors 'none'"}


def _serve(pages: dict[str, str]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/slow":
                time.sleep(1.5)
            body = pages.get(path)
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "application/pdf" if path.endswith(".pdf") else "text/html")
            for k, v in SECURE_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write((body or _page("Not found", "<h1>Not found</h1>")).encode())

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def audit(workdir: Path, url: str) -> CliRun:
    cfg = base_config(url, timeout_ms=10000, artifacts={"video": "off"}, accessibility={"fail_on": "none"},
                      audit={"max_pages": 10, "check_external_links": False, "security_checks": "on"},
                      compliance={"enabled": True},
                      name="Test site audit")
    return invoke_cli(workdir, ["site_audit"], config=cfg)


@pytest.fixture(scope="module")
def flawed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CliRun]:
    for url in _serve(FLAWED):
        yield audit(tmp_path_factory.mktemp("flawed"), url)


def test_crawl_follows_links_once_and_skips_files(flawed: CliRun) -> None:
    assert flawed.test("test_crawl_found_the_site")["outcome"] == "passed"
    scans = flawed.test("test_pages_are_accessible")["accessibility"]
    paths = sorted(s["url"].split(":", 2)[2].split("/", 1)[1] for s in scans)
    # /gone is a page too (its 404 is reported by the page test); /report.pdf is a file; #team and ?utm are dupes.
    assert paths == ["", "about", "data", "gone", "js-error", "wide"], paths


@pytest.mark.parametrize(
    "test, expected",
    [
        ("test_every_page_loads_with_a_title_and_heading", ["/data: no main heading (h1)", "/gone: HTTP 404"]),
        ("test_no_javascript_errors", ["/js-error: undefinedFunction is not defined"]),
        ("test_no_broken_links", ["/gone (linked from /): HTTP 404"]),
        ("test_no_broken_images", ["/about: http://127.0.0.1:"]),
        ("test_no_network_errors", ["2 failed network request(s)", "/about: GET /missing.png -> HTTP 404",
                                    "/data: GET /api/data -> HTTP 404"]),
        ("test_pages_fit_every_screen_size", ["1 page(s) that need sideways scrolling",
                                              "/wide: on small phone (320px) it is 1208px wide (widest element: "
                                              "div.table-wrap); on phone (375px)", "; on tablet (768px)"]),
        ("test_served_securely", ["the site is not served over HTTPS"]),
        ("test_meets_website_requirements", ["Privacy notice: No link to a privacy notice", "(GDPR Art. 13/14",
                                             "Accessibility statement: No link to an accessibility statement"]),
    ],
)
def test_each_planted_problem_is_found(flawed: CliRun, test: str, expected: list[str]) -> None:
    t = flawed.test(test)
    assert t["outcome"] == "failed"
    text = t["message"] + t["details"]
    for item in expected:
        assert item in text, (item, text)


def test_network_errors_never_show_query_strings(flawed: CliRun) -> None:
    t = flawed.test("test_no_network_errors")
    assert "s3cret" not in t["message"] + t["details"]  # query strings can carry tokens


def test_each_screen_size_that_breaks_has_a_screenshot(flawed: CliRun) -> None:
    t = flawed.test("test_pages_fit_every_screen_size")
    assert t["steps"] == ["/wide on small phone (320px)", "/wide on phone (375px)", "/wide on tablet (768px)"]
    assert all((flawed.run_dir / p).is_file() or Path(p).is_file() for p in t["screenshots"])
    assert "/wide: on laptop" not in t["message"] + t["details"]  # 1366px: the 1200px element fits


def test_problems_are_counted_exactly(flawed: CliRun) -> None:
    assert "2 page problem(s)" in flawed.test("test_every_page_loads")["message"]
    assert "1 broken image(s)" in flawed.test("test_no_broken_images")["message"]
    assert "1 broken link(s)" in flawed.test("test_no_broken_links")["message"]


def test_clean_site_passes_everything_but_https(tmp_path: Path) -> None:
    for url in _serve(CLEAN):
        run = audit(tmp_path, url)
    failed = {t["nodeid"].split("::")[1].split("[")[0]: t["message"] + t["details"][-600:]
              for t in run.summary["tests"] if t["outcome"] == "failed"}
    # A local test server has no HTTPS; every header is present, so that is the only finding.
    assert list(failed) == ["test_served_securely"], failed
    assert "1 security finding(s)" in failed["test_served_securely"]


def test_unreachable_local_app_is_explained_before_any_test(tmp_path: Path) -> None:
    from servers import free_port

    port = free_port()
    run = audit(tmp_path, f"http://127.0.0.1:{port}")
    assert run.returncode == 2 and run.run_dir is None
    assert f"Nothing is answering at http://127.0.0.1:{port}" in run.output


def test_report_names_the_site_and_lists_requirements(flawed: CliRun) -> None:
    html = (flawed.run_dir / "summary.html").read_text(encoding="utf-8")
    assert '<p class="site">Test site audit</p>' in html and "<title>Test site audit: FAILED</title>" in html
    assert "Website requirements (Ireland / EU)" in html


def test_report_only_lists_requirements_without_failing(tmp_path: Path) -> None:
    for url in _serve(FLAWED):
        cfg = base_config(url, artifacts={"video": "off"}, compliance={"enabled": True, "report_only": True},
                          audit={"max_pages": 1, "check_external_links": False})
        run = invoke_cli(tmp_path, ["site_audit", "-k", "requirements"], config=cfg)
    t = run.test("test_meets_website_requirements")
    assert t["outcome"] == "passed" and any(not r["passed"] for r in t["compliance"][0]["results"])


def test_shared_accessibility_issue_is_reported_once_with_its_pages(tmp_path: Path) -> None:
    """Meridian Data's first run said '1 accessibility issue(s)' on 23 pages without naming it. The same issue in a
    shared footer must come out as one line: what it is, on which pages, and that it is probably one fix."""
    footer = '<footer><p style="color:#bbb;background:#fff">Grey footer text</p></footer>'
    shared = {path: _page(title, f'<h1>{title}</h1><a href="/">Home</a> <a href="/a">A</a> <a href="/b">B</a>{footer}')
              for path, title in (("/", "Home"), ("/a", "A"), ("/b", "B"))}
    for url in _serve(shared):
        cfg = base_config(url, artifacts={"video": "off"}, accessibility={"fail_on": "serious"},
                          audit={"max_pages": 5, "check_external_links": False})
        run = invoke_cli(tmp_path, ["site_audit", "-k", "accessible"], config=cfg)
    message = run.test("test_pages_are_accessible")["message"] + run.test("test_pages_are_accessible")["details"]
    assert "1 accessibility issue type(s) at or above 'serious'" in message
    assert "[serious] Elements must meet minimum color contrast ratio thresholds (WCAG 1.4.3): 3 page(s): /, /a, /b" in message
    assert "probably one fix in a shared header, footer or style" in message


# An app that shows nothing at first and builds the page 3 s later without any network request (so the network
# is quiet the whole time), and a page that never gets past "Loading...".
LATE = {
    "/": _page("Home", '<div id="root"></div><script>setTimeout(() => { document.getElementById("root").innerHTML ='
                       ' \'<h1>Dashboard</h1><a href="/deposits">Deposits</a> <a href="/stuck">Stuck</a>\'; }, 3000)'
                       "</script>"),
    "/deposits": _page("Deposits", '<div id="root"><div role="progressbar" aria-label="Loading">…</div></div>'
                                   '<script>setTimeout(() => { document.getElementById("root").innerHTML ='
                                   ' "<h1>Deposits</h1><p>3 deposits</p>"; }, 2000)</script>'),
    "/stuck": _page("Stuck", "<p>Loading…</p>"),
}


def test_pages_are_measured_once_their_content_is_there(tmp_path: Path) -> None:
    """A blank or loading screen is not measured as the page: the audit waits for real content (and its links),
    and a page that never shows any is reported as still loading, not as 'no heading'."""
    for url in _serve(LATE):
        cfg = base_config(url, timeout_ms=10000, artifacts={"video": "off"}, accessibility={"fail_on": "none"},
                          audit={"max_pages": 10, "check_external_links": False, "content_wait_ms": 6000})
        run = invoke_cli(tmp_path, ["site_audit", "-k", "crawl or title"], config=cfg)
    pages = sorted(s.split(". ", 1)[-1] for s in run.test("test_crawl_found_the_site")["steps"])
    assert pages == ["/", "/deposits", "/stuck"], pages  # the links only exist once the page is built
    t = run.test("test_every_page_loads_with_a_title_and_heading")
    text = t["message"] + t["details"]
    assert "1 page problem(s)" in text, text
    assert "/stuck: still blank or loading after 6 s" in text
    assert "no main heading" not in t["message"]  # / and /deposits were measured once built


def test_every_browser_audits_the_pages_and_site_wide_checks_run_once(tmp_path: Path) -> None:
    """browsers: [chromium, firefox]: each page is measured in each browser; checks that do not depend on the
    browser (links, HTTPS, legal pages) run once. GitHub's self-test job installs Firefox for this."""
    from ui_automation.browsers import missing_browsers

    if missing_browsers(["firefox"]):
        pytest.skip("Firefox is not installed here (CI installs it: python -m playwright install firefox)")
    for url in _serve(CLEAN):
        cfg = base_config(url, browsers=["chromium", "firefox"], browser_rotation="off", artifacts={"video": "off"},
                          audit={"max_pages": 5, "check_external_links": False, "security_checks": "on"},
                          compliance={"enabled": True}, accessibility={"fail_on": "none"})
        run = invoke_cli(tmp_path, ["site_audit"], config=cfg)
    ids = {t["nodeid"].split("::")[1] for t in run.summary["tests"]}
    for check in ("test_crawl_found_the_site", "test_pages_fit_every_screen_size", "test_pages_are_accessible"):
        assert {f"{check}[public-chromium]", f"{check}[public-firefox]"} <= ids, ids
    for check in ("test_no_broken_links", "test_served_securely", "test_meets_website_requirements"):
        assert [i for i in ids if i.startswith(check)] == [f"{check}[public-chromium]"], ids
    chromium, firefox = (run.test(f"test_pages_are_accessible[public-{b}]") for b in ("chromium", "firefox"))
    assert firefox["outcome"] == "passed", firefox
    assert len(firefox["accessibility"]) == len(chromium["accessibility"]) >= 4  # the same pages, in each browser


PHONE_PAGES = {
    "/": _page("Home", '<h1>Home</h1><p>Welcome.</p><a href="/desktop-only">Desktop-only page</a> '
                       '<p>Read our <a href="/">terms</a> in this sentence, which is fine to tap.</p>'),
    # No viewport tag, 9px text, and two 16px buttons side by side.
    "/desktop-only": ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Old</title></head><body>'
                      '<main><h1>Old page</h1><p style="font-size:9px">Tiny print</p>'
                      '<button style="width:16px;height:16px;padding:0">a</button>'
                      '<button style="width:16px;height:16px;padding:0">b</button></main></body></html>'),
}


def test_mobile_devices_find_phone_problems(tmp_path: Path) -> None:
    for url in _serve(PHONE_PAGES):
        cfg = base_config(url, artifacts={"video": "off"}, accessibility={"fail_on": "none"},
                          audit={"max_pages": 5, "check_external_links": False, "mobile_devices": ["Pixel 7"]})
        run = invoke_cli(tmp_path, ["site_audit", "-k", "mobile"], config=cfg)
    t = run.test("test_works_on_mobile_devices")
    text = t["message"] + t["details"]
    assert t["outcome"] == "failed", run.output
    assert "Pixel 7 /desktop-only: not set up for phones" in text
    assert "Pixel 7 /desktop-only: text smaller than 12px: p (9px)" in text
    assert "Pixel 7 /desktop-only: hard to tap: 2 button(s) or link(s) smaller than 24x24px" in text  # axe target-size
    assert "Pixel 7 /:" not in text  # the good page, with a link inside a sentence, is fine
    assert t["steps"] == ["Pixel 7 /", "Pixel 7 /desktop-only"]  # the first page, and the page with problems


def test_unknown_device_is_named(tmp_path: Path) -> None:
    for url in _serve(PHONE_PAGES):
        cfg = base_config(url, artifacts={"video": "off"}, audit={"max_pages": 1, "mobile_devices": ["Pixel 99"]})
        run = invoke_cli(tmp_path, ["site_audit", "-k", "mobile"], config=cfg)
    t = run.test("test_works_on_mobile_devices")
    assert "Pixel 99: not a known device (did you mean: Pixel" in t["message"] + t["details"]
