from __future__ import annotations

import pytest

from ui_automation.config import load_settings
from ui_automation.reporting.summary import RunSummary


def _load(tmp_path, text: str):
    p = tmp_path / "s.yaml"
    p.write_text(text, encoding="utf-8")
    return load_settings(p)


def test_env_substituted_false_is_false(tmp_path, monkeypatch):
    monkeypatch.setenv("H", "false")
    s = _load(tmp_path, 'base_url: http://x\nheadless: "${H}"\n')
    assert s.headless is False


@pytest.mark.parametrize(
    "text",
    [
        "base_url: playwright.dev\n",
        "base_url: http://x\ntimeout_ms: -5\n",
        "base_url: http://x\nslow_mo_ms: abc\n",
        "base_url: http://x\nartifacts: on\n",
        "base_url: http://x\nviewport: 5\n",
        "base_url: http://x\nheadless: maybe\n",
        "- a\n",
    ],
)
def test_invalid_config_raises_value_error(tmp_path, text):
    with pytest.raises(ValueError):
        _load(tmp_path, text)


def test_status_includes_errors():
    assert "2 errors" in RunSummary(exit_status=1, errors=2).short_status()
