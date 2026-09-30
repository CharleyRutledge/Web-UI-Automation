"""Lightweight local dashboard: browse past runs and trigger new ones.

Optional extra — requires Flask (see requirements-ui.txt). Launch with:

    python -m ui_automation --ui
"""

from __future__ import annotations

import json
import subprocess
import threading
import sys
from dataclasses import dataclass
from pathlib import Path

REPORTS_DIRNAME = "reports"


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


def _list_runs(reports_dir: Path) -> list[RunRow]:
    if not reports_dir.is_dir():
        return []

    rows: list[RunRow] = []
    for entry in reports_dir.iterdir():
        if not entry.is_dir() or entry.name == "latest":
            continue
        summary_path = entry / "summary.json"
        report_path = entry / "report.html"
        if summary_path.is_file():
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
        else:
            data = {}
        rows.append(
            RunRow(
                run_id=entry.name,
                ok=data.get("ok"),
                passed=data.get("passed", 0),
                failed=data.get("failed", 0),
                skipped=data.get("skipped", 0),
                errors=data.get("errors", 0),
                generated_at=data.get("generated_at"),
                has_report=report_path.is_file(),
                has_summary=(entry / "summary.html").is_file(),
            )
        )
    rows.sort(key=lambda r: r.run_id, reverse=True)
    return rows


_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
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
  form.run-form button { background: #3b6ff2; color: #fff; border: none; padding: 10px 18px;
          border-radius: 6px; font-size: 14px; cursor: pointer; }
  form.run-form button:hover { background: #2f59c9; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #eceef2; font-size: 14px; }
  th { color: #666; font-weight: 600; font-size: 12px; text-transform: uppercase; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
           font-weight: 600; }
  .badge.pass { background: #e3f7e9; color: #1e7e34; }
  .badge.fail { background: #fdeceb; color: #c0392b; }
  .badge.unknown { background: #eee; color: #666; }
  a.report-link { color: #3b6ff2; text-decoration: none; font-weight: 500; }
  a.report-link:hover { text-decoration: underline; }
  .empty { color: #888; font-size: 14px; }
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
  {% if message %}<div class="flash">{{ message }}</div>{% endif %}
  <div class="card">
    <table>
      <thead>
        <tr><th>Run</th><th>Status</th><th>Passed</th><th>Failed</th><th>Skipped</th><th>Errors</th><th>Report</th></tr>
      </thead>
      <tbody>
      {% for row in runs %}
        <tr>
          <td>{{ row.run_id }}</td>
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
              {% if row.has_summary %}<a class="report-link" href="/reports/{{ row.run_id }}/summary.html" target="_blank">Summary</a> · {% endif %}
              <a class="report-link" href="/reports/{{ row.run_id }}/report.html" target="_blank">Full report</a>
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
</main>
</body>
</html>
"""


def create_app():
    from flask import Flask, abort, redirect, render_template_string, request, send_from_directory, url_for

    root = _project_root()
    reports_dir = root / REPORTS_DIRNAME

    app = Flask(__name__)
    run_lock = threading.Lock()

    @app.get("/")
    def index():
        message = request.args.get("message")
        return render_template_string(_PAGE, runs=_list_runs(reports_dir), message=message)

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
                    timeout=600,
                )
            except subprocess.TimeoutExpired:
                message = "Run timed out after 600s and was stopped."
            else:
                tail = "\n".join(result.stdout.strip().splitlines()[-6:])
                message = f"Run finished (exit {result.returncode}).\n{tail}"
        finally:
            run_lock.release()
        return redirect(url_for("index", message=message))

    @app.get("/reports/<path:filename>")
    def serve_report(filename: str):
        return send_from_directory(reports_dir, filename)

    return app


def run_dashboard(host: str = "127.0.0.1", port: int = 8501) -> None:
    try:
        app = create_app()
    except ImportError as exc:
        raise SystemExit(
            "The dashboard needs Flask. Install it with:\n"
            "  python -m pip install -r requirements-ui.txt"
        ) from exc
    print(f"UI Automation dashboard: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
