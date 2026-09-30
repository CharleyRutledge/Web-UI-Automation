from __future__ import annotations

from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from ui_automation.config import TelegramSettings
    from ui_automation.reporting.summary import RunSummary


def _is_unresolved(value: str) -> bool:
    return "${" in value


def send_run_telegram(
    summary: RunSummary,
    telegram: TelegramSettings,
    *,
    ai_summary: str | None = None,
) -> None:
    if not telegram.enabled or not telegram.bot_token or not telegram.chat_id:
        return
    if _is_unresolved(telegram.bot_token) or _is_unresolved(telegram.chat_id):
        print("Telegram notification skipped: bot_token/chat_id not resolved (missing env vars)")
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
    url = f"https://api.telegram.org/bot{telegram.bot_token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": telegram.chat_id, "text": text},
            timeout=30,
        )
    except requests.RequestException as exc:
        # The request URL embeds the bot token; never let it reach logs via the exception text.
        raise RuntimeError(f"Telegram request failed ({type(exc).__name__})") from None
    if not response.ok:
        raise RuntimeError(f"Telegram API returned HTTP {response.status_code}")
