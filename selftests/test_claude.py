"""Claude analysis: the real Anthropic SDK over real HTTP to a local stand-in for the Messages API."""

from __future__ import annotations

import json
import time
from pathlib import Path

import anthropic
import pytest
import yaml

from harness import make_summary
from servers import Reply, anthropic_error, anthropic_message
from ui_automation.config import AiSettings, load_settings
from ui_automation.reporting.claude import analyze_run
from ui_automation.reporting.pipeline import notify_run

FAST = {"retry-after-ms": "0"}  # let the SDK retry immediately instead of backing off


@pytest.fixture
def api(anthropic_api, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SENTINEL-KEY")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", anthropic_api.url)
    return anthropic_api


AI = AiSettings(enabled=True, timeout_seconds=5)


def test_passing_run_makes_no_request(api, tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path, failures=0)
    assert analyze_run(s, AI) is None
    assert api.calls() == []


def test_failed_run_request_and_answer(api, tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path, failures=2)
    assert analyze_run(s, AI) == "What broke: the heading is missing."
    [call] = api.calls("/v1/messages")
    body = call.json()
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 2048
    assert body["fallbacks"] == "default"
    headers = {k.lower(): v for k, v in call.headers.items()}
    assert "server-side-fallback-2026-07-01" in headers.get("anthropic-beta", "")
    assert headers.get("x-api-key") == "sk-ant-SENTINEL-KEY"
    assert "plain text only" in body["system"].lower()
    prompt = body["messages"][0]["content"]
    assert "tests/test_x.py::test_broken_1[chromium]" in prompt
    assert "Stopped at step: Assert heading 'Installation' is visible" in prompt
    assert "under 150 words" in prompt


@pytest.mark.parametrize("settings", [AiSettings(enabled=False), AI])
def test_no_request_without_switch_or_key(anthropic_api, monkeypatch, tmp_path: Path, settings) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", anthropic_api.url)
    if settings.enabled:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    s, _ = make_summary(tmp_path)
    assert analyze_run(s, settings) is None and anthropic_api.calls() == []


@pytest.mark.parametrize(
    "reply, error",
    [
        (Reply(400, anthropic_error("invalid_request_error", "Your credit balance is too low")), anthropic.BadRequestError),
        (Reply(401, anthropic_error("authentication_error", "invalid x-api-key")), anthropic.AuthenticationError),
        (Reply(404, anthropic_error("not_found_error", "model: claude-nope")), anthropic.NotFoundError),
    ],
)
def test_client_errors_are_not_retried(api, tmp_path: Path, reply: Reply, error) -> None:
    api.script("/v1/messages", reply)
    s, _ = make_summary(tmp_path)
    with pytest.raises(error):
        analyze_run(s, AI)
    assert len(api.calls("/v1/messages")) == 1


def test_rate_limit_is_retried_then_succeeds(api, tmp_path: Path) -> None:
    api.script("/v1/messages", Reply(429, anthropic_error("rate_limit_error", "slow down"), headers=FAST))
    s, _ = make_summary(tmp_path)
    assert analyze_run(s, AI) == "What broke: the heading is missing."
    assert len(api.calls("/v1/messages")) == 2


@pytest.mark.parametrize("status, kind", [(500, "api_error"), (529, "overloaded_error")])
def test_server_errors_give_up_after_retries(api, tmp_path: Path, status: int, kind: str) -> None:
    api.script("/v1/messages", *[Reply(status, anthropic_error(kind, "boom"), headers=FAST)] * 3)
    s, _ = make_summary(tmp_path)
    with pytest.raises(anthropic.APIStatusError):
        analyze_run(s, AI)
    assert len(api.calls("/v1/messages")) == 3  # first try + 2 retries


def test_refusal_gives_no_analysis(api, tmp_path: Path) -> None:
    api.script("/v1/messages", Reply(200, anthropic_message("", stop_reason="refusal")))
    s, _ = make_summary(tmp_path)
    assert analyze_run(s, AI) is None


def test_empty_answer_gives_no_analysis(api, tmp_path: Path) -> None:
    api.script("/v1/messages", Reply(200, anthropic_message("   ")))
    s, _ = make_summary(tmp_path)
    assert analyze_run(s, AI) is None


def test_timeout(api, tmp_path: Path) -> None:
    api.script("/v1/messages", *[Reply(200, anthropic_message("late"), delay=4)] * 3)
    s, _ = make_summary(tmp_path)
    start = time.monotonic()
    with pytest.raises(anthropic.APITimeoutError):
        analyze_run(s, AiSettings(enabled=True, timeout_seconds=0.5))
    assert time.monotonic() - start < 8


def test_huge_failure_output_is_capped(api, tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path, failures=200, message="y" * 2000)
    analyze_run(s, AI)
    assert len(api.calls("/v1/messages")[0].json()["messages"][0]["content"]) <= 20000


def _run_dir_with_summary(tmp_path: Path, failures: int) -> tuple[Path, object]:
    s, _ = make_summary(tmp_path / "run", failures=failures)
    (s.run_dir / "summary.json").write_text(json.dumps(s.to_dict()), encoding="utf-8")
    cfg = tmp_path / "s.yaml"
    cfg.write_text(yaml.safe_dump({"base_url": "http://127.0.0.1", "ai": {"enabled": True, "timeout_seconds": 5}}))
    return s.run_dir, load_settings(cfg)


def test_pipeline_puts_analysis_in_report(api, tmp_path: Path) -> None:
    run_dir, settings = _run_dir_with_summary(tmp_path, failures=1)
    report = notify_run(run_dir, settings)
    assert (run_dir / "claude_summary.txt").read_text() == "What broke: the heading is missing."
    assert "Claude&#x27;s analysis" in report.read_text() or "Claude's analysis" in report.read_text()


def test_pipeline_survives_api_failure(api, tmp_path: Path, capsys) -> None:
    api.script("/v1/messages", Reply(400, anthropic_error("invalid_request_error", "Your credit balance is too low")))
    run_dir, settings = _run_dir_with_summary(tmp_path, failures=1)
    report = notify_run(run_dir, settings)
    out = capsys.readouterr().out
    assert "Claude analysis skipped" in out and "credit balance is too low" in out
    assert report.is_file() and not (run_dir / "claude_summary.txt").exists()
    assert "SENTINEL" not in out
