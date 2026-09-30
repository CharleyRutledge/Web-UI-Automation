"""Our own pages (the phone report and the dashboard) must meet WCAG 2.2 AA: automated axe rules plus
the checks axe cannot do on its own (keyboard use, visible focus, reflow, text alternatives)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from harness import CliRun, base_config, invoke_cli
from ui_automation.accessibility import describe, scan


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory, site) -> dict[str, CliRun]:
    work = tmp_path_factory.mktemp("own-a11y")
    failing = invoke_cli(work / "f", ["scenarios/test_browser_scenarios.py", "tests/test_accessibility.py",
                                      "tests/test_compliance.py", "-k",
                                      "missing_element or test_passes or skipped or setup_error or accessible or requirements"],
                         config=base_config(site.url, accessibility={"pages": ["/a11y-bad", "/a11y-good"]},
                                            compliance={"enabled": True, "pages": ["/legal-bad", "/legal-good"]}),
                         reports_dir=work / "reports")
    html = (failing.run_dir / "summary.html").read_text()
    assert "Accessibility:" in html and "Website requirements" in html, "report includes both new sections"
    passing = invoke_cli(work / "p", ["scenarios/test_browser_scenarios.py", "-k", "test_passes"],
                         config=base_config(site.url))
    return {"failing": failing, "passing": passing}


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def dashboard_url(runs) -> Iterator[str]:
    from werkzeug.serving import make_server

    from ui_automation.webui import create_app

    server = make_server("127.0.0.1", 0, create_app(runs["failing"].reports_dir), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/?message=Run+finished"
    server.shutdown()


def pages(runs, dashboard_url) -> dict[str, str]:
    return {
        "report-failing": (runs["failing"].run_dir / "summary.html").as_uri(),
        "report-passing": (runs["passing"].run_dir / "summary.html").as_uri(),
        "dashboard": dashboard_url,
    }


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("name", ["report-failing", "report-passing", "dashboard"])
def test_no_wcag22_aa_violations(browser: Browser, runs, dashboard_url, name: str, scheme: str) -> None:
    page = browser.new_page(color_scheme=scheme)
    page.goto(pages(runs, dashboard_url)[name])
    page.eval_on_selector_all("details", "els => els.forEach(e => e.open = true)")  # scan hidden content too
    violations = scan(page, "wcag22aa")
    assert not violations, describe(violations)


@pytest.mark.parametrize("name", ["report-failing", "dashboard"])
def test_reflows_at_320_css_pixels(browser: Browser, runs, dashboard_url, name: str) -> None:
    """WCAG 1.4.10: no page-level horizontal scrolling at 320 px (400% zoom of a 1280 px screen)."""
    page = browser.new_page(viewport={"width": 320, "height": 640})
    page.goto(pages(runs, dashboard_url)[name])
    page.eval_on_selector_all("details", "els => els.forEach(e => e.open = true)")
    assert page.evaluate("document.documentElement.scrollWidth") <= 320


@pytest.mark.parametrize("name", ["report-failing", "report-passing", "dashboard"])
def test_page_basics(browser: Browser, runs, dashboard_url, name: str) -> None:
    page = browser.new_page()
    page.goto(pages(runs, dashboard_url)[name])
    assert page.get_attribute("html", "lang") == "en"  # 3.1.1
    assert page.title().strip()  # 2.4.2
    assert page.locator("h1").count() == 1  # one main heading
    assert page.locator("main").count() == 1  # a main landmark
    assert page.locator("img:not([alt]), img[alt='']").count() == 0  # 1.1.1 (all our images are informative)


def _tab_through(page: Page, limit: int = 80) -> list[dict]:
    seen = []
    for _ in range(limit):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
            const el = document.activeElement;
            if (!el || el === document.body) return null;
            if (el.dataset.tabSeen) return el.tagName === 'VIDEO' ? 'video-control' : 'again';
            el.dataset.tabSeen = '1';
            const style = getComputedStyle(el);
            return {tag: el.tagName.toLowerCase(), text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 40),
                    outline: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0};
        }""")
        if info == "video-control":  # still inside the player's own buttons (play, volume, fullscreen)
            continue
        if info is None or info == "again":  # left the page or wrapped around to the first element
            break
        seen.append(info)
    return seen


