from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from ui_automation.config import TelegramSettings
    from ui_automation.reporting.summary import RunSummary


def _is_unresolved(value: str) -> bool:
    return "${" in value


def _call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the Bot API. The URL embeds the bot token, so it must never reach an error message."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"Telegram {method} request failed ({type(exc).__name__})") from None
    if not response.ok:
        raise RuntimeError(f"Telegram {method} returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise RuntimeError(f"Telegram {method} returned a non-JSON response") from None


def discover_chat_id(token: str) -> str | None:
    """Find the chat of the most recent message sent to the bot (personal-bot convenience)."""
    data = _call(token, "getUpdates", {"limit": 100, "allowed_updates": ["message", "channel_post"]})
    for update in reversed(data.get("result") or []):
        message = update.get("message") or update.get("channel_post") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is not None:
            return str(chat_id)
    return None


def send_run_telegram(
    summary: RunSummary,
    telegram: TelegramSettings,
    *,
    ai_summary: str | None = None,
) -> None:
    if not telegram.enabled or not telegram.bot_token:
        return
    if _is_unresolved(telegram.bot_token):
        print("Telegram notification skipped: bot_token not resolved (missing env var)")
        return

    chat_id = "" if _is_unresolved(telegram.chat_id) else telegram.chat_id
    if not chat_id:
        chat_id = discover_chat_id(telegram.bot_token) or ""
        if not chat_id:
            print(
                "Telegram notification skipped: no chat_id set and the bot has no messages yet "
                "- send your bot a message (e.g. 'hi') and re-run"
            )
            return

    lines = [
        f"UI Automation — {summary.short_status()}",
        f"Report: {summary.report_html}" if summary.report_html else "",
        f"Videos: {len(summary.video_files)}",
    ]
    if ai_summary:
        clipped = ai_summary if len(ai_summary) < 3500 else ai_summary[:3500] + "…"
        lines.extend(["", "Claude analysis:", clipped])

    text = "\n".join(line for line in lines if line)
    _call(telegram.bot_token, "sendMessage", {"chat_id": chat_id, "text": text})
