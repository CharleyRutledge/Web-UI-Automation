"""A short, phone-friendly HTML report sent with notifications (the full pytest-html report stays in CI)."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from ui_automation.reporting.summary import RunSummary, TestResult

_MAX_EMBEDDED_SCREENSHOTS = 5

_CSS = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1b1f24; --muted:#667085; --line:#e4e7ec;
        --pass:#16794a; --pass-bg:#e7f6ee; --fail:#c0322b; --fail-bg:#fdecea; --skip:#8a6d00; --skip-bg:#fff6d6;
        --code-bg:#f2f4f7; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --card:#181b21; --text:#e7e9ee; --muted:#98a2b3; --line:#2a2f38;
          --pass:#4ccf8a; --pass-bg:#163325; --fail:#ff7b72; --fail-bg:#3a1a19; --skip:#e3c35a; --skip-bg:#3a3214;
          --code-bg:#11141a; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
main { max-width:720px; margin:0 auto; padding:16px; }
.hero { border-radius:14px; padding:20px; margin-bottom:16px; }
.hero.pass { background:var(--pass-bg); color:var(--pass); }
.hero.fail { background:var(--fail-bg); color:var(--fail); }
.hero h1 { margin:0; font-size:26px; letter-spacing:.3px; }
.hero p { margin:6px 0 0; color:var(--text); }
.hero .meta { color:var(--muted); font-size:13px; margin-top:8px; word-break:break-all; }
.tiles { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:16px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px; text-align:center; }
.tile b { display:block; font-size:22px; }
.tile span { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
.tile.pass b { color:var(--pass); } .tile.fail b { color:var(--fail); } .tile.skip b { color:var(--skip); }
h2 { font-size:16px; margin:20px 0 8px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:12px; }
.card.fail { border-left:4px solid var(--fail); }
.name { font-weight:600; word-break:break-word; }
.file { color:var(--muted); font-size:13px; word-break:break-all; }
.step { margin-top:8px; }
.tag { display:inline-block; font-weight:500; font-size:12px; color:var(--muted); border:1px solid var(--line);
       border-radius:999px; padding:0 8px; margin-left:4px; vertical-align:1px; }
.msg { color:var(--fail); margin:8px 0; white-space:pre-wrap; word-break:break-word; }
.ai { white-space:pre-wrap; }
img { width:100%; border:1px solid var(--line); border-radius:8px; margin-top:8px; }
details { margin-top:8px; } summary { cursor:pointer; color:var(--muted); font-size:13px; }
pre { background:var(--code-bg); border-radius:8px; padding:10px; overflow-x:auto; font-size:12px; margin:8px 0 0; }
ul.tests { list-style:none; margin:0; padding:0; }
ul.tests li { display:flex; gap:10px; align-items:flex-start; padding:10px 0; border-bottom:1px solid var(--line); }
ul.tests li:last-child { border-bottom:none; }
.dot { flex:none; width:22px; height:22px; border-radius:50%; display:inline-flex; align-items:center;
       justify-content:center; font-size:12px; font-weight:700; margin-top:1px; }
.dot.passed { background:var(--pass-bg); color:var(--pass); }
.dot.failed, .dot.error { background:var(--fail-bg); color:var(--fail); }
.dot.skipped { background:var(--skip-bg); color:var(--skip); }
.grow { flex:1; min-width:0; }
.dur { color:var(--muted); font-size:13px; white-space:nowrap; }
.foot { color:var(--muted); font-size:13px; margin-top:20px; }
a { color:inherit; }
"""

_DOT = {"passed": "✓", "failed": "✕", "error": "!", "skipped": "–"}


