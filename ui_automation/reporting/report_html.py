"""A short, phone-friendly HTML report sent with notifications (the full pytest-html report stays in CI)."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from ui_automation.reporting.summary import RunSummary, TestResult

# The report travels as one file (Telegram's limit is 50 MB, many mail servers stop at ~25 MB),
# so media is embedded until this budget is used; anything beyond it is linked to the CI run.
_EMBED_BUDGET_BYTES = 18 * 1024 * 1024
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4", ".webm": "video/webm", ".zip": "application/zip"}

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
.hero .meta { color:var(--text); opacity:.85; font-size:13px; margin-top:8px; overflow-wrap:anywhere; }
.tiles { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:16px; }
@media (max-width:360px) { .tiles { grid-template-columns:repeat(2,1fr); } }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px; text-align:center; }
.tile b { display:block; font-size:22px; }
.tile span { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
.tile.pass b { color:var(--pass); } .tile.fail b { color:var(--fail); } .tile.skip b { color:var(--skip); }
h2 { font-size:16px; margin:20px 0 8px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:12px; }
.card.fail { border-left:4px solid var(--fail); }
.name { font-weight:600; word-break:break-word; }
.file { color:var(--muted); font-size:13px; overflow-wrap:anywhere; }
.step { margin-top:8px; }
.tag { display:inline-block; font-weight:500; font-size:12px; color:var(--muted); border:1px solid var(--line);
       border-radius:999px; padding:0 8px; margin-left:4px; vertical-align:1px; }
ul.checks { list-style:none; padding-left:0; } ul.checks .dot { margin-right:6px; }
ul.issues, ul.manual { margin:8px 0 0; padding-left:18px; } li.issue { margin:10px 0; }
ul.targets { margin:4px 0 0; padding-left:16px; font-size:12px; } code { word-break:break-all; }
.impact-critical, .impact-serious { color:var(--fail); border-color:var(--fail); }
.impact-moderate, .impact-minor { color:var(--skip); border-color:var(--skip); }
.ok-text { color:var(--pass); margin:8px 0 0; }
.msg { color:var(--fail); margin:8px 0; white-space:pre-wrap; word-break:break-word; }
.ai { white-space:pre-wrap; }
video { width:100%; border-radius:8px; margin-top:6px; background:#000; }
figure { margin:12px 0 0; } figcaption { color:var(--muted); font-size:13px; }
.sub { font-weight:600; font-size:13px; margin-top:12px; }
.btn { display:inline-block; margin-top:6px; padding:8px 14px; border-radius:8px; border:1px solid var(--line);
       text-decoration:none; font-weight:600; }
.card .name .dot { vertical-align:-5px; margin-right:4px; }
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
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden;
           clip:rect(0,0,0,0); white-space:nowrap; border:0; }
:focus-visible { outline:3px solid #4c7cf0; outline-offset:2px; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior:auto !important; transition:none !important; } }
.dur { color:var(--muted); font-size:13px; white-space:nowrap; }
.foot { color:var(--muted); font-size:13px; margin-top:20px; }
a { color:inherit; }
"""

_DOT = {"passed": "✓", "failed": "✕", "error": "!", "skipped": "–"}
_STATUS_TEXT = {"passed": "Passed", "failed": "Failed", "error": "Error", "skipped": "Skipped"}


def _dot(outcome: str) -> str:
    """Status icon: the symbol is decorative, the word is what screen readers announce."""
    return (
        f'<span class="dot {outcome}" aria-hidden="true">{_DOT.get(outcome, "?")}</span>'
        f'<span class="sr-only">{_STATUS_TEXT.get(outcome, outcome)}: </span>'
    )


