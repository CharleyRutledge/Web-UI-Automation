"""Load and performance (weekly): python -m pytest -c selftests/pytest.ini selftests -m load"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests

from harness import base_config, invoke_cli, make_summary
from test_dashboard import Dashboard, seed_run
from ui_automation.reporting.report_html import _EMBED_BUDGET_BYTES, render_summary_html

pytestmark = pytest.mark.load


def report(name: str, **numbers: float) -> None:
    print(f"\n[load] {name}: " + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in numbers.items()))


def test_dashboard_under_concurrent_load(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    for i in range(2000):
        seed_run(reports, f"2026{i:010d}", ok=i % 7 != 0)
    d = DashboardProcess(reports)
    timings: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    def hit(_: int) -> None:
        with requests.Session() as s:
            for _ in range(25):
                start = time.perf_counter()
                try:
                    r = s.get(d.url + "/", timeout=30)
                    ok = r.status_code == 200 and "of 2000 runs" in r.text
                except requests.RequestException as exc:
                    ok, r = False, exc
                with lock:
                    timings.append(time.perf_counter() - start)
                    if not ok:
                        errors.append(str(r))

    try:
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(hit, range(16)))
        wall = time.perf_counter() - start
        full = time.perf_counter()
        everything = requests.get(d.url + "/?all=1", timeout=60)
        full = time.perf_counter() - full
        single = []
        with requests.Session() as s:
            for _ in range(20):
                t0 = time.perf_counter()
                s.get(d.url + "/", timeout=30)
                single.append(time.perf_counter() - t0)
    finally:
        d.stop()
    p50, p95 = statistics.median(timings), statistics.quantiles(timings, n=20)[-1]
    report("dashboard 2000 runs, 16 clients x 25 requests", requests=len(timings), errors=len(errors),
           p50_s=p50, p95_s=p95, max_s=max(timings), throughput_rps=len(timings) / wall, show_all_s=full,
           single_user_p50_s=statistics.median(single))
    assert not errors, errors[:3]
    assert everything.status_code == 200 and everything.text.count('class="badge') == 2000
    assert statistics.median(single) < 0.25, "one person using the dashboard gets a page in well under a second"
    assert p95 < 2.0, f"p95 {p95:.2f}s with 16 simultaneous clients (was ~14s before paging + caching)"


class DashboardProcess:
    """The dashboard in its own process (as users run it), so client load doesn't share its GIL."""

    def __init__(self, reports: Path) -> None:
        import subprocess
        import sys

        from harness import REPO
        from servers import free_port

        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen([sys.executable, "-m", "ui_automation", "--ui", "--ui-port", str(self.port)],
                                     cwd=REPO, env={**os.environ, "WEB_UI_REPORTS_DIR": str(reports)},
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                requests.get(self.url, timeout=5)
                return
            except requests.ConnectionError:
                time.sleep(0.2)
        raise RuntimeError("dashboard did not start")

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)


def test_report_downloads_under_load(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    run = seed_run(reports, "20260101_000000")
    (run / "report.html").write_bytes(os.urandom(5_000_000))
    d = Dashboard(reports)
    try:
        def fetch(_: int) -> int:
            return len(requests.get(d.url + "/reports/20260101_000000/report.html", timeout=60).content)

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=12) as pool:
            sizes = list(pool.map(fetch, range(48)))
        wall = time.perf_counter() - start
    finally:
        d.server.shutdown()
    report("5 MB report x 48 downloads", seconds=wall, mb_per_s=48 * 5 / wall)
    assert sizes == [5_000_000] * 48


def test_500_test_run_through_the_pipeline(tmp_path: Path, site, smtp, telegram_api) -> None:
    controller, sink = smtp()
    cfg = base_config(site.url, notifications={
        "email": {"enabled": True, "smtp_host": "127.0.0.1", "smtp_port": controller.port, "use_tls": False,
                  "from_addr": "a@b.c", "to_addrs": ["d@e.f"]},
        "telegram": {"enabled": True, "bot_token": "1:x", "chat_id": "5"},
    })
    start = time.perf_counter()
    run = invoke_cli(tmp_path, ["scenarios/test_load.py"], config=cfg,
                     env={"SCENARIO_LOAD_TESTS": "500", "TELEGRAM_API_BASE": telegram_api.url})
    wall = time.perf_counter() - start
    s = run.summary
    size = (run.run_dir / "summary.html").stat().st_size
    report("500-test run", seconds=wall, summary_json_kb=(run.run_dir / "summary.json").stat().st_size // 1024,
           summary_html_kb=size // 1024)
    assert (s["total"], s["passed"], s["failed"]) == (500, 490, 10)
    assert run.returncode == 1 and wall < 180
    caption = telegram_api.calls("sendDocument")[0].body.decode(errors="replace")
    assert "10 of 500 tests failed" in caption and "…and 5 more" in caption
    assert len(sink.messages) == 1 and size < 5_000_000


def test_concurrent_cli_runs(tmp_path: Path, site) -> None:
    reports = tmp_path / "shared"
    results = []

    def go(i: int):
        return invoke_cli(tmp_path / f"w{i}", ["scenarios/test_browser_scenarios.py", "-k", "test_passes"],
                          config=base_config(site.url), reports_dir=reports)

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(go, range(4)))
    report("4 concurrent browser runs", seconds=time.perf_counter() - start)
    assert [r.returncode for r in results] == [0, 0, 0, 0], [r.output for r in results if r.returncode]
    runs = [p for p in reports.iterdir() if p.name != "latest"]
    assert len(runs) == 4
    for p in runs:
        data = json.loads((p / "summary.json").read_text())
        assert data["passed"] == 1 and data["tests"][0]["videos"], p


def test_report_stays_within_budget_with_huge_media(tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path / "run", failures=30, passed=30)
    for i, t in enumerate(s.tests):
        shot = tmp_path / "run" / f"shot{i}.png"
        shot.write_bytes(os.urandom(1_500_000))
        video = tmp_path / "run" / f"video{i}.webm"
        video.write_bytes(os.urandom(800_000))
        t.screenshots, t.steps, t.videos = [str(shot.name)], ["step"], [str(video.name)]
        if t.outcome == "failed":
            t.failure_screenshots = [str(shot.name)]
    start = time.perf_counter()
    html = render_summary_html(s)
    elapsed = time.perf_counter() - start
    report("report with 60 x (1.5 MB screenshot + 0.8 MB video)", seconds=elapsed, mb=len(html) / 1e6)
    assert len(html) < _EMBED_BUDGET_BYTES + 1_000_000, "stays under the Telegram/email-safe budget"
    assert "too large to include" in html
    assert elapsed < 10
