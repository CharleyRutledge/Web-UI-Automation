"""Real browser runs through the real CLI: every way a UI test can end, and the artifacts each leaves.

One CLI run executes all scenarios (scenarios/test_browser_scenarios.py) against the local site;
each test below checks one scenario's outcome, message and artifacts in summary.json / the reports.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from harness import CliRun, base_config, invoke_cli

HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory, site, tls_site) -> CliRun:
    result = invoke_cli(
        tmp_path_factory.mktemp("scenarios"),
        ["scenarios/test_browser_scenarios.py"],
        # The local test servers would otherwise get the self-signed allowance; this run checks that an
        # untrusted certificate is reported as a failure, as it is for any public site.
        config=base_config(site.url, allow_self_signed="off"),
        env={"SCENARIO_TLS_URL": tls_site.url + "/"},
    )
    assert result.run_dir is not None, result.output
    return result


def artifact(run: CliRun, rel: str) -> Path:
    return run.run_dir / rel  # type: ignore[operator]


def test_exit_code_and_counts(run: CliRun) -> None:
    s = run.summary
    assert run.returncode == 1, run.output
    assert (s["passed"], s["skipped"], s["errors"]) == (3, 2, 1), [(t["nodeid"], t["outcome"]) for t in s["tests"]]
    assert s["failed"] == 8
    assert s["total"] == len(s["tests"]) == 14
    assert s["ok"] is False


def test_passing_test_has_video_and_step_screenshots_but_no_trace(run: CliRun) -> None:
    t = run.test("test_passes")
    assert t["outcome"] == "passed"
    assert t["screenshots"] and all(artifact(run, p).is_file() for p in t["screenshots"])
    assert any(v.endswith(".webm") for v in t["videos"])
    assert any(v.endswith(".mp4") for v in t["videos"]) is HAS_FFMPEG
    assert t["traces"] == [] and t["failure_screenshots"] == []


def test_missing_element(run: CliRun) -> None:
    t = run.test("test_missing_element")
    assert t["outcome"] == "failed"
    assert "element(s) not found" in t["message"]
    assert 'get_by_role("heading", name="Installation")' in t["message"]
    assert t["steps"][-1] == "Assert heading 'Installation' is visible"
    for key in ("traces", "failure_screenshots", "videos"):
        assert t[key] and all(artifact(run, p).stat().st_size > 0 for p in t[key]), key


@pytest.mark.parametrize(
    "name, expected",
    [
        ("test_slow_page_times_out", "Timeout 3000ms exceeded"),
        ("test_server_error_page", "HTTP 500"),
        ("test_redirect_loop", "ERR_TOO_MANY_REDIRECTS"),
        ("test_offline", "ERR_INTERNET_DISCONNECTED"),
        ("test_dns_failure", "net::ERR_"),
        ("test_tls_certificate_error", "ERR_CERT_AUTHORITY_INVALID"),
    ],
)
def test_failures_explain_themselves(run: CliRun, name: str, expected: str) -> None:
    t = run.test(name)
    assert t["outcome"] == "failed"
    assert expected in t["message"], t["message"]
    assert t["traces"], "a failed browser test must keep its trace"


def test_throttled_network_is_slow_but_passes(run: CliRun) -> None:
    t = run.test("test_throttled_network_still_passes")
    assert t["outcome"] == "passed"
    assert t["duration"] >= 1.5


def test_fixture_error_is_reported_as_error(run: CliRun) -> None:
    t = run.test("test_setup_error")
    assert t["outcome"] == "error"
    assert "fixture exploded during setup" in t["message"]


def test_skip_and_xfail(run: CliRun) -> None:
    assert run.test("test_skipped")["outcome"] == "skipped"
    assert "not relevant on this platform" in run.test("test_skipped")["message"]
    assert run.test("test_expected_failure")["outcome"] == "skipped"


def test_reports_are_written_and_published(run: CliRun) -> None:
    for name in ("report.html", "summary.html", "summary.json"):
        assert artifact(run, name).is_file(), name
        assert (run.reports_dir / "latest" / name).is_file(), name
    html = artifact(run, "summary.html").read_text(encoding="utf-8")
    assert "FAILED" in html and "Missing element" in html and "Download trace" in html
    assert html.count("<video") >= 5


def test_no_playwright_output_is_lost(run: CliRun) -> None:
    raw = artifact(run, "playwright-output")
    webms = list(raw.rglob("*.webm"))
    copied = list(artifact(run, "videos").rglob("*.webm"))
    assert webms and len(copied) == len(webms)
    assert len(list(artifact(run, "traces").rglob("*.zip"))) == len(list(raw.rglob("trace.zip")))


def test_videos_have_a_poster_frame(run: CliRun) -> None:
    """Players show the page as the test left it instead of a black box (needs ffmpeg, like MP4)."""
    t = run.test("test_passes")
    poster = artifact(run, t["videos"][0]).parent / "poster.jpg"
    assert poster.is_file() is HAS_FFMPEG
    html = (run.run_dir / "summary.html").read_text(encoding="utf-8")
    assert ('poster="data:image/jpeg' in html) is HAS_FFMPEG


def test_video_count_counts_recordings_not_files(run: CliRun) -> None:
    html = (run.run_dir / "summary.html").read_text(encoding="utf-8")
    recordings = len({artifact(run, v).parent for t in run.summary["tests"] for v in t["videos"]})
    assert f"Videos: {recordings} ·" in html
