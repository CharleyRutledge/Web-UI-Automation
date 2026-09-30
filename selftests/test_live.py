"""Live checks against the real services (weekly / on demand; needs the real secrets):

    python -m pytest -c selftests/pytest.ini selftests -m live

A deliberately failing browser test runs through the real CLI, so the real Anthropic API and the real
Telegram Bot API are both exercised end to end. The Telegram message is marked as a self-check.
"""

from __future__ import annotations

import os

import pytest

from harness import base_config, invoke_cli

pytestmark = pytest.mark.live

# Captured at import time: the autouse fixture strips secrets from every test's environment.
LIVE = {k: os.environ.get(k, "") for k in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}


@pytest.mark.skipif(not all(LIVE[k] for k in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN")), reason="needs real secrets")
def test_real_claude_and_telegram(tmp_path, site) -> None:
    cfg = base_config(site.url, ai={"enabled": True},
                      notifications={"telegram": {"enabled": True, "bot_token": "${TELEGRAM_BOT_TOKEN}",
                                                  "chat_id": "${TELEGRAM_CHAT_ID}"}})
    run = invoke_cli(tmp_path, ["scenarios/test_browser_scenarios.py", "-k", "test_missing_element"],
                     config=cfg, env={k: v for k, v in LIVE.items() if v})
    assert run.returncode == 1, run.output
    assert "Claude summary:" in run.output, run.output
    assert "Claude analysis skipped" not in run.output
    assert "Telegram notification skipped" not in run.output
    analysis = (run.run_dir / "claude_summary.txt").read_text()
    assert "*" not in analysis and len(analysis.split()) < 250, analysis
