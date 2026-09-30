from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from ui_automation.config import Settings
from ui_automation.reporting.claude import analyze_run
from ui_automation.reporting.email_notify import send_run_email
from ui_automation.reporting.report_html import render_summary_html
from ui_automation.reporting.summary import RunSummary, TestResult
from ui_automation.reporting.telegram_notify import send_run_telegram


_ARTIFACT_DIRS = {".webm": "videos", ".zip": "traces", ".png": "failure-screenshots"}


def collect_playwright_artifacts(output_dir: Path, run_dir: Path) -> tuple[list[Path], list[Path]]:
    """Copy videos, traces and failure screenshots from pytest-playwright output into the run folder.

    Each test keeps its own sub-folder (named after its node id), e.g. videos/<test>/video.webm.
    """
    video_files: list[Path] = []
    trace_files: list[Path] = []
    for folder in set(_ARTIFACT_DIRS.values()):
        (run_dir / folder).mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        return video_files, trace_files

    for src in output_dir.rglob("*"):
        suffix = src.suffix.lower()
        if not src.is_file() or suffix not in _ARTIFACT_DIRS:
            continue
        if suffix == ".zip" and "trace" not in src.name.lower():
            continue
        dst = run_dir / _ARTIFACT_DIRS[suffix] / src.relative_to(output_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if suffix == ".webm":
            video_files.append(dst)
            mp4 = _to_mp4(dst)
            if mp4:
                video_files.append(mp4)
        elif suffix == ".zip":
            trace_files.append(dst)
    return video_files, trace_files


def _to_mp4(webm: Path) -> Path | None:
    """Playwright records WebM, which iPhones/Safari may not play; add an H.264 MP4 copy if ffmpeg exists."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    mp4 = webm.with_suffix(".mp4")
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "28", "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
           "-movflags", "+faststart", "-an", str(mp4)]
    try:
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"MP4 conversion skipped for {webm.name}: {exc}")
        return None
    return mp4


def _artifact_folder(nodeid: str) -> str:
    """The per-test folder name pytest-playwright uses for this test's video / trace / screenshot."""
    try:
        from pytest_playwright.pytest_playwright import _truncate_file_name
        from slugify import slugify
    except ImportError:
        return ""
    return _truncate_file_name(slugify(nodeid))


def _attach_media(summary: RunSummary, run_dir: Path) -> None:
    def rel(paths: list[Path]) -> list[str]:
        return [str(p.relative_to(run_dir)) for p in sorted(paths)]

    for test in summary.tests:
        folder = _artifact_folder(test.nodeid)
        if folder:
            # MP4 first (iPhone/Safari), WebM second (browsers without H.264); players pick what they can.
            video_dir = run_dir / "videos" / folder
            test.videos = rel(list(video_dir.glob("*.mp4"))) + rel(list(video_dir.glob("*.webm")))
            test.traces = rel(list((run_dir / "traces" / folder).glob("*.zip")))
            test.failure_screenshots = rel(list((run_dir / "failure-screenshots" / folder).glob("*.png")))
        test.screenshots = [
            str(Path(p).relative_to(run_dir)) if Path(p).is_relative_to(run_dir) else p for p in test.screenshots
        ]


_OUTCOME_RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}


_E_ERROR = re.compile(r"^E\s+Error:\s*(.+?)\s*$", re.M)
_E_WAITING = re.compile(r"^E\s+- waiting for (.+?)\s*$", re.M)


def _crash_message(rep: pytest.TestReport) -> str:
    longrepr = rep.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:  # skip: (path, lineno, reason)
        return str(longrepr[2]).removeprefix("Skipped: ")
    crash = getattr(longrepr, "reprcrash", None)
    text = getattr(rep, "longreprtext", "") or ""
    message = (getattr(crash, "message", "") or text).strip()
    message = message.splitlines()[0] if message else ""
    # Playwright puts the useful part (what was not found, and which locator) further down.
    error, waiting = _E_ERROR.search(text), _E_WAITING.search(text)
    if error and error.group(1) not in message:
        message += f" ({error.group(1)}"
        message += f": {waiting.group(1)})" if waiting else ")"
    return message[:400]


