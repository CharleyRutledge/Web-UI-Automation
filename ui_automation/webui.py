"""Lightweight local dashboard: browse past runs and trigger new ones.

Optional extra — requires Flask (see requirements-ui.txt). Launch with:

    python -m ui_automation --ui
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import sys
from dataclasses import dataclass
from pathlib import Path

from ui_automation.config import reports_root

@dataclass
class RunRow:
    run_id: str
    ok: bool | None
    passed: int
    failed: int
    skipped: int
    errors: int
    generated_at: str | None
    has_report: bool
    has_summary: bool = False


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PAGE_SIZE = 200


class _RunIndex:
    """Lists run folders newest first. Only the rows shown are read, and each summary.json is parsed
    once and cached until the file changes, so a folder with thousands of runs stays fast."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir
        self._cache: dict[str, tuple[float, RunRow]] = {}
        self._lock = threading.Lock()

    def rows(self, limit: int | None = PAGE_SIZE) -> tuple[list[RunRow], int]:
        if not self.reports_dir.is_dir():
            return [], 0
        names = sorted(
            (e.name for e in os.scandir(self.reports_dir)
             if e.is_dir() and e.name != "latest" and not e.name.startswith(".")),
            reverse=True
        )
        shown = names if limit is None else names[:limit]
        return [self._row(name) for name in shown], len(names)

    def _row(self, name: str) -> RunRow:
        entry = self.reports_dir / name
        summary_path = entry / "summary.json"
        try:
            mtime = summary_path.stat().st_mtime
        except OSError:
            mtime = -1.0
        with self._lock:
            cached = self._cache.get(name)
        if cached and cached[0] == mtime and mtime != -1.0:
            return cached[1]
        data: object = {}
        if mtime != -1.0:
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        row = RunRow(
            run_id=name,
            ok=data.get("ok"),
            passed=data.get("passed", 0),
            failed=data.get("failed", 0),
            skipped=data.get("skipped", 0),
            errors=data.get("errors", 0),
            generated_at=data.get("generated_at"),
            has_report=(entry / "report.html").is_file(),
            has_summary=(entry / "summary.html").is_file(),
        )
        with self._lock:
            self._cache[name] = (mtime, row)
        return row


def _list_runs(reports_dir: Path) -> list[RunRow]:
    return _RunIndex(reports_dir).rows(limit=None)[0]


