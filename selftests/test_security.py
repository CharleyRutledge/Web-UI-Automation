"""Security: hostile content in reports, secrets never leaking, and the report staying self-contained."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest

from harness import CliRun, base_config, invoke_cli, make_summary
from servers import Reply
from ui_automation.reporting.report_html import render_summary_html
from ui_automation.reporting.summary import TestResult

SECRETS = {
    "TELEGRAM_BOT_TOKEN": "987654:SENTINEL-TELEGRAM-TOKEN",
    "ANTHROPIC_API_KEY": "sk-ant-SENTINEL-ANTHROPIC-KEY",
    "EMAIL_SMTP_PASSWORD": "SENTINEL-SMTP-PASSWORD",
}


@pytest.fixture(scope="module")
def hostile_run(tmp_path_factory: pytest.TempPathFactory, site) -> CliRun:
    # GitHub's run variables are set so the report carries the CI-run link exactly as it does in CI.
    run = invoke_cli(tmp_path_factory.mktemp("hostile"),
                     ["scenarios/test_browser_scenarios.py", "-k", "hostile or missing_element"],
                     config=base_config(site.url),
                     env={"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "1"})
    assert run.run_dir is not None, run.output
    return run


@pytest.mark.parametrize("report", ["summary.html", "report.html"])
def test_hostile_test_names_and_messages_are_escaped(hostile_run: CliRun, report: str) -> None:
    html = (hostile_run.run_dir / report).read_text(encoding="utf-8")
    for raw in ("<script>alert(1)</script>", '<img src=x onerror="alert(2)">'):
        assert raw not in html, f"{raw} is live HTML in {report}"
    assert "alert(1)" in html and "alert(2)" in html, "the text is still shown, just escaped"


def test_summary_report_loads_nothing_from_the_internet(hostile_run: CliRun) -> None:
    html = (hostile_run.run_dir / "summary.html").read_text(encoding="utf-8")
    # Only what a browser fetches by itself counts (src=, stylesheets, scripts). Clickable links such as the
    # CI run, trace.playwright.dev or "How to fix" pages load nothing until the reader chooses to follow them.
    loaded = [s for s in re.findall(r'\bsrc="([^"]+)"', html) if not s.startswith("data:")]
    assert loaded == [], loaded
    assert "<link" not in html.lower() and "@import" not in html
    # Exactly one script (the Copy buttons), inline, and the only one the page's CSP allows to run.
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S | re.I)
    assert len(scripts) == 1 and scripts[0][0] == "", scripts
    digest = base64.b64encode(hashlib.sha256(scripts[0][1].encode()).digest()).decode()
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html).group(1)
    assert f"script-src 'sha256-{digest}'" in csp and "default-src 'none'" in csp
    assert "url(" not in html.split("</style>")[0].split("<style>")[-1], "no external CSS resources"


def test_hostile_ai_text_is_escaped(tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path)
    html = render_summary_html(s, ai_text="<script>steal()</script> & <b>bold</b>")
    assert "<script>steal()" not in html and "&lt;script&gt;steal()" in html


def test_hostile_markup_in_a_fix_is_shown_not_run(tmp_path: Path) -> None:
    """Fixes quote the site's own HTML, which could be anything."""
    from ui_automation.accessibility import Violation

    s, _ = make_summary(tmp_path)
    raw = {"id": "image-alt", "impact": "critical", "help": "Images must have alternative text", "tags": ["wcag111"],
           "nodes": [{"html": '<img src=x onerror="alert(7)"><script>alert(6)</script>', "target": ["img"]}]}
    s.tests[0].accessibility = [{"url": "http://x/", "standard": "wcag21aa", "violations": [Violation.from_axe(raw).__dict__]}]
    html = render_summary_html(s)
    assert 'onerror="alert(7)"' not in html and "<script>alert(6)" not in html
    assert "onerror=&quot;alert(7)&quot;" in html


def test_injected_script_would_be_blocked_by_the_csp(tmp_path: Path) -> None:
    """Defence in depth: even if escaping ever failed, the browser refuses to run anything but our script."""
    from playwright.sync_api import sync_playwright

    s, _ = make_summary(tmp_path)
    html = render_summary_html(s).replace("</main>", "<script>document.title='pwned'</script></main>")
    page_file = tmp_path / "injected.html"
    page_file.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(page_file.as_uri())
        assert page.title() != "pwned"
        assert page.evaluate("document.documentElement.classList.contains('js')"), "our own script still runs"
        browser.close()


def test_hostile_media_paths_cannot_escape_the_page(tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path)
    s.tests[0].screenshots = ['x" onerror="alert(9)', "../../../../etc/passwd"]
    s.tests[0].steps = ['<b onmouseover="alert(8)">step</b>', "second"]
    html = render_summary_html(s)
    assert 'onerror="alert(9)' not in html and '<b onmouseover' not in html and "root:x:" not in html


def test_secrets_never_reach_output_or_artifacts(tmp_path: Path, site, smtp, telegram_api, anthropic_api) -> None:
    """A failing run with every integration on, where every integration then fails (the error paths are
    where tokens usually leak). Nothing it prints or writes may contain any secret."""
    controller, _ = smtp(login=("robot", "different-password"))
    telegram_api.script("sendDocument", Reply(401, {"ok": False, "description": "Unauthorized"}))
    anthropic_api.script("/v1/messages", Reply(401, {"type": "error", "error": {"type": "authentication_error",
                                                                               "message": "invalid x-api-key"}}))
    cfg = base_config(site.url, ai={"enabled": True, "timeout_seconds": 5}, notifications={
        "email": {"enabled": True, "smtp_host": "127.0.0.1", "smtp_port": controller.port, "use_tls": False,
                  "smtp_user": "robot", "smtp_password": "${EMAIL_SMTP_PASSWORD}", "from_addr": "a@b.c",
                  "to_addrs": ["d@e.f"], "timeout_seconds": 3},
        "telegram": {"enabled": True, "bot_token": "${TELEGRAM_BOT_TOKEN}", "chat_id": "5"},
    })
    run = invoke_cli(tmp_path, ["scenarios/test_browser_scenarios.py", "-k", "missing_element"], config=cfg, env={
        **SECRETS, "TELEGRAM_API_BASE": telegram_api.url, "ANTHROPIC_BASE_URL": anthropic_api.url})
    assert run.returncode == 1, run.output
    assert "Telegram notification skipped" in run.output and "Claude analysis skipped" in run.output
    assert "Email notification skipped" in run.output

    leaks = [name for name, value in SECRETS.items() if value in run.output]
    assert not leaks, f"printed: {leaks}"
    for path in run.reports_dir.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            found = [name for name, value in SECRETS.items() if value.encode() in data]
            assert not found, f"{path.relative_to(run.reports_dir)} contains {found}"
    # The secrets really were in use (so the scan above is meaningful).
    assert any(SECRETS["TELEGRAM_BOT_TOKEN"] in c.path for c in telegram_api.calls())


def test_report_file_names_are_safe(tmp_path: Path) -> None:
    from ui_automation.reporting.telegram_notify import build_caption

    s, _ = make_summary(tmp_path)
    s.tests.append(TestResult("tests/x.py::test_evil[../../etc]", "failed", 1.0, message="‮evil"))
    assert "\n" not in build_caption(s).splitlines()[0]
