"""The dashboard as a real HTTP server: functionality, and attacks against it."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
import requests
import yaml

from harness import REPO, base_config
from servers import free_port

HOSTILE = '<img src=x onerror="alert(1)">'


def seed_run(reports: Path, name: str, ok: bool | None = True, *, summary: object | None = None) -> Path:
    d = reports / name
    d.mkdir(parents=True)
    (d / "report.html").write_text("<h1>full report</h1>")
    (d / "summary.html").write_text("<h1>summary</h1>")
    data = summary if summary is not None else {"ok": ok, "passed": 3, "failed": 0 if ok else 1, "skipped": 0, "errors": 0}
    (d / "summary.json").write_text(data if isinstance(data, str) else json.dumps(data))
    return d


class Dashboard:
    def __init__(self, reports: Path, **kwargs) -> None:
        from werkzeug.serving import make_server

        from ui_automation.webui import create_app

        self.reports = reports
        self.port = free_port()
        self.server = make_server("127.0.0.1", self.port, create_app(reports, **kwargs), threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.port}"

    def get(self, path: str, **kw) -> requests.Response:
        return requests.get(self.url + path, timeout=30, allow_redirects=False, **kw)

    def post(self, path: str, **kw) -> requests.Response:
        return requests.post(self.url + path, timeout=120, allow_redirects=False, **kw)

    def raw(self, request_line: str, host: str = "127.0.0.1") -> str:
        """Send an unnormalised request (requests/urllib would clean up ../ and %2e)."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(f"{request_line}\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while chunk := s.recv(65536):
                data += chunk
        return data.decode(errors="replace")


@pytest.fixture
def dash(tmp_path: Path) -> Iterator[Dashboard]:
    reports = tmp_path / "reports"
    seed_run(reports, "20260101_000000", ok=True)
    seed_run(reports, "20260102_000000", ok=False)
    seed_run(reports, "20260103_000000", summary="{not json")
    seed_run(reports, "20260104_000000", summary="[1, 2]")
    (reports / "latest").mkdir()
    d = Dashboard(reports)
    yield d
    d.server.shutdown()


# ---------------------------------------------------------------- functionality


def test_lists_runs_newest_first_and_survives_bad_summaries(dash: Dashboard) -> None:
    r = dash.get("/")
    assert r.status_code == 200
    html = r.text
    order = [html.index(n) for n in ("20260104_000000", "20260103_000000", "20260102_000000", "20260101_000000")]
    assert order == sorted(order) and "latest" not in html.split("<tbody>")[1]
    assert html.count("badge pass") == 1 and html.count("badge fail") == 1 and html.count("badge unknown") == 2
    assert 'href="/reports/20260101_000000/summary.html"' in html


def test_serves_reports(dash: Dashboard) -> None:
    assert dash.get("/reports/20260101_000000/report.html").text == "<h1>full report</h1>"
    assert dash.get("/reports/20260101_000000/nope.html").status_code == 404


def test_empty_reports_folder(tmp_path: Path) -> None:
    d = Dashboard(tmp_path / "does-not-exist")
    try:
        assert "No runs yet" in d.get("/").text
    finally:
        d.server.shutdown()


