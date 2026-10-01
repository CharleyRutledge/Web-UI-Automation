"""Telegram notifications over real HTTP to a local stand-in for the Bot API: success and every failure."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from harness import make_summary
from servers import Reply, free_port
from ui_automation.config import TelegramSettings
from ui_automation.reporting.telegram_notify import build_caption, send_run_telegram

TOKEN = "123456:SENTINEL-TOKEN-abc"


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Retry pauses are recorded instead of slept, so the tests stay fast and can check them."""
    from ui_automation.reporting import telegram_notify

    recorded: list[float] = []
    monkeypatch.setattr(telegram_notify, "_sleep", recorded.append)
    return recorded


@pytest.fixture
def api(telegram_api, monkeypatch: pytest.MonkeyPatch, waits):
    monkeypatch.setenv("TELEGRAM_API_BASE", telegram_api.url)
    return telegram_api


@pytest.fixture
def summary(tmp_path: Path):
    return make_summary(tmp_path / "run", failures=1)


def tg(chat_id: str = "77", **kw) -> TelegramSettings:
    return TelegramSettings(enabled=True, bot_token=kw.pop("token", TOKEN), chat_id=chat_id, **{"timeout_seconds": 5, **kw})


def test_sends_report_as_document(api, summary) -> None:
    s, report = summary
    send_run_telegram(s, tg(), attachment=report)
    [call] = api.calls()
    assert call.path == f"/bot{TOKEN}/sendDocument"
    body = call.body.decode(errors="replace")
    assert 'name="chat_id"' in body and "\r\n77\r\n" in body
    assert "❌ FAILED — 1 of 2 tests failed" in body and "Full details are in the attached report." in body
    assert 'filename="ui-tests-failed-run.html"' in body and report.read_text() in body
    assert "parse_mode" not in body, "captions are plain text: no Markdown/HTML injection from test names"


def test_without_attachment_sends_text(api, summary) -> None:
    send_run_telegram(summary[0], tg(), attachment=None)
    [call] = api.calls()
    assert call.path.endswith("/sendMessage") and call.json()["chat_id"] == "77"


@pytest.mark.parametrize("chat_id", ["", "${TELEGRAM_CHAT_ID}"])
def test_finds_chat_from_recent_messages(api, summary, capsys, chat_id: str) -> None:
    send_run_telegram(summary[0], tg(chat_id=chat_id), attachment=summary[1])
    assert [c.path.rsplit("/", 1)[1] for c in api.calls()] == ["getUpdates", "sendDocument"]
    assert "\r\n4242\r\n" in api.calls("sendDocument")[0].body.decode(errors="replace")
    assert "Save this number as the TELEGRAM_CHAT_ID secret" in capsys.readouterr().out


def test_finds_chat_from_channel_post(api, summary) -> None:
    api.script("getUpdates", Reply(200, {"ok": True, "result": [{"update_id": 3, "channel_post": {"chat": {"id": -100}}}]}))
    send_run_telegram(summary[0], tg(chat_id=""), attachment=None)
    assert api.calls("sendMessage")[0].json()["chat_id"] == "-100"


def test_no_chat_found_skips(api, summary, capsys) -> None:
    api.script("getUpdates", Reply(200, {"ok": True, "result": []}))
    send_run_telegram(summary[0], tg(chat_id=""), attachment=summary[1])
    assert [c.path.rsplit("/", 1)[1] for c in api.calls()] == ["getUpdates"]
    assert "send your bot a message" in capsys.readouterr().out


@pytest.mark.parametrize(
    "reply, expected, attempts",
    [
        (Reply(401, {"ok": False, "description": "Unauthorized"}), "HTTP 401", 1),
        (Reply(400, {"ok": False, "description": "Bad Request: chat not found"}), "HTTP 400", 1),
        (Reply(429, {"ok": False, "parameters": {"retry_after": 5}}), "HTTP 429", 3),
        (Reply(500, "Internal error"), "HTTP 500", 3),
        (Reply(502, "<html>Bad gateway</html>"), "HTTP 502", 3),
        (Reply(200, "this is not json"), "non-JSON", 1),
        (Reply(drop=True), "ConnectionError", 3),
    ],
)
def test_api_errors_raise_without_leaking_the_token(api, summary, reply: Reply, expected: str, attempts: int) -> None:
    api.script("sendDocument", *[reply] * 3)
    api.script("sendMessage", *[reply] * 3)
    with pytest.raises(RuntimeError) as exc:
        send_run_telegram(summary[0], tg(), attachment=summary[1])
    assert expected in str(exc.value)
    assert "SENTINEL" not in str(exc.value)
    # "from None": the original requests error (whose text contains the URL + token) never appears in a traceback.
    assert exc.value.__cause__ is None and (exc.value.__context__ is None or exc.value.__suppress_context__)
    # Temporary failures are retried; permanent ones (bad token, unknown chat) are not.
    assert len(api.calls("sendDocument")) == attempts and len(api.calls("sendMessage")) == attempts