_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UI Automation Dashboard</title>
<style>
  :root { color-scheme: light; }
  body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; margin: 0;
         background: #f6f7f9; color: #1a1a1a; }
  header { background: #202433; color: #fff; padding: 20px 32px; display: flex;
           align-items: center; justify-content: space-between; }
  header h1 { margin: 0; font-size: 20px; }
  main { max-width: 960px; margin: 32px auto; padding: 0 20px; }
  .card { background: #fff; border: 1px solid #e2e4e9; border-radius: 8px; padding: 20px;
          margin-bottom: 20px; }
  form.run-form button { background: #2453c7; color: #fff; border: none; padding: 10px 18px;
          border-radius: 6px; font-size: 14px; cursor: pointer; }
  form.run-form button:hover { background: #1b3f9c; }
  :focus-visible { outline: 3px solid #f5b700; outline-offset: 2px; }
  table { width: 100%; border-collapse: collapse; }
  .table-scroll { overflow-x: auto; position: relative; }  /* contains the absolutely-positioned sr-only labels */
  tbody th { font-weight: 400; color: inherit; text-transform: none; font-size: 14px; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
             clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
  @media (max-width: 600px) { header { padding: 16px; flex-wrap: wrap; gap: 12px; } main { margin: 16px auto; padding: 0 12px; } }
  th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #eceef2; font-size: 14px; }
  th { color: #666; font-weight: 600; font-size: 12px; text-transform: uppercase; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
           font-weight: 600; }
  .badge.pass { background: #e3f7e9; color: #1e7e34; }
  .badge.fail { background: #fdeceb; color: #c0392b; }
  .badge.unknown { background: #eee; color: #4a4a4a; }
  a.report-link { color: #2453c7; text-decoration: none; font-weight: 500; }
  a.report-link:hover { text-decoration: underline; }
  .empty { color: #595959; font-size: 14px; }
  .flash { background: #fff8e1; border: 1px solid #ffe08a; padding: 12px 16px; border-radius: 6px;
           margin-bottom: 20px; font-size: 14px; white-space: pre-wrap; }
</style>
</head>
<body>
<header>
  <h1>UI Automation Dashboard</h1>
  <form class="run-form" method="post" action="/run">
    <button type="submit">Run tests</button>
  </form>
</header>
<main>
  <div role="status">{% if message %}<div class="flash">{{ message }}</div>{% endif %}</div>
  <div class="card">
    <div class="table-scroll" role="region" aria-label="Test runs" tabindex="0">
    <table>
      <caption class="sr-only">Test runs, newest first</caption>
      <thead>
        <tr><th scope="col">Run</th><th scope="col">Status</th><th scope="col">Passed</th><th scope="col">Failed</th>
            <th scope="col">Skipped</th><th scope="col">Errors</th><th scope="col">Report</th></tr>
      </thead>
      <tbody>
      {% for row in runs %}
        <tr>
          <th scope="row">{{ row.run_id }}</th>
          <td>
            {% if row.ok is none %}
              <span class="badge unknown">unknown</span>
            {% elif row.ok %}
              <span class="badge pass">passed</span>
            {% else %}
              <span class="badge fail">failed</span>
            {% endif %}
          </td>
          <td>{{ row.passed }}</td>
          <td>{{ row.failed }}</td>
          <td>{{ row.skipped }}</td>
          <td>{{ row.errors }}</td>
          <td>
            {% if row.has_report %}
              {% if row.has_summary %}<a class="report-link" href="/reports/{{ row.run_id }}/summary.html" target="_blank">Summary<span class="sr-only"> for run {{ row.run_id }} (opens in a new tab)</span></a> · {% endif %}
              <a class="report-link" href="/reports/{{ row.run_id }}/report.html" target="_blank">Full report<span class="sr-only"> for run {{ row.run_id }} (opens in a new tab)</span></a>
            {% else %}
              <span class="empty">n/a</span>
            {% endif %}
          </td>
        </tr>
      {% else %}
        <tr><td colspan="7" class="empty">No runs yet. Click "Run tests" to start one.</td></tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
    {% if total > runs|length %}
      <p class="empty">Showing the newest {{ runs|length }} of {{ total }} runs. <a class="report-link" href="/?all=1">Show all</a></p>
    {% endif %}
  </div>
</main>
</body>
</html>
"""


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def create_app(
    reports_dir: Path | None = None,
    *,
    allowed_hosts: frozenset[str] | None = None,
    run_timeout: float = 600,
):
    from flask import Flask, abort, redirect, request, send_from_directory, url_for

    root = _project_root()
    reports_dir = (Path(reports_dir) if reports_dir else reports_root(root)).resolve()
    hosts = _LOCAL_HOSTS | (allowed_hosts or frozenset())

    app = Flask(__name__)
    run_lock = threading.Lock()
    index_ = _RunIndex(reports_dir)
    page = app.jinja_env.from_string(_PAGE)  # compiled once, not on every request

    @app.before_request
    def only_expected_hosts():
        # DNS rebinding: a malicious site can point its own domain at 127.0.0.1 and then read or
        # drive this dashboard as a "same-origin" page. Its requests carry its domain in Host.
        host = request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
        if host.lower() not in hosts:
            abort(403)

    @app.get("/")
    def index():
        message = request.args.get("message")
        show_all = request.args.get("all") == "1"
        runs, total = index_.rows(limit=None if show_all else PAGE_SIZE)
        return page.render(runs=runs, total=total, message=message)

    @app.post("/run")
    def trigger_run():
        # Reject cross-site form posts: any web page could otherwise trigger a test run on localhost.
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            abort(403)
        if not run_lock.acquire(blocking=False):
            return redirect(url_for("index", message="A run is already in progress."))
        try:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "ui_automation"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=run_timeout,
                )
            except subprocess.TimeoutExpired:
                message = f"Run timed out after {run_timeout:.0f}s and was stopped."
            else:
                tail = "\n".join(result.stdout.strip().splitlines()[-6:])
                message = f"Run finished (exit {result.returncode}).\n{tail}"
        finally:
            run_lock.release()
        return redirect(url_for("index", message=message))

    @app.get("/reports/<path:filename>")
    def serve_report(filename: str):
        # send_from_directory blocks "../" but follows symlinks; refuse anything that resolves outside.
        target = (reports_dir / filename).resolve()
        if not target.is_relative_to(reports_dir):
            abort(404)
        return send_from_directory(reports_dir, filename)

    return app


def run_dashboard(host: str = "127.0.0.1", port: int = 8501) -> None:
    try:
        app = create_app(allowed_hosts=frozenset({host.lower()}))
    except ImportError as exc:
        raise SystemExit(
            "The dashboard needs Flask. Install it with:\n"
            "  python -m pip install -r requirements-ui.txt"
        ) from exc
    print(f"UI Automation dashboard: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
