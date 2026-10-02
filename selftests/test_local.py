"""Testing apps on this computer or the local network: recognising local addresses, a clear message when
the app is not running, starting and stopping it with --start, self-signed HTTPS, and keys from .env."""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import pytest

from harness import base_config, invoke_cli
from servers import free_port
from ui_automation.env import load_dotenv
from ui_automation.local import AppServer, check_running, is_local

HERE = Path(__file__).resolve().parent


@pytest.mark.parametrize(
    "url, local",
    [
        ("http://localhost:3000", True), ("https://127.0.0.1:8443/x", True), ("http://[::1]:5173", True),
        ("http://192.168.1.20:8080", True), ("http://10.0.0.5", True), ("http://172.20.1.1", True),
        ("http://meridian.local", True), ("http://app.localhost:3000", True), ("http://0.0.0.0:8000", True),
        ("https://statespend.ie", False), ("http://8.8.8.8", False), ("http://172.32.0.1", False),
        ("http://localhost.evil.com", False), ("http://127.0.0.1.nip.io", False), ("", False),
    ],
)
def test_is_local(url: str, local: bool) -> None:
    assert is_local(url) is local


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _app_folder(tmp_path: Path) -> Path:
    """A tiny 'app': a folder of pages served by Python's own web server."""
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "index.html").write_text('<!doctype html><html lang="en"><head><title>Meridian Data</title></head>'
                                       "<body><main><h1>Meridian Data</h1></main></body></html>")
    return folder


def test_app_not_running_is_explained_and_nothing_runs(tmp_path: Path) -> None:
    port = free_port()
    # As on your own computer (CI sets GITHUB_ACTIONS, which switches to the GitHub wording tested below).
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=base_config(f"http://localhost:{port}"),
                     env={"GITHUB_ACTIONS": "false"})
    assert run.returncode == 2 and run.run_dir is None
    assert f"Nothing is answering at http://localhost:{port}" in run.output
    assert '--start "<command>"' in run.output


def test_on_github_localhost_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    message = check_running(f"http://localhost:{free_port()}")
    assert "'localhost' means GitHub's own machine, not your computer" in message
    assert '--start "<command>"' in message


def test_start_runs_the_app_tests_it_and_stops_it(tmp_path: Path) -> None:
    port = free_port()
    folder = _app_folder(tmp_path)
    cmd = f'"{sys.executable}" -m http.server {port} --bind 127.0.0.1'
    run = invoke_cli(tmp_path, ["site_audit", "-k", "loads or crawl"],
                     config=base_config(f"http://localhost:{port}", artifacts={"video": "off"}),
                     cli_args=["--start", cmd, "--start-in", str(folder)])
    assert run.returncode == 0, run.output
    assert "The app is up at" in run.output and "Stopped the app." in run.output
    assert run.summary["passed"] == 2
    assert "GET / HTTP" in (run.run_dir / "app-server.log").read_text(), "the app's own output is kept"
    assert not _listening(port), "the app must be stopped after the run"


def test_start_command_that_fails_is_reported(tmp_path: Path) -> None:
    cmd = f'"{sys.executable}" -c "import sys; print(\'boom: port in use\'); sys.exit(3)"'
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=base_config(f"http://localhost:{free_port()}"),
                     cli_args=["--start", cmd])
    assert run.returncode == 2
    assert "The start command exited (code 3)" in run.output and "app-server.log" in run.output
    assert "boom: port in use" in (run.run_dir / "app-server.log").read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process check")
def test_app_that_never_answers_is_stopped_after_the_timeout(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    cmd = f'"{sys.executable}" -c "import os, time; open(r\'{pid_file}\', \'w\').write(str(os.getpid())); time.sleep(60)"'
    start = time.monotonic()
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=base_config(f"http://localhost:{free_port()}"),
                     cli_args=["--start", cmd, "--start-timeout", "2"])
    assert run.returncode == 2 and "did not answer within 2 s" in run.output
    assert time.monotonic() - start < 30
    pid = int(pid_file.read_text())
    time.sleep(0.5)
    assert not _alive(pid), "the whole process group was stopped, not just the shell"


