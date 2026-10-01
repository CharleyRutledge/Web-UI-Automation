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
    run = invoke_cli(tmp_path, ["scenarios/test_quick.py"], config=base_config(f"http://localhost:{port}"))
    assert run.returncode == 2 and run.run_dir is None
    assert f"Nothing is answering at http://localhost:{port}" in run.output
    assert '--start "<command>"' in run.output


def test_on_github_localhost_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    message = check_running(f"http://localhost:{free_port()}")
    assert "'localhost' means GitHub's own machine, not your computer" in message


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
    assert outcomes["test_served_securely"] == "skipped"  # local: HTTPS and headers are for the deployed site


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
