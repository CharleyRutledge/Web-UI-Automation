"""Helpers shared by the self-tests: run the real CLI and read what it produced."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
SECRET_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
              "TELEGRAM_API_BASE", "EMAIL_SMTP_USER", "EMAIL_SMTP_PASSWORD", "WEB_UI_CONFIG", "WEB_UI_RUN_DIR")


@dataclass
class CliRun:
    returncode: int
    stdout: str
    stderr: str
    reports_dir: Path
    run_dir: Path | None

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    @property
    def summary(self) -> dict[str, Any]:
        assert self.run_dir is not None, self.output
        return json.loads((self.run_dir / "summary.json").read_text(encoding="utf-8"))

    def test(self, name: str) -> dict[str, Any]:
        matches = [t for t in self.summary["tests"] if t["nodeid"].split("::")[-1].startswith(name)]
        assert len(matches) == 1, f"{name}: {[t['nodeid'] for t in self.summary['tests']]}"
        return matches[0]


def base_config(site_url: str, **overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "base_url": site_url,
        "browsers": ["chromium"],  # the self-tests run in Chromium (one multi-browser test sets its own)
        "timeout_ms": 3000,
        "artifacts": {"video": "on", "tracing": "retain-on-failure"},
        "ai": {"enabled": False},
        "compliance": {"enabled": False},  # on by default in real configs; tests that need it turn it on
        "notifications": {"email": {"enabled": False}, "telegram": {"enabled": False}},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    return cfg


def invoke_cli(
    workdir: Path,
    pytest_args: list[str],
    config: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    cli_args: list[str] | None = None,
    reports_dir: Path | None = None,
    timeout: float = 300,
    interrupt_after: float | None = None,
) -> CliRun:
    """Run `python -m ui_automation` for real, with its own config and reports folder."""
    workdir.mkdir(parents=True, exist_ok=True)
    reports = reports_dir or workdir / "reports"
    args = list(cli_args or [])
    if config is not None:
        cfg_path = workdir / f"settings-{len(list(workdir.glob('settings-*.yaml')))}.yaml"
        cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        args += ["--config", str(cfg_path)]
    full_env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    # WEB_UI_NO_DOTENV: a developer's real .env (bot token, API key) must never reach a self-test run.
    full_env.update({"WEB_UI_REPORTS_DIR": str(reports), "PYTHONUNBUFFERED": "1", "WEB_UI_NO_DOTENV": "1"}, **(env or {}))
    cmd = [sys.executable, "-m", "ui_automation", *args, "--", *pytest_args]
    proc = subprocess.Popen(cmd, cwd=REPO, env=full_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    if interrupt_after is not None:
        try:
            out, err = proc.communicate(timeout=interrupt_after)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGINT)  # like Ctrl-C in a terminal: the whole process group
            out, err = proc.communicate(timeout=timeout)
    else:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            raise AssertionError(f"CLI did not finish within {timeout}s:\n{out}\n{err}")
    run_dirs = sorted(p for p in reports.glob("2*") if p.is_dir()) if reports.is_dir() else []
    return CliRun(proc.returncode, out, err, reports, run_dirs[-1] if run_dirs else None)


def make_summary(run_dir: Path, *, failures: int = 1, passed: int = 1, message: str = "Locator expected to be visible"):
    """A real RunSummary (as the pipeline builds it) plus its rendered report file."""
    from ui_automation.reporting.report_html import render_summary_html
    from ui_automation.reporting.summary import RunSummary, TestResult

    run_dir.mkdir(parents=True, exist_ok=True)
    tests = [TestResult(f"tests/test_x.py::test_ok_{i}[chromium]", "passed", 0.4) for i in range(passed)]
    tests += [
        TestResult(f"tests/test_x.py::test_broken_{i}[chromium]", "failed", 1.2, message=f"{message} #{i}",
                   details="E   AssertionError", steps=["Open home", "Assert heading 'Installation' is visible"])
        for i in range(failures)
    ]
    summary = RunSummary(exit_status=1 if failures else 0, passed=passed, failed=failures, run_dir=run_dir,
                         tests=tests, base_url="http://127.0.0.1", duration=3.0)
    report = run_dir / "summary.html"
    report.write_text(render_summary_html(summary), encoding="utf-8")
    return summary, report
