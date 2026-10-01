"""The whole-site audit (site_audit/) through the real CLI, against a local site with one planted
problem of each kind, and a clean one. Every problem must be found; nothing else may be reported."""

from __future__ import annotations

import threading
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
    "/data": _page("Data", "<p>No heading here</p>"),
    "/wide": _page("Wide", '<h1>Wide</h1><div class="table-wrap" style="width:1200px">wide</div>'),
    "/js-error": _page("JS", "<h1>JS</h1><script>undefinedFunction()</script>"),
    "/report.pdf": "%PDF-1.4",
}
CLEAN = {
    "/": _page("Home", '<h1>Home</h1><a href="/about">About</a>'),
    "/about": _page("About", '<h1>About</h1><a href="/">Home</a>'),
}
SECURE_HEADERS = {"Strict-Transport-Security": "max-age=31536000", "X-Content-Type-Options": "nosniff",
                  "Referrer-Policy": "strict-origin", "Content-Security-Policy": "frame-ancestors 'none'"}


def _serve(pages: dict[str, str]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
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
                      audit={"max_pages": 10, "check_external_links": False})
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
        ("test_pages_work_on_a_phone", ["/wide: page is 1208px wide on a 375px phone (widest element: div.table-wrap)"]),
        ("test_served_securely", ["the site is not served over HTTPS"]),
    ],
)
def test_each_planted_problem_is_found(flawed: CliRun, test: str, expected: list[str]) -> None:
    t = flawed.test(test)
    assert t["outcome"] == "failed"
    text = t["message"] + t["details"]
    for item in expected:
        assert item in text, (item, text)


def test_problems_are_counted_exactly(flawed: CliRun) -> None:
    assert "2 page problem(s)" in flawed.test("test_every_page_loads")["message"]
    assert "1 broken image(s)" in flawed.test("test_no_broken_images")["message"]
    assert "1 broken link(s)" in flawed.test("test_no_broken_links")["message"]


def test_clean_site_passes_everything_but_https(tmp_path: Path) -> None:
    for url in _serve(CLEAN):
        run = audit(tmp_path, url)
    failed = {t["nodeid"].split("::")[1].split("[")[0]: t["message"] for t in run.summary["tests"] if t["outcome"] != "passed"}
    # A local test server has no HTTPS; every header is present, so that is the only finding.
    assert list(failed) == ["test_served_securely"], failed
    assert "1 security finding(s)" in failed["test_served_securely"]


def test_unreachable_site_fails_clearly(tmp_path: Path) -> None:
    from servers import free_port

    run = audit(tmp_path, f"http://127.0.0.1:{free_port()}")
    t = run.test("test_crawl_found_the_site")
    assert t["outcome"] == "failed" and "ERR_CONNECTION_REFUSED" in t["message"] + t["details"]
