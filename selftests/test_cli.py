"""The real CLI (`python -m ui_automation`) end to end: exit codes, selection, bad input, interrupts,
concurrency, missing tools, and a full run with email + Telegram + Claude all connected."""

from __future__ import annotations

import re

import os
import shutil
import threading
from pathlib import Path

import pytest

from harness import CliRun, base_config, invoke_cli
from servers import Reply, anthropic_error


def quick(run_cli, site, *args: str, **kwargs) -> CliRun:
    return run_cli(["scenarios/test_quick.py", *args], config=kwargs.pop("config", None) or base_config(site.url), **kwargs)


def test_passing_run(run_cli, site) -> None:
    r = quick(run_cli, site)
    assert r.returncode == 0, r.output
    s = r.summary
    assert (s["ok"], s["passed"], s["failed"], s["skipped"]) == (True, 1, 0, 2)
    assert (r.run_dir / "summary.html").is_file() and (r.reports_dir / "latest" / "summary.html").is_file()
    assert "Traceback" not in r.output


def test_failing_run(run_cli, site) -> None:
    r = quick(run_cli, site, env={"SCENARIO_FAIL": "1"})
    assert r.returncode == 1
    t = r.test("test_quick_fail")
    assert t["outcome"] == "failed" and "arithmetic is broken" in t["message"]


def test_selection_is_respected(run_cli, site) -> None:
    r = quick(run_cli, site, "-k", "test_quick_pass")
    assert r.returncode == 0 and r.summary["total"] == 1


def test_no_tests_collected(run_cli, site) -> None:
    r = quick(run_cli, site, "-k", "matches_nothing")
    assert r.returncode == 5, r.output  # pytest's "no tests collected"
    s = r.summary
    assert s["total"] == 0 and s["ok"] is False
    assert (r.run_dir / "summary.html").is_file()


@pytest.mark.parametrize(
    "text, message",
    [
        ("base_url: nope\n", "base_url must start with"),
        ("base_url: [unclosed\n", "Invalid configuration"),
        ("- just\n- a list\n", "must be a mapping"),
        ("base_url: http://x\ntimeout_ms: -1\n", "timeout_ms must be >= 1"),
    ],
)
def test_bad_config_exits_cleanly(tmp_path: Path, text: str, message: str) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(text, encoding="utf-8")
    r = invoke_cli(tmp_path, ["scenarios/test_quick.py"], cli_args=["--config", str(cfg)])
    assert r.returncode == 2, r.output
    assert message in r.stderr and "Traceback" not in r.output
    assert r.run_dir is None, "no run folder for a run that never started"


def test_missing_config_file(tmp_path: Path) -> None:
    r = invoke_cli(tmp_path, ["scenarios/test_quick.py"], cli_args=["--config", str(tmp_path / "absent.yaml")])
    assert r.returncode == 2 and "No such file" in r.stderr and "Traceback" not in r.output