def _fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{int(seconds // 60)} m {seconds % 60:.0f} s"


def _embed_png(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f'<img alt="Last screenshot before the failure" src="data:image/png;base64,{data}">'


def _tile(count: int, label: str, cls: str) -> str:
    # Zero is not news: keep it neutral so a green run doesn't show red numbers.
    return f'<div class="tile {cls if count else ""}"><b>{count}</b><span>{label}</span></div>'


def _variant(test: TestResult) -> str:
    return f' <span class="tag">{escape(test.variant)}</span>' if test.variant else ""


def _failure_card(test: TestResult, embed: bool) -> str:
    screenshot = _embed_png(test.screenshots[-1]) if embed and test.screenshots else ""
    label = "Error during setup/teardown" if test.outcome == "error" else "Failed"
    details = (
        f"<details><summary>Full error</summary><pre>{escape(test.details)}</pre></details>"
        if test.details
        else ""
    )
    step = (
        f'<div class="step">Stopped at step: <b>{escape(test.last_step)}</b></div>' if test.last_step else ""
    )
    shot_note = '<div class="file">Screen at that step:</div>' if screenshot else ""
    return (
        f'<div class="card fail"><div class="name">{escape(test.title)}{_variant(test)}</div>'
        f'<div class="file">{escape(test.file)} · {label} after {_fmt_duration(test.duration)}</div>'
        f"{step}"
        f'<div class="msg">{escape(test.message or "No error message")}</div>'
        f"{shot_note}{screenshot}{details}</div>"
    )


def render_summary_html(summary: RunSummary, ai_text: str | None = None) -> str:
    status_cls = "pass" if summary.ok else "fail"
    title = "PASSED" if summary.ok else "FAILED"
    if summary.ok:
        line = f"All {summary.passed} tests passed." if not summary.skipped else (
            f"{summary.passed} tests passed, {summary.skipped} skipped."
        )
    else:
        bad = summary.failed + summary.errors
        line = f"{bad} of {summary.total} tests failed. Details below."
    when = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    meta = [when, f"took {_fmt_duration(summary.duration)}"]
    if summary.base_url:
        meta.append(f"site: {summary.base_url}")

    parts = [
        f'<section class="hero {status_cls}"><h1>{title}</h1><p>{escape(line)}</p>'
        f'<div class="meta">{escape(" · ".join(meta))}</div></section>',
        '<section class="tiles">'
        + _tile(summary.passed, "Passed", "pass")
        + _tile(summary.failed, "Failed", "fail")
        + _tile(summary.errors, "Errors", "fail")
        + _tile(summary.skipped, "Skipped", "skip")
        + "</section>",
    ]

    problems = summary.problems
    if problems:
        parts.append("<h2>What failed</h2>")
        parts.extend(
            _failure_card(t, embed=i < _MAX_EMBEDDED_SCREENSHOTS) for i, t in enumerate(problems)
        )
    if ai_text:
        parts.append(f'<h2>Claude\'s analysis</h2><div class="card ai">{escape(ai_text)}</div>')

    rows = []
    order = {"error": 0, "failed": 1, "skipped": 2, "passed": 3}
    for t in sorted(summary.tests, key=lambda t: (order.get(t.outcome, 4), t.nodeid)):
        note = f'<div class="file">{escape(t.message)}</div>' if t.outcome == "skipped" and t.message else ""
        rows.append(
            f'<li><span class="dot {t.outcome}">{_DOT.get(t.outcome, "?")}</span>'
            f'<div class="grow"><div class="name">{escape(t.title)}{_variant(t)}</div>'
            f'<div class="file">{escape(t.file)}</div>{note}</div>'
            f'<span class="dur">{_fmt_duration(t.duration)}</span></li>'
        )
    parts.append(f'<h2>All tests ({summary.total})</h2><div class="card"><ul class="tests">{"".join(rows)}</ul></div>')

    trace_note = {
        "retain-on-failure": "traces are kept only for failed tests",
        "off": "tracing is off",
        "on": "traces are kept for every browser test",
    }.get(summary.tracing_mode, "")
    foot = [
        f"Videos: {len(summary.video_files)} · Traces: {len(summary.trace_files)} "
        f"(only browser tests record video{'; ' + trace_note if trace_note else ''})."
    ]
    if summary.run_url:
        foot.append(f'Full report, videos and traces: <a href="{escape(summary.run_url)}">CI run</a>')
    elif summary.run_dir:
        foot.append(f"Full report: {escape(str(summary.run_dir / 'report.html'))}")
    parts.append('<div class="foot">' + "<br>".join(foot) + "</div>")

    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>UI tests: {title}</title><style>{_CSS}</style></head>"
        f"<body><main>{''.join(parts)}</main></body></html>"
    )