def test_dropped_connection_and_server_error_are_retried(api, summary, waits, capsys) -> None:
    """The real CI failure: Telegram reset the connection mid-upload (SSLError) and the report was lost."""
    api.script("sendDocument", Reply(drop=True), Reply(502, "Bad gateway"))
    send_run_telegram(summary[0], tg(), attachment=summary[1])
    assert len(api.calls("sendDocument")) == 3 and api.calls("sendMessage") == []
    assert waits == [3.0, 10.0]
    assert "retrying in 3s" in capsys.readouterr().out


def test_rate_limit_waits_as_long_as_telegram_asks(api, summary, waits) -> None:
    api.script("sendDocument", Reply(429, {"ok": False, "parameters": {"retry_after": 7}}))
    send_run_telegram(summary[0], tg(), attachment=summary[1])
    assert waits == [7.0] and len(api.calls("sendDocument")) == 2


def test_long_rate_limit_is_not_waited_out(api, summary, waits) -> None:
    api.script("sendDocument", Reply(429, {"ok": False, "parameters": {"retry_after": 3600}}))
    send_run_telegram(summary[0], tg(), attachment=summary[1])
    assert waits == [] and len(api.calls("sendDocument")) == 1 and len(api.calls("sendMessage")) == 1


def test_unattachable_report_still_sends_the_results(api, summary, capsys) -> None:
    s, report = summary
    s.run_url = "https://github.com/o/r/actions/runs/1"
    api.script("sendDocument", *[Reply(drop=True)] * 3)
    send_run_telegram(s, tg(), attachment=report)
    [message] = api.calls("sendMessage")
    text = message.json()["text"]
    assert text.startswith("❌ FAILED — 1 of 2 tests failed") and "attached report" not in text
    assert "(The report could not be attached: Telegram sendDocument request failed (ConnectionError).)" in text
    assert text.endswith("Full report: https://github.com/o/r/actions/runs/1") and "SENTINEL" not in text
    assert "sending the results as a message instead" in capsys.readouterr().out


def test_report_over_telegrams_limit_is_sent_as_text(api, summary, monkeypatch: pytest.MonkeyPatch) -> None:
    from ui_automation.reporting import telegram_notify

    monkeypatch.setattr(telegram_notify, "_DOCUMENT_LIMIT", 10)
    send_run_telegram(summary[0], tg(), attachment=summary[1])
    assert api.calls("sendDocument") == []
    assert "over Telegram's 50 MB limit" in api.calls("sendMessage")[0].json()["text"]


def test_timeout(api, summary) -> None:
    api.script("sendDocument", *[Reply(200, {"ok": True}, delay=5)] * 3)
    api.script("sendMessage", *[Reply(200, {"ok": True}, delay=5)] * 3)
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="ReadTimeout") as exc:
        send_run_telegram(summary[0], tg(timeout_seconds=0.3), attachment=summary[1])
    assert time.monotonic() - start < 6 and "SENTINEL" not in str(exc.value)


def test_connection_refused(monkeypatch: pytest.MonkeyPatch, summary, waits) -> None:
    monkeypatch.setenv("TELEGRAM_API_BASE", f"http://127.0.0.1:{free_port()}")
    with pytest.raises(RuntimeError, match="ConnectionError") as exc:
        send_run_telegram(summary[0], tg(), attachment=summary[1])
    assert "SENTINEL" not in str(exc.value) and len(waits) == 4  # 2 retries for the report, 2 for the message


@pytest.mark.parametrize(
    "settings",
    [
        TelegramSettings(enabled=False, bot_token=TOKEN, chat_id="1"),
        TelegramSettings(enabled=True, bot_token="", chat_id="1"),
        TelegramSettings(enabled=True, bot_token="${TELEGRAM_BOT_TOKEN}", chat_id="1"),
    ],
)
def test_nothing_sent_when_not_configured(api, summary, settings: TelegramSettings) -> None:
    send_run_telegram(summary[0], settings, attachment=summary[1])
    assert api.calls() == []


def test_caption_respects_telegram_limit(tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path, failures=40, message="x" * 500)
    caption = build_caption(s)
    assert len(caption) <= 1024
    assert caption.startswith("❌ FAILED — 40 of 41 tests failed")


def test_caption_lists_at_most_five_failures(tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path, failures=8)
    caption = build_caption(s)
    assert caption.count("• ") == 5 and "…and 3 more" in caption


def test_passing_caption(tmp_path: Path) -> None:
    s, _ = make_summary(tmp_path, failures=0, passed=3)
    assert build_caption(s).splitlines()[0] == "✅ PASSED — 3/3 tests (3s)"