def test_relative_config_path_from_another_directory(tmp_path: Path, site) -> None:
    import subprocess
    import sys

    import yaml

    (tmp_path / "rel.yaml").write_text(yaml.safe_dump(base_config(site.url)), encoding="utf-8")
    env = {**{k: v for k, v in os.environ.items()}, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
           "WEB_UI_REPORTS_DIR": str(tmp_path / "reports")}
    p = subprocess.run([sys.executable, "-m", "ui_automation", "--config", "rel.yaml", "--", "scenarios/test_quick.py"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


def test_unwritable_reports_folder(tmp_path: Path, site) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    r = quick(lambda *a, **k: invoke_cli(tmp_path, *a, **k), site, reports_dir=blocker / "reports")
    assert r.returncode == 2 and "Cannot create a run folder" in r.stderr and "Traceback" not in r.output


def test_ctrl_c_is_handled(run_cli, site) -> None:
    r = quick(run_cli, site, env={"SCENARIO_SLEEP": "60"}, interrupt_after=6, timeout=60)
    assert r.returncode == 2, r.output  # pytest's "interrupted"
    assert "Traceback" not in r.stderr, r.stderr
    assert r.summary["exit_status"] == 2, "results up to the interrupt are still recorded"


def test_runs_in_the_same_second_get_separate_folders(tmp_path: Path, site) -> None:
    reports = tmp_path / "shared-reports"
    results: list[CliRun] = []

    def go(i: int) -> None:
        results.append(quick(lambda *a, **k: invoke_cli(tmp_path / f"w{i}", *a, **k), site, reports_dir=reports))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(r.returncode == 0 for r in results), [r.output for r in results if r.returncode]
    runs = [p for p in reports.iterdir() if p.is_dir() and p.name != "latest"]
    assert len(runs) == 4, sorted(p.name for p in runs)
    assert all((p / "summary.json").is_file() for p in runs)
    assert (reports / "latest" / "summary.json").is_file()


def test_without_ffmpeg_videos_stay_webm(run_cli, site) -> None:
    path = os.pathsep.join(d for d in os.environ["PATH"].split(os.pathsep) if not (Path(d) / "ffmpeg").exists())
    assert shutil.which("ffmpeg", path=path) is None
    r = run_cli(["scenarios/test_browser_scenarios.py", "-k", "test_passes"], config=base_config(site.url),
                env={"PATH": path})
    assert r.returncode == 0, r.output
    videos = r.test("test_passes")["videos"]
    assert videos and all(v.endswith(".webm") for v in videos)


def test_open_without_a_browser_does_not_crash(run_cli, site) -> None:
    r = quick(run_cli, site, cli_args=["--open"], env={"BROWSER": "false", "DISPLAY": "", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and "Traceback" not in r.output


def test_full_run_with_every_notification(run_cli, site, smtp, telegram_api, anthropic_api) -> None:
    """A failing run with email, Telegram and Claude all switched on, each talking to a real local server."""
    controller, sink = smtp()
    cfg = base_config(
        site.url,
        ai={"enabled": True, "timeout_seconds": 10},
        notifications={
            "email": {"enabled": True, "smtp_host": "127.0.0.1", "smtp_port": controller.port, "use_tls": False,
                      "from_addr": "ci@example.com", "to_addrs": ["qa@example.com"]},
            "telegram": {"enabled": True, "bot_token": "${TELEGRAM_BOT_TOKEN}", "chat_id": "${TELEGRAM_CHAT_ID}"},
        },
    )
    r = quick(run_cli, site, config=cfg, env={
        "SCENARIO_FAIL": "1", "TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "77",
        "TELEGRAM_API_BASE": telegram_api.url, "ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_BASE_URL": anthropic_api.url,
    })
    assert r.returncode == 1, r.output
    # Claude was asked about the real failure, and its answer is in the report.
    [claude_call] = anthropic_api.calls("/v1/messages")
    assert "test_quick_fail" in claude_call.body.decode() and "arithmetic is broken" in claude_call.body.decode()
    assert (r.run_dir / "claude_summary.txt").read_text() == "What broke: the heading is missing."
    assert "What broke: the heading is missing." in (r.run_dir / "summary.html").read_text()
    # Telegram got the report as a document with a short caption.
    [doc] = telegram_api.calls("sendDocument")
    body = doc.body.decode(errors="replace")
    assert "FAILED" in body and "Quick fail" in body
    # Named after the site (the settings' name, by default its address) so several suites can share a chat.
    assert re.search(r'filename="127\.0\.0\.1-\d+-failed-\d{8}_\d{6}\.html"', body), body[:400]
    assert re.search(r"🌐 127\.0\.0\.1:\d+\r?\n❌ FAILED", body)
    assert telegram_api.calls("getUpdates") == []
    # Email arrived with the same report attached.
    [(sender, rcpts, msg)] = sink.messages
    from email.header import decode_header, make_header

    subject = str(make_header(decode_header(msg["Subject"])))
    assert rcpts == ["qa@example.com"] and re.match(r"\[UI Automation\] 127\.0\.0\.1:\d+: ❌ FAILED", subject), subject
    assert any(part.get_filename() == "ui-test-report.html" for part in msg.walk())


def test_notification_failures_never_break_the_run(run_cli, site, telegram_api, anthropic_api) -> None:
    telegram_api.script("sendDocument", Reply(401, {"ok": False}))  # a bad token: permanent, so not retried
    telegram_api.script("sendMessage", Reply(401, {"ok": False}))  # ...and the text fallback fails too
    anthropic_api.script("/v1/messages", *[Reply(400, anthropic_error("invalid_request_error", "credit too low"))] * 3)
    cfg = base_config(site.url, ai={"enabled": True, "timeout_seconds": 5},
                      notifications={"telegram": {"enabled": True, "bot_token": "1:x", "chat_id": "5"},
                                     "email": {"enabled": True, "smtp_host": "127.0.0.1", "smtp_port": 1,
                                               "from_addr": "a@b.c", "to_addrs": ["d@e.f"], "use_tls": False}})
    r = quick(run_cli, site, config=cfg, env={"SCENARIO_FAIL": "1", "TELEGRAM_API_BASE": telegram_api.url,
                                              "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": anthropic_api.url})
    assert r.returncode == 1  # the test result, not a notification error
    assert "Claude analysis skipped" in r.output and "credit too low" in r.output
    assert "could not attach the report (Telegram sendDocument returned HTTP 401)" in r.output
    assert "Telegram notification skipped: Telegram sendMessage returned HTTP 401" in r.output
    assert "Email notification skipped" in r.output
    assert (r.run_dir / "summary.html").is_file() and "Traceback" not in r.output


def _publish(args: tuple[str, str]) -> str | None:
    from ui_automation.cli import publish_latest_report

    try:
        publish_latest_report(Path(args[0]), Path(args[1]))
        return None
    except Exception as exc:  # noqa: BLE001 - report any crash
        return f"{type(exc).__name__}: {exc}"


def test_latest_is_published_safely_by_simultaneous_runs(tmp_path: Path) -> None:
    """16 processes publish reports/latest at the same moment (as parallel CI runs can). Before the fix,
    most crashed and `latest` mixed files from different runs."""
    from multiprocessing import get_context

    reports = tmp_path / "reports"
    runs = []
    for i in range(16):
        run = reports / f"2026_{i:02d}"
        for folder in ("screenshots", "failure-screenshots", "videos", "traces"):
            for j in range(20):
                f = run / folder / f"t{j}" / "file.bin"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(os.urandom(1000))
        for name in ("report.html", "summary.json", "summary.html"):
            (run / name).write_text(f"run {i}")
        runs.append(run)
    with get_context("spawn").Pool(16) as pool:
        errors = [e for e in pool.map(_publish, [(str(r), str(reports)) for r in runs]) if e]
    assert errors == []
    latest = reports / "latest"
    assert len({(latest / n).read_text() for n in ("report.html", "summary.json", "summary.html")}) == 1
    assert len(list((latest / "videos").iterdir())) == 20
    assert [p.name for p in reports.iterdir() if p.name.startswith(".")] == [], "no staging/lock leftovers"


def test_url_option_tests_that_site_and_names_the_report_after_it(run_cli, site) -> None:
    cfg = base_config("https://not-this-site.example", name="Configured name")
    r = run_cli(["scenarios/test_quick.py"], config=cfg, cli_args=["--url", site.url])
    assert r.returncode == 0, r.output
    assert r.summary["base_url"] == site.url.rstrip("/")
    assert r.summary["name"] == site.url.split("://", 1)[1].rstrip("/")  # not the file's name


@pytest.mark.parametrize("url", ["statespend.ie", "ftp://x.ie", "javascript:alert(1)", "https://", "https://a b.ie", ""])
def test_url_option_rejects_non_web_addresses(run_cli, url: str) -> None:
    r = run_cli(["scenarios/test_quick.py"], cli_args=["--url", url])
    assert r.returncode == 2 and "must start with http:// or https://" in r.output and r.run_dir is None


def test_all_browsers_option_is_accepted(tmp_path: Path, site) -> None:
    run = invoke_cli(tmp_path, ["scenarios/test_browser_scenarios.py", "-k", "test_passes"],
                     config=base_config(site.url, browsers=["chromium"], browser_rotation="daily"),
                     cli_args=["--all-browsers"])
    assert run.returncode == 0, run.output
    assert "Browser for today" not in run.output  # one browser only: nothing takes turns