def _alive(pid: int) -> bool:
    """Running (a finished process waiting for a container's init to collect it is a zombie: not running)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/status")
    return not (status.is_file() and "\tZ" in next(l for l in status.read_text().splitlines() if l.startswith("State")))


def test_start_in_a_missing_folder(tmp_path: Path) -> None:
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=base_config("http://localhost:3000"),
                     cli_args=["--start", "echo hi", "--start-in", str(tmp_path / "nope")])
    assert run.returncode == 2 and "is not a folder" in run.output


def test_self_signed_https_on_a_local_app_works(tmp_path: Path, tls_site) -> None:
    """Local dev servers often use self-signed certificates; the browser and the checks accept them there."""
    run = invoke_cli(tmp_path, ["site_audit", "-k", "loads or crawl or secure"],
                     config=base_config(tls_site.url, artifacts={"video": "off"}, audit={"max_pages": 2}))
    outcomes = {t["nodeid"].split("::")[1].split("[")[0]: t["outcome"] for t in run.summary["tests"]}
    assert outcomes["test_crawl_found_the_site"] == "passed", run.output
    # local: HTTPS and headers are for the deployed site, so that check is not run (and the run says why)
    assert "test_served_securely" not in outcomes and "Served securely: a local app has no HTTPS" in run.output


def test_self_signed_allowance_can_be_switched_off(tmp_path: Path, tls_site) -> None:
    run = invoke_cli(tmp_path, ["site_audit", "-k", "crawl"],
                     config=base_config(tls_site.url, artifacts={"video": "off"}, audit={"max_pages": 1},
                                        allow_self_signed="off"))
    t = run.test("test_crawl_found_the_site")
    assert t["outcome"] == "failed" and "ERR_CERT_AUTHORITY_INVALID" in t["message"] + t["details"]


def test_public_site_certificates_are_still_verified() -> None:
    """Only local addresses get the self-signed allowance (a public name with a bad certificate does not)."""
    assert not is_local("https://self-signed.badssl.com/")


def test_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text('# my keys\nexport TELEGRAM_CHAT_ID=42\nANTHROPIC_API_KEY="sk-quoted value"\n'
                   "UNQUOTED=abc # comment\nALREADY_SET=from-file\nnot a line\n\n", encoding="utf-8")
    for name in ("TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY", "UNQUOTED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-shell")
    loaded = load_dotenv(env)
    assert sorted(loaded) == ["ANTHROPIC_API_KEY", "TELEGRAM_CHAT_ID", "UNQUOTED"]
    assert os.environ["TELEGRAM_CHAT_ID"] == "42" and os.environ["ANTHROPIC_API_KEY"] == "sk-quoted value"
    assert os.environ["UNQUOTED"] == "abc" and os.environ["ALREADY_SET"] == "from-shell"
    assert load_dotenv(tmp_path / "missing.env") == []


def test_dotenv_is_git_ignored() -> None:
    lines = (HERE.parent / ".gitignore").read_text().splitlines()
    assert ".env" in lines and (HERE.parent / ".env.example").is_file()


def test_app_server_reports_readiness_directly() -> None:
    server = AppServer(f'"{sys.executable}" -c "import time; time.sleep(30)"', f"http://127.0.0.1:{free_port()}",
                       timeout=1.5)
    assert "did not answer within 1.5 s" in server.start()
    assert server.process is None


# ------------------------------------------------------------------ apps started in the background (Docker)

_LAUNCHER = """
import subprocess, sys
# Like `docker compose up -d`: start the server in the background, then exit successfully.
proc = subprocess.Popen([sys.executable, "-m", "http.server", "{port}", "--bind", "127.0.0.1"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
open("server.pid", "w").write(str(proc.pid))
print("launched", proc.pid)
"""
_STOPPER = """
import os, signal
# Like `docker compose down`.
os.kill(int(open("server.pid").read()), signal.SIGTERM)
print("stopped")
"""


def _docker_like_app(tmp_path: Path) -> tuple[Path, int]:
    folder = _app_folder(tmp_path)
    port = free_port()
    (folder / "launch.py").write_text(_LAUNCHER.format(port=port))
    (folder / "stop.py").write_text(_STOPPER)
    return folder, port


def _app_config(folder: Path, port: int, **app) -> dict:
    return base_config(f"http://localhost:{port}", artifacts={"video": "off"}, app={
        "start": f'"{sys.executable}" launch.py', "stop": f'"{sys.executable}" stop.py', "start_in": str(folder), **app})


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals in the stand-in stop script")
def test_background_start_and_stop_command_from_settings(tmp_path: Path) -> None:
    folder, port = _docker_like_app(tmp_path)
    run = invoke_cli(tmp_path, ["site_audit", "-k", "loads or crawl"], config=_app_config(folder, port))
    assert run.returncode == 0, run.output
    assert "The app is up at" in run.output and "Stopped the app." in run.output
    log = (run.run_dir / "app-server.log").read_text()
    assert "launched" in log and "--- stop:" in log and "stopped" in log
    time.sleep(0.5)
    assert not _listening(port), "the stop command must have stopped the app"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals in the stand-in stop script")
def test_app_already_running_is_left_alone(tmp_path: Path) -> None:
    folder, port = _docker_like_app(tmp_path)
    import subprocess

    subprocess.run([sys.executable, "launch.py"], cwd=folder, check=True, capture_output=True)  # you started it
    try:
        for _ in range(50):
            if _listening(port):
                break
            time.sleep(0.1)
        run = invoke_cli(tmp_path, ["site_audit", "-k", "loads"], config=_app_config(folder, port))
        assert run.returncode == 0, run.output
        assert "already running" in run.output and "Stopped the app." not in run.output
        assert _listening(port), "an app you started yourself must not be stopped"
    finally:
        subprocess.run([sys.executable, "stop.py"], cwd=folder, check=False, capture_output=True)


def test_stop_command_failure_is_reported_but_results_are_kept(tmp_path: Path) -> None:
    port = free_port()
    folder = _app_folder(tmp_path)
    run = invoke_cli(tmp_path, ["site_audit", "-k", "loads"], config=base_config(
        f"http://localhost:{port}", artifacts={"video": "off"}),
        cli_args=["--start", f'"{sys.executable}" -m http.server {port} --bind 127.0.0.1', "--start-in", str(folder),
                  "--stop", f'"{sys.executable}" -c "import sys; sys.exit(4)"'])
    assert run.returncode == 0 and run.summary["passed"] == 1, run.output
    assert "Could not stop the app cleanly: the stop command" in run.output and "exited with code 4" in run.output
    assert not _listening(port), "the start command's own process is still stopped"


def test_failed_start_still_runs_the_stop_command(tmp_path: Path) -> None:
    """A half-started app (e.g. one container up, one crashed) is cleaned up."""
    marker = tmp_path / "stop-ran"
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=base_config(f"http://localhost:{free_port()}"),
                     cli_args=["--start", f'"{sys.executable}" -c "import sys; sys.exit(1)"',
                               "--stop", f'"{sys.executable}" -c "open(r\'{marker}\', \'w\').write(\'x\')"'])
    assert run.returncode == 2 and "exited (code 1)" in run.output
    assert marker.is_file()


def test_no_start_tests_the_running_app_only(tmp_path: Path) -> None:
    folder, port = _docker_like_app(tmp_path)
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=_app_config(folder, port), cli_args=["--no-start"])
    assert run.returncode == 2 and "Nothing is answering" in run.output and not _listening(port)


@pytest.mark.parametrize("raw, field", [("{start: [npm, run]}", "app.start"), ("{start_timeout: 0}", "app.start_timeout"),
                                        ("{stop: 5}", "app.stop"), ("[1]", "'app'")])
def test_app_settings_rejected(tmp_path: Path, raw: str, field: str) -> None:
    from ui_automation.config import load_settings

    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"base_url: http://localhost:3000\napp: {raw}\n")
    with pytest.raises(ValueError, match=field.replace(".", r"\.")):
        load_settings(cfg)


# ------------------------------------------------------------------ found on the first Windows run


def test_axe_with_windows_line_endings_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git on Windows turned axe.min.js's LF into CRLF, the checksum failed and no page was scanned."""
    import ui_automation.accessibility as a11y

    crlf = tmp_path / "axe.min.js"
    crlf.write_bytes(a11y._AXE_PATH.read_bytes().replace(b"\n", b"\r\n"))
    monkeypatch.setattr(a11y, "_AXE_PATH", crlf)
    assert "axe" in a11y.axe_source()[:2000]


def test_tampered_axe_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ui_automation.accessibility as a11y

    bad = tmp_path / "axe.min.js"
    bad.write_bytes(a11y._AXE_PATH.read_bytes() + b"\nwindow.stolen = document.cookie;")
    monkeypatch.setattr(a11y, "_AXE_PATH", bad)
    with pytest.raises(RuntimeError, match="does not match axe-core"):
        a11y.axe_source()


def test_git_keeps_vendored_files_byte_for_byte() -> None:
    import subprocess

    out = subprocess.run(["git", "check-attr", "text", "ui_automation/accessibility/vendor/axe.min.js"],
                         cwd=HERE.parent, capture_output=True, text=True).stdout
    assert out.strip().endswith("text: unset"), out


def test_open_shows_the_summary_report(tmp_path: Path, site) -> None:
    opened = tmp_path / "opened.txt"
    recorder = tmp_path / "browser.py"
    recorder.write_text(f"import sys; open(r'{opened}', 'w').write(sys.argv[1])")
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=base_config(site.url), cli_args=["--open"],
                     env={"BROWSER": f'"{sys.executable}" "{recorder}" %s'})
    assert run.returncode == 0, run.output
    assert opened.read_text().endswith("/latest/summary.html")


def test_waiting_for_a_slow_app_shows_progress(capsys: pytest.CaptureFixture) -> None:
    server = AppServer(f'"{sys.executable}" -c "import time; time.sleep(30)"', f"http://127.0.0.1:{free_port()}",
                       timeout=2.5)
    server.progress_every = 0.8
    server.start()
    out = capsys.readouterr().out
    assert out.count("still waiting for") >= 2 and "s of 2.5 s" in out


@pytest.mark.skipif(sys.platform == "win32", reason="sends SIGINT")
def test_ctrl_c_while_waiting_stops_cleanly(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    cmd = f'"{sys.executable}" -c "import os, time; open(r\'{pid_file}\', \'w\').write(str(os.getpid())); time.sleep(60)"'
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=base_config(f"http://localhost:{free_port()}"),
                     cli_args=["--start", cmd, "--start-timeout", "60"], interrupt_after=3)
    assert run.returncode == 130, run.output
    assert "Traceback" not in run.output and "interrupted while waiting for the app" in run.output
    time.sleep(0.5)
    assert not _alive(int(pid_file.read_text())), "the app being started is shut down again"
