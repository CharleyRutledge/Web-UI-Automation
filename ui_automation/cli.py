from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from ui_automation.config import load_settings, reports_root


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def setup_project(root: Path, *, include_mcp: bool) -> int:
    req = root / "requirements-mcp.txt" if include_mcp else root / "requirements.txt"
    steps = [
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
        [sys.executable, "-m", "playwright", "install", "chromium"],
    ]
    for cmd in steps:
        print("+", " ".join(cmd))
        code = subprocess.call(cmd, cwd=str(root))
        if code != 0:
            return code
    print("Setup complete.")
    return 0


def new_run_dir(reports_dir: Path) -> Path:
    """A fresh run folder named after the UTC time. Two runs in the same second get _2, _3, …

    mkdir(exist_ok=False) makes the choice atomic, so concurrent runs never share a folder.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for n in range(1, 1000):
        candidate = reports_dir / (stamp if n == 1 else f"{stamp}_{n}")
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate.resolve()
    raise RuntimeError(f"Could not create a unique run folder under {reports_dir}")


def publish_latest_report(run_dir: Path, reports_dir: Path) -> Path:
    """Copy the run report and artifacts to <reports>/latest for a stable path."""
    latest_dir = reports_dir / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    src = run_dir / "report.html"
    dst = latest_dir / "report.html"
    if src.is_file():
        shutil.copy2(src, dst)
    elif dst.exists():
        dst.unlink()

    for folder in ("screenshots", "failure-screenshots", "videos", "traces"):
        src_dir = run_dir / folder
        dst_dir = latest_dir / folder
        if dst_dir.exists():
            shutil.rmtree(dst_dir)  # never leave artifacts from an older run in "latest"
        if src_dir.is_dir():
            shutil.copytree(src_dir, dst_dir)

    for name in ("claude_summary.txt", "summary.json", "summary.html"):
        file_src = run_dir / name
        file_dst = latest_dir / name
        if file_src.is_file():
            shutil.copy2(file_src, file_dst)
        elif file_dst.exists():
            file_dst.unlink()

    return dst


def open_report(path: Path) -> None:
    uri = path.resolve().as_uri()
    print(f"Opening report: {uri}")
    try:
        if webbrowser.open(uri):
            return
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.call(["xdg-open", str(path)])
    except OSError:
        # Headless machines (servers, CI, containers) have no browser; the run itself still succeeded.
        print(f"Could not open a browser. Open the report manually: {path.resolve()}")


def _run_pytest(cmd: list[str], root: Path) -> int:
    """Run pytest and wait for it. Ctrl-C reaches pytest too (same terminal); it stops the tests and
    still writes its results, so we keep waiting instead of dying with a traceback mid-report."""
    proc = subprocess.Popen(cmd, cwd=str(root))
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            print("\nInterrupted: waiting for pytest to save the results so far...", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ui_automation",
        description="Web UI test runner: Playwright + pytest + timestamped HTML reports.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Install Python dependencies and Chromium, then exit.",
    )
    parser.add_argument(
        "--with-mcp",
        action="store_true",
        help="With --setup, also install requirements-mcp.txt (fetch MCP + uv).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the HTML report in the default browser after the run.",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch the local dashboard (browse past runs, trigger new ones) instead of running tests.",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=8501,
        help="Port for --ui (default: 8501).",
    )
    parser.add_argument(
        "--config",
        dest="config",
        default=None,
        help="Path to settings YAML (forwarded to pytest --config).",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Extra arguments after -- are passed to pytest (example: -- -m smoke -q).",
    )
    args = parser.parse_args(argv)

    root = _project_root()

    if args.setup:
        return setup_project(root, include_mcp=args.with_mcp)

    if args.ui:
        from ui_automation.webui import run_dashboard

        run_dashboard(port=args.ui_port)
        return 0

    pytest_args = list(args.pytest_args)
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]

    if args.config:
        # The tests run with cwd=root, so a config path relative to the caller's cwd must be absolutized.
        args.config = str(Path(args.config).expanduser().resolve())
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError) as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2

    reports_dir = reports_root(root)
    try:
        run_dir = new_run_dir(reports_dir)
    except OSError as exc:
        print(f"Cannot create a run folder under {reports_dir}: {exc}", file=sys.stderr)
        return 2
    playwright_output = run_dir / "playwright-output"

    os.environ["WEB_UI_RUN_DIR"] = str(run_dir)

    html_report = run_dir / "report.html"
    report_css = root / "ui_automation" / "reporting" / "assets" / "report_theme.css"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        # No explicit test path: pytest.ini's testpaths applies unless the user passes their own after --.
        f"--html={html_report}",
        "--self-contained-html",
        f"--css={report_css}",
        f"--output={playwright_output}",
        f"--video={settings.video_mode}",
        f"--tracing={settings.tracing_mode}",
        "--screenshot=only-on-failure",
    ]
    if args.config:
        cmd.append(f"--config={args.config}")
    cmd.extend(pytest_args)

    print(f"Report directory: {run_dir}")
    print(f"HTML report: {html_report}")
    print(f"Playwright output: {playwright_output}")
    print(f"Video mode: {settings.video_mode}")

    exit_code = _run_pytest(cmd, root)

    # pytest has exited, so report.html and summary.json are complete: now analyse and notify.
    from ui_automation.reporting.pipeline import notify_run

    notify_run(run_dir, settings)

    latest_report = publish_latest_report(run_dir, reports_dir)
    print(f"Latest report copy: {latest_report}")

    if args.open and latest_report.is_file():
        open_report(latest_report)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
