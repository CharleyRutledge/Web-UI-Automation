from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ui_automation.config import Settings
from ui_automation.reporting.claude import analyze_run
from ui_automation.reporting.email_notify import send_run_email
from ui_automation.reporting.summary import RunSummary
from ui_automation.reporting.telegram_notify import send_run_telegram


def collect_playwright_artifacts(output_dir: Path, run_dir: Path) -> tuple[list[Path], list[Path]]:
    """Copy videos and traces from pytest-playwright output into the run report folder."""
    videos_dst = run_dir / "videos"
    traces_dst = run_dir / "traces"
    videos_dst.mkdir(parents=True, exist_ok=True)
    traces_dst.mkdir(parents=True, exist_ok=True)

    video_files: list[Path] = []
    trace_files: list[Path] = []

    if not output_dir.is_dir():
        return video_files, trace_files

    for src in output_dir.rglob("*"):
        if not src.is_file():
            continue
        suffix = src.suffix.lower()
        if suffix == ".webm":
            rel = src.relative_to(output_dir)
            dst = videos_dst / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            video_files.append(dst)
        elif suffix == ".zip" and "trace" in src.name.lower():
            rel = src.relative_to(output_dir)
            dst = traces_dst / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            trace_files.append(dst)

    return video_files, trace_files


def build_summary(session: pytest.Session, exitstatus: int, run_dir: Path) -> RunSummary:
    summary = RunSummary(exit_status=exitstatus, run_dir=run_dir)
    summary.report_html = run_dir / "report.html"

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        for key, stat_key in (("passed", "passed"), ("failed", "failed"), ("skipped", "skipped")):
            items = reporter.stats.get(key, []) or []
            setattr(summary, stat_key, len(items))
        summary.errors = len(reporter.stats.get("error", []) or [])
        for rep in (reporter.stats.get("failed", []) or []) + (reporter.stats.get("error", []) or []):
            text = getattr(rep, "longreprtext", "") or ""
            # Keep the end of each traceback: that is where the assertion / error message is.
            summary.failure_details.append(f"{rep.nodeid} ({rep.when})\n{text[-3000:]}")

    output = session.config.getoption("--output")
    if output:
        video_files, trace_files = collect_playwright_artifacts(Path(output), run_dir)
        summary.video_files = video_files
        summary.trace_files = trace_files

    return summary


def write_summary_json(summary: RunSummary, run_dir: Path) -> Path:
    """Persist a small machine-readable summary the web dashboard can read without parsing HTML."""
    payload = {
        "run_id": run_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exit_status": summary.exit_status,
        "ok": summary.ok,
        "passed": summary.passed,
        "failed": summary.failed,
        "skipped": summary.skipped,
        "errors": summary.errors,
        "total": summary.total,
        "report_html": summary.report_html.name if summary.report_html else None,
        "video_count": len(summary.video_files),
        "trace_count": len(summary.trace_files),
    }
    path = run_dir / "summary.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def finalize_run(
    session: pytest.Session,
    exitstatus: int,
    settings: Settings,
    run_dir: Path,
    *,
    notify: bool = True,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(session, exitstatus, run_dir)
    write_summary_json(summary, run_dir)
    if not notify:
        return

    ai_text: str | None = None
    try:
        ai_text = analyze_run(summary, settings.ai)
        if ai_text:
            ai_path = run_dir / "claude_summary.txt"
            ai_path.write_text(ai_text, encoding="utf-8")
            print(f"Claude summary: {ai_path}")
    except Exception as exc:
        print(f"Claude analysis skipped: {exc}")

    try:
        send_run_email(summary, settings.notifications.email, ai_summary=ai_text)
    except Exception as exc:
        print(f"Email notification skipped: {exc}")

    try:
        send_run_telegram(summary, settings.notifications.telegram, ai_summary=ai_text)
    except Exception as exc:
        print(f"Telegram notification skipped: {exc}")

    if summary.video_files:
        print(f"Saved {len(summary.video_files)} video(s) under {run_dir / 'videos'}")