def test_run_button_runs_the_suite(tmp_path: Path, site, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "s.yaml"
    cfg.write_text(yaml.safe_dump(base_config(site.url)))
    monkeypatch.setenv("WEB_UI_CONFIG", str(cfg))
    monkeypatch.setenv("WEB_UI_REPORTS_DIR", str(tmp_path / "reports"))
    d = Dashboard(tmp_path / "reports")
    try:
        r = d.post("/run", headers={"Origin": d.url})
        assert r.status_code == 302 and "Run+finished+(exit+0)" in r.headers["Location"], r.headers["Location"]
        assert "badge pass" in d.get("/").text
    finally:
        d.server.shutdown()


def test_second_click_while_running_is_refused(tmp_path: Path, site, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "s.yaml"
    cfg.write_text(yaml.safe_dump(base_config(site.url)))
    monkeypatch.setenv("WEB_UI_CONFIG", str(cfg))
    monkeypatch.setenv("WEB_UI_REPORTS_DIR", str(tmp_path / "reports"))
    d = Dashboard(tmp_path / "reports")
    try:
        results: list[str] = []
        first = threading.Thread(target=lambda: results.append(d.post("/run").headers["Location"]))
        first.start()
        time.sleep(1.5)
        second = d.post("/run").headers["Location"]
        first.join()
        assert "already+in+progress" in second and "Run+finished" in results[0]
    finally:
        d.server.shutdown()


def test_run_timeout_is_reported(tmp_path: Path, site, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "s.yaml"
    cfg.write_text(yaml.safe_dump(base_config(site.url)))
    monkeypatch.setenv("WEB_UI_CONFIG", str(cfg))
    d = Dashboard(tmp_path / "reports", run_timeout=1)
    try:
        r = d.post("/run")
        assert r.status_code == 302 and "timed+out" in r.headers["Location"]
    finally:
        d.server.shutdown()


# ---------------------------------------------------------------- security


def test_message_parameter_is_escaped(dash: Dashboard) -> None:
    html = dash.get("/", params={"message": HOSTILE + "<script>alert(2)</script>"}).text
    assert HOSTILE not in html and "<script>alert(2)" not in html
    assert "&lt;img src=x" in html


def test_hostile_run_folder_names_are_escaped(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    seed_run(reports, '2026"><script>alert(3)</script>')
    d = Dashboard(reports)
    try:
        html = d.get("/").text
        assert "<script>alert(3)" not in html and "&lt;script&gt;alert(3)" in html
    finally:
        d.server.shutdown()


@pytest.mark.parametrize(
    "path",
    [
        "/reports/../conftest.py",
        "/reports/../../etc/passwd",
        "/reports/%2e%2e/conftest.py",
        "/reports/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/reports/..%2f..%2fetc%2fpasswd",
        "/reports/%252e%252e/%252e%252e/etc/passwd",
        "/reports//etc/passwd",
        "/reports/..\\..\\etc\\passwd",
        "/reports/20260101_000000/../../../etc/passwd",
    ],
)
def test_path_traversal_is_blocked(dash: Dashboard, path: str) -> None:
    response = dash.raw(f"GET {path} HTTP/1.1")
    status = int(response.split(" ", 2)[1])
    if status in (301, 308):  # Werkzeug merges "//": the redirect must stay inside /reports/ and 404 there
        location = next(l for l in response.split("\r\n") if l.lower().startswith("location:")).split(":", 1)[1].strip()
        assert "/reports/" in location and ".." not in location, location
        response = dash.raw(f"GET {location.split('127.0.0.1', 1)[-1]} HTTP/1.1")
        status = int(response.split(" ", 2)[1])
    assert status in (400, 404), response[:200]
    assert "root:" not in response and "conftest" not in response.split("\r\n\r\n", 1)[-1]


def test_symlink_out_of_reports_is_blocked(dash: Dashboard) -> None:
    os.symlink("/etc", dash.reports / "escape")
    os.symlink(REPO, dash.reports / "20260101_000000" / "repo")
    assert dash.get("/reports/escape/passwd").status_code == 404
    assert dash.get("/reports/20260101_000000/repo/conftest.py").status_code == 404


def test_cross_site_post_is_rejected(dash: Dashboard) -> None:
    assert dash.post("/run", headers={"Origin": "https://evil.example"}).status_code == 403
    assert dash.post("/run", headers={"Origin": "null"}).status_code == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8501", "127.0.0.1.evil.example"])
def test_dns_rebinding_host_is_rejected(dash: Dashboard, host: str) -> None:
    assert dash.get("/", headers={"Host": host}).status_code == 403
    assert dash.post("/run", headers={"Host": host, "Origin": f"http://{host}"}).status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", f"localhost:8501"])
def test_local_hosts_are_allowed(dash: Dashboard, host: str) -> None:
    assert dash.get("/", headers={"Host": host}).status_code == 200


def test_run_requires_post(dash: Dashboard) -> None:
    assert dash.get("/run").status_code == 405


def test_only_listens_on_loopback(tmp_path: Path) -> None:
    port = free_port()
    env = {**os.environ, "WEB_UI_REPORTS_DIR": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, "-m", "ui_automation", "--ui", "--ui-port", str(port)], cwd=REPO,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                requests.get(f"http://127.0.0.1:{port}/", timeout=1)
                break
            except requests.ConnectionError:
                time.sleep(0.2)
        out = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True).stdout if _has("ss") else _proc_net_listen()
        listeners = [line for line in out.splitlines() if f":{port}" in line or f"{port:04X}" in line]
        assert listeners, out
        assert all(("127.0.0.1" in l or "0100007F" in l) for l in listeners), listeners
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _has(cmd: str) -> bool:
    import shutil

    return shutil.which(cmd) is not None


def _proc_net_listen() -> str:
    lines = []
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines += [l for l in Path(f).read_text().splitlines()[1:] if l.split()[3] == "0A"]  # 0A = LISTEN
        except OSError:
            pass
    return "\n".join(l.split()[1] for l in lines)
