from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from ui_automation.config import TelegramSettings
    from ui_automation.reporting.summary import RunSummary


def _is_unresolved(value: str) -> bool:
    return "${" in value


def _api_base() -> str:
    # Overridable so the error paths (401, 429, 500, timeouts) can be tested against a local server.
    return os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


def _call(
    token: str,
    method: str,
    payload: dict[str, Any],
    files: dict[str, tuple[str, bytes, str]] | None = None,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """POST to the Bot API. The URL embeds the bot token, so it must never reach an error message."""
    url = f"{_api_base()}/bot{token}/{method}"
    try:
        if files:
            response = requests.post(url, data=payload, files=files, timeout=timeout)
        else:
            response = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"Telegram {method} request failed ({type(exc).__name__})") from None
    if not response.ok:
        raise RuntimeError(f"Telegram {method} returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise RuntimeError(f"Telegram {method} returned a non-JSON response") from None


_CAPTION_LIMIT = 1024  # Telegram's limit for a document caption


def build_caption(summary: RunSummary) -> str:
    lines = [summary.headline()]
    problems = summary.problems
    if problems:
        lines.append("")
        for test in problems[:5]:
            reason = (test.message or "no error message")[:160]
            where = f" at step \"{test.last_step}\"" if test.last_step else ""
            lines.append(f"• {test.title}{where}: {reason}")
        if len(problems) > 5:
            lines.append(f"…and {len(problems) - 5} more")
    scans = [scan for t in summary.tests for scan in t.accessibility]
    if scans:
        issues = sum(len(scan["violations"]) for scan in scans)
        lines.append("")
        lines.append(f"Accessibility: {issues} issue type(s) on {len(scans)} page(s)" if issues
                     else f"Accessibility: no issues found on {len(scans)} page(s)")
    checks = [r for t in summary.tests for entry in t.compliance for r in entry["results"]]
    if checks:
        failed = [r for r in checks if not r["passed"]]
        if not scans:
            lines.append("")
        lines.append(f"Website requirements: {len(failed)} problem(s): " + ", ".join(sorted({r['title'] for r in failed}))
                     if failed else "Website requirements: all checks passed")
    lines.append("")
    lines.append("Full details are in the attached report.")
    if summary.run_url:
        lines.append(f"CI run: {summary.run_url}")
    caption = "\n".join(lines)
    return caption if len(caption) <= _CAPTION_LIMIT else caption[: _CAPTION_LIMIT - 1] + "…"


def discover_chat_id(token: str, *, timeout: float = 60.0) -> str | None:
    """Find the chat of the most recent message sent to the bot (personal-bot convenience)."""
    data = _call(
        token, "getUpdates", {"limit": 100, "allowed_updates": ["message", "channel_post"]}, timeout=timeout
    )
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
    attachment: Path | None = None,
) -> None:
    # ai_summary is part of the attached report; the caption stays short on purpose.
    if not telegram.enabled or not telegram.bot_token:
        return
    if _is_unresolved(telegram.bot_token):
        print("Telegram notification skipped: bot_token not resolved (missing env var)")
        return

    chat_id = "" if _is_unresolved(telegram.chat_id) else telegram.chat_id
    if not chat_id:
        chat_id = discover_chat_id(telegram.bot_token, timeout=telegram.timeout_seconds) or ""
        if not chat_id:
            print(
                "Telegram notification skipped: no chat_id set and the bot has no messages yet "
                "- send your bot a message (e.g. 'hi') and re-run"
            )
            return
        # Telegram only keeps a bot's incoming messages for ~24h, so this lookup stops working later.
        print(
            f"Telegram: sending to chat {chat_id}, found from the bot's recent messages. "
            "Save this number as the TELEGRAM_CHAT_ID secret so future runs keep working."
        )

    caption = build_caption(summary)
    if attachment is not None and attachment.is_file():
        name = f"ui-tests-{'passed' if summary.ok else 'failed'}-{summary.run_dir.name if summary.run_dir else 'run'}.html"
        _call(
            telegram.bot_token,
            "sendDocument",
            {"chat_id": chat_id, "caption": caption},
            files={"document": (name, attachment.read_bytes(), "text/html")},
            timeout=telegram.timeout_seconds,
        )
    else:
        _call(
            telegram.bot_token, "sendMessage", {"chat_id": chat_id, "text": caption}, timeout=telegram.timeout_seconds
        )
