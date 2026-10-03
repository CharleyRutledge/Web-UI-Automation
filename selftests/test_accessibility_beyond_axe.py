"""The accessibility checks axe-core cannot do on its own (keyboard, focus, text spacing, motion, alt quality,
captions, refresh, heading levels), in a real browser: each planted problem is found, and a well-built page
passes them all."""

from __future__ import annotations

from typing import Iterator

import pytest
from playwright.sync_api import Page, sync_playwright

from ui_automation.accessibility.beyond_axe import check, keyboard, page_checks, text_spacing


@pytest.fixture(scope="module")
def page() -> Iterator[Page]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page()
        browser.close()


GOOD = """<!doctype html><html lang="en"><head><title>Good</title><style>
  :focus-visible { outline: 3px solid #4c7cf0; }
  .card { min-height: 40px; overflow: hidden; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner { animation: spin 1s infinite; }
</style></head><body><main><h1>Deposits</h1><h2>This month</h2><h3>Detail</h3>
  <a href="#a">Open</a> <button>Save</button> <input aria-label="Name">
  <img src="chart.png" alt="Bar chart of deposits by month">
  <img src="divider.png" alt="">
  <video src="intro.mp4" controls><track kind="captions" src="intro.vtt" srclang="en"></video>
  <div class="spinner">Working</div><button>Pause animation</button>
  <p class="card">Some text that grows with its box.</p>
</main></body></html>"""

BAD = """<!doctype html><html lang="en"><head><title>Bad</title>
<meta http-equiv="refresh" content="30">
<style>
  button.plain:focus { outline: none; }
  .tile { cursor: pointer; }
  .clip { height: 20px; overflow: hidden; font-size: 14px; line-height: 20px; width: 200px; }
  @keyframes slide { to { transform: translateX(10px); } }
  .ticker { animation: slide 2s infinite; }
</style></head><body><main><h1>Home</h1><h3>Skipped a level</h3>
  <button class="plain">Invisible focus</button>
  <div class="tile" onclick="location.hash='x'">Open account</div>
  <img src="/img/photo_2.jpg" alt="image"> <img src="/img/banner.png" alt="banner.png">
  <video src="promo.mp4"></video>
  <div class="ticker">Latest rates</div>
  <p class="clip">A sentence that fits only with tight spacing</p>
</main></body></html>"""

TRAP = """<!doctype html><html lang="en"><head><title>Trap</title></head><body><main><h1>Form</h1>
  <input id="a" aria-label="First"><input id="b" aria-label="Second">
  <script>document.getElementById('b').addEventListener('keydown', e => {
    if (e.key === 'Tab') { e.preventDefault(); document.getElementById('a').focus(); } });</script>
  <a href="#never">Never reached</a> <button>Also never reached</button>
</main></body></html>"""


def rules(found) -> dict[str, list[str]]:
    return {v.rule: v.targets for v in found}


def test_a_well_built_page_passes_everything(page: Page) -> None:
    page.set_content(GOOD)
    assert check(page) == []


def test_each_planted_problem_is_found(page: Page) -> None:
    page.set_content(BAD)
    found = rules(check(page))
    assert found["focus-visible"] == ['button.plain "Invisible focus"']
    assert found["mouse-only"] == ['div.tile "Open account"']
    assert found["alt-meaningless"] == ['img alt="image"', 'img alt="banner.png"']
    assert found["video-captions"] == ["video promo.mp4"]
    assert found["auto-refresh"] == ['meta http-equiv="refresh" content="30"']
    assert found["endless-motion"] == ["div.ticker"]
    assert found["heading-skip"] == ['h1 then h3 "Skipped a level"']
    assert found["text-spacing"] == ['p.clip "A sentence that fits only with"']
    assert "keyboard-trap" not in found


def test_findings_carry_the_wcag_criterion_and_a_fix(page: Page) -> None:
    page.set_content(BAD)
    by_rule = {v.rule: v for v in check(page)}
    assert by_rule["focus-visible"].criteria == ["2.4.7"] and by_rule["focus-visible"].impact == "serious"
    assert ":focus-visible" in by_rule["focus-visible"].fixes[0]["note"]
    assert by_rule["text-spacing"].help_url.startswith("https://www.w3.org/WAI/WCAG22/Understanding/")


def test_keyboard_trap_is_found(page: Page) -> None:
    page.set_content(TRAP)
    found = rules(keyboard(page))
    assert found["keyboard-trap"][0].startswith("input#"), found  # Tab keeps going back between the two fields


def test_text_spacing_check_leaves_the_page_as_it_was(page: Page) -> None:
    page.set_content(BAD)
    before = page.evaluate("() => document.body.innerHTML")
    assert text_spacing(page)
    assert page.evaluate("() => document.body.innerHTML") == before  # every style put back


def test_decorative_images_and_reduced_motion_pages_are_left_alone(page: Page) -> None:
    page.set_content('<main><h1>x</h1><img src="a.png" alt=""><div style="animation: none">Still</div></main>')
    assert page_checks(page) == []


def test_text_spacing_works_under_a_strict_content_security_policy(page: Page) -> None:
    """Well-secured sites block added <style> tags; the check must still see the text being cut off."""
    def serve(route) -> None:  # noqa: ANN001 - Playwright Route
        if route.request.url.endswith(".css"):
            route.fulfill(body=".clip{height:20px;overflow:hidden;width:200px;line-height:20px}", content_type="text/css")
        else:
            route.fulfill(body='<!doctype html><html lang="en"><head><link rel="stylesheet" href="/s.css"></head>'
                               '<body><p class="clip">A sentence that fits only with tight spacing</p></body></html>',
                          content_type="text/html",
                          headers={"Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'none'"})

    page.route("http://csp.test/**", serve)
    page.goto("http://csp.test/")
    assert [v.rule for v in text_spacing(page)] == ["text-spacing"]