def _fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{int(seconds // 60)} m {seconds % 60:.0f} s"


class _Embedder:
    """Turns run files into data: URIs until the size budget is spent (earlier calls win)."""

    def __init__(self, run_dir: Path | None, budget: int = _EMBED_BUDGET_BYTES) -> None:
        self.run_dir = run_dir
        self.remaining = budget
        self.skipped = 0

    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() or self.run_dir is None else self.run_dir / p

    def uri(self, path: str) -> str | None:
        p = self.resolve(path)
        if not p.is_file():
            return None
        size = p.stat().st_size
        if size * 4 // 3 > self.remaining:  # base64 grows the file by a third
            self.skipped += 1
            return None
        self.remaining -= size * 4 // 3
        mime = _MIME.get(p.suffix.lower(), "application/octet-stream")
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"

    def img(self, path: str, alt: str) -> str:
        src = self.uri(path)
        return f'<img alt="{escape(alt)}" src="{src}">' if src else ""

    def video(self, paths: list[str], label: str = "Test recording", described_by: str = "", poster: str = "") -> str:
        """One player with every available encoding of the same recording."""
        sources = []
        for path in paths:
            src = self.uri(path)
            if src:
                mime = _MIME.get(Path(path).suffix.lower(), "video/webm")
                sources.append(f'<source src="{src}" type="{mime}">')
        if not sources:
            return ""
        poster_uri = self.uri(poster) if poster and self.resolve(poster).is_file() else None
        return (
            f'<video controls playsinline muted preload="metadata" aria-label="{escape(label)}"'
            f'{f" poster={chr(34)}{poster_uri}{chr(34)}" if poster_uri else ""}'
            f'{f" aria-describedby={chr(34)}{described_by}{chr(34)}" if described_by else ""}>'
            f'{"".join(sources)}Your viewer cannot play this video.</video>'
        )

    def download(self, path: str, filename: str, label: str) -> str:
        src = self.uri(path)
        return f'<a class="btn" download="{escape(filename)}" href="{src}">{escape(label)}</a>' if src else ""


def _tile(count: int, label: str, cls: str) -> str:
    # Zero is not news: keep it neutral so a green run doesn't show red numbers.
    return f'<div class="tile {cls if count else ""}"><b>{count}</b><span>{label}</span></div>'


def _variant(test: TestResult) -> str:
    return "".join(f' <span class="tag">{escape(tag)}</span>' for tag in test.variant_tags)


def _failure_card(test: TestResult, media: _Embedder) -> str:
    if test.failure_screenshots:
        shot, caption = media.img(test.failure_screenshots[-1], "Screen when the test failed"), "Screen when it failed:"
    elif test.screenshots:
        shot, caption = media.img(test.screenshots[-1], "Screen at the last step"), "Screen at the last step:"
    else:
        shot, caption = "", ""
    label = "Error during setup/teardown" if test.outcome == "error" else "Failed"
    details = (
        f'<details><summary>Full error</summary><pre tabindex="0" role="region" aria-label="Full error for '
        f'{escape(test.title)}">{escape(test.details)}</pre></details>'
        if test.details
        else ""
    )
    step = (
        f'<div class="step">Stopped at step: <b>{escape(test.last_step)}</b></div>' if test.last_step else ""
    )
    shot_note = f'<div class="file">{caption}</div>' if shot else ""
    return (
        f'<div class="card fail"><div class="name">{escape(test.title)}{_variant(test)}</div>'
        f'<div class="file">{escape(test.file)} · {label} after {_fmt_duration(test.duration)}</div>'
        f"{step}"
        f'<div class="msg">{escape(test.message or "No error message")}</div>'
        f"{shot_note}{shot}{details}</div>"
    )


def _media_card(test: TestResult, media: _Embedder, run_name: str) -> str:
    failed = test.outcome in ("failed", "error")
    parts = [
        f'<div class="card{" fail" if failed else ""}"><div class="name">'
        f"{_dot(test.outcome)} "
        f"{escape(test.title)}{_variant(test)}</div>"
    ]
    if test.videos:
        # WCAG 1.2.1: the recording has no sound, so it needs a text alternative: the steps it shows.
        desc_id = "rec-" + hashlib.sha256(test.nodeid.encode()).hexdigest()[:12]
        shown = " → ".join(test.steps) if test.steps else "the browser while the test ran"
        poster = str(Path(test.videos[0]).parent / "poster.jpg")  # last frame, made by the pipeline
        player = media.video(test.videos, f"Screen recording of {test.title}", desc_id, poster)
        parts.append(
            f'<div class="sub">Video</div>{player}'
            f'<div class="file" id="{desc_id}">Silent screen recording of: {escape(shown)}</div>'
            if player
            else '<div class="file">Video too large to include; it is in the CI run.</div>'
        )
    if test.screenshots:
        shots = "".join(
            f'<figure><figcaption>{i}. {escape(label)}</figcaption>{media.img(path, label)}</figure>'
            for i, (label, path) in enumerate(zip(test.steps or [""] * len(test.screenshots), test.screenshots), 1)
        )
        opened = " open" if failed else ""
        parts.append(f"<details{opened}><summary>Step screenshots ({len(test.screenshots)})</summary>{shots}</details>")
    for i, trace in enumerate(test.traces):
        link = media.download(trace, f"{run_name}-{test.title.replace(' ', '-').lower()}-trace{i or ''}.zip", "Download trace")
        if link:
            parts.append(
                f'<div class="sub">Trace</div>{link}'
                '<div class="file">Open it at <a href="https://trace.playwright.dev">trace.playwright.dev</a> '
                "to replay every action, network call and console message.</div>"
            )
    parts.append("</div>")
    return "".join(parts)


_STANDARD_LABEL = {"wcag21aa": "WCAG 2.1 AA (EN 301 549)", "wcag22aa": "WCAG 2.2 AA"}


def _accessibility_section(summary: RunSummary) -> str:
    scans = [scan for t in summary.tests for scan in t.accessibility]
    if not scans:
        return ""
    from ui_automation.accessibility import AXE_VERSION, MANUAL_CHECKS

    standard = _STANDARD_LABEL.get(scans[0].get("standard", ""), scans[0].get("standard", ""))
    total = sum(len(s["violations"]) for s in scans)
    parts = [f"<h2>Accessibility: {escape(standard)}</h2>"]
    summary_line = (
        f"{total} issue type(s) found on {sum(1 for s in scans if s['violations'])} of {len(scans)} page(s)."
        if total else f"No issues found by the automated checks on {len(scans)} page(s)."
    )
    parts.append(f'<p class="file">{escape(summary_line)} Checked with axe-core {AXE_VERSION}.</p>')
    for scan_ in scans:
        issues = scan_["violations"]
        rows = []
        for v in issues:
            crit = ", ".join(v.get("criteria") or []) or "best practice"
            where = "".join(f"<li><code>{escape(t)}</code></li>" for t in v.get("targets", [])[:3])
            more = v.get("count", 0) - min(3, len(v.get("targets", [])))
            rows.append(
                f'<li class="issue"><span class="tag impact-{escape(v.get("impact", ""))}">{escape(v.get("impact", ""))}</span> '
                f'<b>{escape(v.get("help", ""))}</b><div class="file">WCAG {escape(crit)} · '
                f'{v.get("count", 0)} element(s)'
                + (f' · <a href="{escape(v["help_url"])}">How to fix<span class="sr-only"> {escape(v.get("help", ""))}</span></a>'
                   if v.get("help_url", "").startswith("https://") else "")
                + f"</div><ul class=\"targets\">{where}{f'<li>…and {more} more</li>' if more > 0 else ''}</ul></li>"
            )
        body = f'<ul class="issues">{"".join(rows)}</ul>' if rows else '<p class="ok-text">No issues found by the automated checks.</p>'
        parts.append(
            f'<div class="card{" fail" if issues else ""}"><div class="name">{escape(scan_.get("url", ""))}</div>{body}</div>'
        )
    manual = "".join(f"<li><b>{escape(c)}</b>: {escape(text)}</li>" for c, text in MANUAL_CHECKS)
    parts.append(
        "<details><summary>Still needs a person to check</summary>"
        '<p class="file">Automated rules catch many problems but cannot prove conformance. A full evaluation '
        "follows WCAG-EM: define the scope, explore the site, pick a representative sample of pages, audit "
        "them against every success criterion (by hand and with tools), and publish the findings in the "
        "site's accessibility statement.</p>"
        f'<ul class="manual">{manual}</ul></details>'
    )
    return "".join(parts)


def _compliance_section(summary: RunSummary) -> str:
    pages = [entry for t in summary.tests for entry in t.compliance]
    if not pages:
        return ""
    failed = sum(1 for p in pages for r in p["results"] if not r["passed"])
    parts = ["<h2>Website requirements (Ireland / EU)</h2>",
             f'<p class="file">{failed} problem(s) found on {len(pages)} page(s). These checks find what is missing '
             "or misbehaving; the wording of your policies still needs a person to review.</p>"]
    for entry in pages:
        rows = "".join(
            f'<li class="issue"><span class="dot {"passed" if r["passed"] else "failed"}" aria-hidden="true">'
            f'{"✓" if r["passed"] else "✕"}</span><span class="sr-only">{"Passed" if r["passed"] else "Failed"}: </span>'
            f' <b>{escape(r["title"])}</b>: {escape(r["detail"])}<div class="file">{escape(r["law"])}</div></li>'
            for r in entry["results"]
        )
        bad = any(not r["passed"] for r in entry["results"])
        parts.append(f'<div class="card{" fail" if bad else ""}"><div class="name">{escape(entry["url"])}</div>'
                     f'<ul class="issues checks">{rows}</ul></div>')
    return "".join(parts)


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

    media = _Embedder(summary.run_dir)
    problems = summary.problems
    if problems:
        parts.append("<h2>What failed</h2>")
        parts.extend(_failure_card(t, media) for t in problems)
    if ai_text:
        parts.append(f'<h2>Claude\'s analysis</h2><div class="card ai">{escape(ai_text)}</div>')

    parts.append(_accessibility_section(summary))
    parts.append(_compliance_section(summary))

    order_media = {"error": 0, "failed": 1, "passed": 2, "skipped": 3}
    with_media = [t for t in summary.tests if t.videos or t.screenshots or t.traces]
    if with_media:
        run_name = summary.run_dir.name if summary.run_dir else "run"
        parts.append("<h2>Screenshots &amp; videos</h2>")
        parts.extend(
            _media_card(t, media, run_name)
            for t in sorted(with_media, key=lambda t: (order_media.get(t.outcome, 4), t.nodeid))
        )

    rows = []
    order = {"error": 0, "failed": 1, "skipped": 2, "passed": 3}
    for t in sorted(summary.tests, key=lambda t: (order.get(t.outcome, 4), t.nodeid)):
        note = f'<div class="file">{escape(t.message)}</div>' if t.outcome == "skipped" and t.message else ""
        rows.append(
            f"<li>{_dot(t.outcome)}"
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
        f"Videos: {len({p.parent for p in summary.video_files})} · Traces: {len(summary.trace_files)} "
        f"(only browser tests record video{'; ' + trace_note if trace_note else ''})."
    ]
    if media.skipped:
        foot.append(f"{media.skipped} file(s) were too large to include in this report; they are in the CI run.")
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
