from __future__ import annotations

import pytest

from ui_automation.config import load_settings
from ui_automation.reporting.summary import RunSummary
from ui_automation.reporting.telegram_notify import send_run_telegram
from ui_automation.config import TelegramSettings


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


def test_telegram_failure_does_not_leak_token(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("https://api.telegram.org/botSECRET/sendMessage")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(RuntimeError) as exc:
        send_run_telegram(RunSummary(0), TelegramSettings(True, "SECRET", "1"))
    assert "SECRET" not in str(exc.value)


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.ok, self.status_code = payload, status < 400, status

    def json(self):
        return self._p


def _fake_post(calls, updates):
    def post(url, json=None, timeout=None):
        calls.append((url.rsplit("/", 1)[1], json))
        return _Resp({"result": updates} if url.endswith("getUpdates") else {"ok": True})

    return post


def test_telegram_discovers_chat_id_when_missing(monkeypatch):
    import requests

    calls = []
    updates = [{"message": {"chat": {"id": 1}}}, {"message": {"chat": {"id": 42}}}]
    monkeypatch.setattr(requests, "post", _fake_post(calls, updates))
    send_run_telegram(RunSummary(0, passed=1), TelegramSettings(True, "T", ""))
    assert [c[0] for c in calls] == ["getUpdates", "sendMessage"]
    assert calls[1][1]["chat_id"] == "42"


def test_telegram_uses_configured_chat_id_without_discovery(monkeypatch):
    import requests

    calls = []
    monkeypatch.setattr(requests, "post", _fake_post(calls, []))
    send_run_telegram(RunSummary(0), TelegramSettings(True, "T", "7"))
    assert [c[0] for c in calls] == ["sendMessage"]


def test_telegram_skips_when_no_chat_found(monkeypatch, capsys):
    import requests

    calls = []
    monkeypatch.setattr(requests, "post", _fake_post(calls, []))
    send_run_telegram(RunSummary(0), TelegramSettings(True, "T", "${TELEGRAM_CHAT_ID}"))
    assert [c[0] for c in calls] == ["getUpdates"]
    assert "skipped" in capsys.readouterr().out