def collect_test_results(reporter: Any) -> list[TestResult]:
    """One TestResult per test, merging its setup / call / teardown reports."""
    results: dict[str, TestResult] = {}
    for category, reports in reporter.stats.items():
        for rep in reports:
            if not isinstance(rep, pytest.TestReport):
                continue
            if category == "error" or (rep.failed and rep.when != "call"):
                outcome = "error"
            elif category in ("failed", "skipped", "passed"):
                outcome = category
            elif category == "xfailed":
                outcome = "skipped"
            elif category == "xpassed":
                outcome = "passed"
            else:  # setup/teardown that passed: only contributes duration
                outcome = ""
            result = results.setdefault(rep.nodeid, TestResult(nodeid=rep.nodeid, outcome="passed"))
            result.duration += rep.duration
            for key, value in rep.user_properties:
                if key == "step_screenshots":
                    result.screenshots = list(value)
                elif key == "step_labels":
                    result.steps = list(value)
                elif key == "accessibility":
                    result.accessibility = list(value)
                elif key == "compliance":
                    result.compliance = list(value)
            if outcome and _OUTCOME_RANK[outcome] >= _OUTCOME_RANK.get(result.outcome, 0):
                if outcome != "passed":
                    result.message = _crash_message(rep)
                    # The end of the traceback is where the assertion / error message is.
                    result.details = (getattr(rep, "longreprtext", "") or "")[-3000:]
                result.outcome = outcome
    return list(results.values())


def finalize_run(
    session: pytest.Session,
    exitstatus: int,
    run_dir: Path,
    *,
    duration: float,
    settings: Settings | None = None,
) -> RunSummary:
    """Runs inside pytest: record results to summary.json. Notifications happen later (notify_run)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = RunSummary(exit_status=exitstatus, run_dir=run_dir, duration=duration)
    if settings is not None:
        summary.video_mode = settings.video_mode
        summary.tracing_mode = settings.tracing_mode
        summary.base_url = settings.base_url

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        summary.tests = collect_test_results(reporter)
        counts = {k: sum(1 for t in summary.tests if t.outcome == k) for k in _OUTCOME_RANK}
        summary.passed, summary.failed = counts["passed"], counts["failed"]
        summary.skipped, summary.errors = counts["skipped"], counts["error"]

    output = session.config.getoption("--output", None)
    if output:
        summary.video_files, summary.trace_files = collect_playwright_artifacts(Path(output), run_dir)
        if summary.video_files:
            print(f"Saved {len(summary.video_files)} video file(s) under {run_dir / 'videos'}")
    _attach_media(summary, run_dir)

    payload = summary.to_dict()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    (run_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return summary


def _ci_run_url() -> str:
    server, repo, run_id = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""


def notify_run(run_dir: Path, settings: Settings) -> Path | None:
    """Runs after pytest has exited (so every file exists): AI analysis, clean report, notifications."""
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        print("Notifications skipped: no summary.json (pytest did not finish)")
        return None
    summary = RunSummary.load(run_dir)
    summary.run_url = _ci_run_url()

    ai_text: str | None = None
    try:
        ai_text = analyze_run(summary, settings.ai)
        if ai_text:
            (run_dir / "claude_summary.txt").write_text(ai_text, encoding="utf-8")
            print(f"Claude summary: {run_dir / 'claude_summary.txt'}")
    except Exception as exc:
        print(f"Claude analysis skipped: {exc}")

    report_path = run_dir / "summary.html"
    report_path.write_text(render_summary_html(summary, ai_text), encoding="utf-8")
    print(f"Summary report: {report_path}")

    try:
        send_run_email(summary, settings.notifications.email, ai_summary=ai_text, attachment=report_path)
    except Exception as exc:
        print(f"Email notification skipped: {exc}")

    try:
        send_run_telegram(summary, settings.notifications.telegram, ai_summary=ai_text, attachment=report_path)
    except Exception as exc:
        print(f"Telegram notification skipped: {exc}")

    return report_path