def test_dashboard_is_usable_by_keyboard(browser: Browser, dashboard_url) -> None:
    page = browser.new_page()
    page.goto(dashboard_url)
    stops = _tab_through(page)
    texts = [s["text"] for s in stops]
    assert texts[0] == "Run tests", texts  # the main action comes first
    assert any(t.startswith("Summary") for t in texts) and any(t.startswith("Full report") for t in texts)
    assert all(s["outline"] for s in stops), [s for s in stops if not s["outline"]]  # 2.4.7 visible focus


def test_report_is_usable_by_keyboard(browser: Browser, runs) -> None:
    page = browser.new_page()
    page.goto((runs["failing"].run_dir / "summary.html").as_uri())
    stops = _tab_through(page)
    tags = [s["tag"] for s in stops]
    assert "summary" in tags, "collapsible sections open with the keyboard"
    assert "video" in tags, "video controls are reachable"
    assert any(s["text"] == "Download trace" for s in stops)
    assert all(s["outline"] for s in stops), [s for s in stops if not s["outline"]]


def test_videos_have_text_alternatives(browser: Browser, runs) -> None:
    """WCAG 1.2.1: a silent recording needs a text alternative; ours lists the steps it shows."""
    page = browser.new_page()
    page.goto((runs["failing"].run_dir / "summary.html").as_uri())
    videos = page.locator("video")
    assert videos.count() >= 2
    for i in range(videos.count()):
        v = videos.nth(i)
        assert v.get_attribute("aria-label").startswith("Screen recording of ")
        description = page.locator(f"#{v.get_attribute('aria-describedby')}").inner_text()
        assert description.startswith("Silent screen recording of: ")


def test_status_is_not_only_colour_or_symbols(browser: Browser, runs) -> None:
    """1.4.1 / 1.1.1: pass/fail is written in words for screen readers, not just ✓ ✕ and red/green."""
    page = browser.new_page()
    page.goto((runs["failing"].run_dir / "summary.html").as_uri())
    assert page.locator(".dot[aria-hidden=true]").count() == page.locator(".dot").count() > 0
    spoken = page.locator(".sr-only").all_inner_texts()
    spoken = [t.strip() for t in spoken]
    assert "Failed:" in spoken and "Passed:" in spoken and "Error:" in spoken


def test_copy_button_copies_the_exact_fix(browser: Browser, runs) -> None:
    context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    page.goto((runs["failing"].run_dir / "summary.html").as_uri())
    buttons = page.locator("button.copy")
    assert buttons.count() >= 6 and buttons.first.is_visible()
    assert not page.get_by_text("Press and hold a code box").is_visible(), "the no-JS hint hides when buttons work"
    for i in (0, buttons.count() - 1):
        button = buttons.nth(i)
        code = page.locator(f"#{button.get_attribute('data-copy')}").text_content()
        button.click()
        assert page.evaluate("navigator.clipboard.readText()") == code
        assert page.locator("#copy-status").text_content() == "Fix copied to the clipboard"
        assert button.inner_text().startswith("Copied")
    context.close()


def test_without_javascript_the_fix_can_still_be_selected(browser: Browser, runs) -> None:
    """Some phone viewers show attachments with scripts off: then no dead buttons, and a hint instead."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    page.goto((runs["failing"].run_dir / "summary.html").as_uri())
    assert page.locator("button.copy").count() >= 6 and not page.locator("button.copy").first.is_visible()
    assert page.get_by_text("Press and hold a code box").is_visible()
    assert page.locator("pre.code").first.evaluate("e => getComputedStyle(e).userSelect") == "all"
    context.close()
