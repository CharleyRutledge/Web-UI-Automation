from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui_automation.config import AiSettings
    from ui_automation.reporting.summary import RunSummary

_SYSTEM = (
    "You explain failed automated UI test runs to the person who owns the tests. "
    "They read your answer on a phone, inside a short report. "
    "Write plain text only: no Markdown, no asterisks, no headings, no bullet symbols other than '-'. "
    "Only state what the evidence shows; if the cause is unclear, say so instead of guessing. "
    "Do not comment on videos, traces, reruns or flakiness unless the failure text points to them."
)


def build_analysis_prompt(summary: RunSummary) -> str:
    lines = [
        "Test run facts:",
        f"- Site under test: {summary.base_url or 'unknown'}",
        f"- Result: {summary.passed} passed, {summary.failed} failed, "
        f"{summary.errors} errors, {summary.skipped} skipped",
        "- Browser tests use Playwright (pytest-playwright, Chromium). Tests under tests/unit/ "
        "are plain Python tests with no browser.",
        "",
        "All tests:",
    ]
    lines.extend(f"- {t.outcome.upper()}: {t.nodeid}" for t in summary.tests)
    lines.append("")
    lines.append("Failures (error message and end of the traceback):")
    for t in summary.problems:
        step = f"Stopped at step: {t.last_step}\n" if t.last_step else ""
        lines.append(f"\n=== {t.nodeid}\n{step}{t.message}\n{t.details}")
    lines.append(
        "\nFor each failing test write:\n"
        "<test name in plain words>\n"
        "What broke: one sentence in plain words.\n"
        "Likely cause: one sentence.\n"
        "Fix: one concrete step.\n\n"
        "Keep the whole answer under 150 words."
    )
    return "\n".join(lines)[:20000]


def analyze_run(summary: RunSummary, ai: AiSettings) -> str | None:
    # A green run needs no analysis: skip the call (and the cost) entirely.
    if summary.ok or not summary.problems:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not ai.enabled or not api_key:
        return None

    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    client = Anthropic(api_key=api_key)
    message = client.beta.messages.create(
        model=ai.model,
        max_tokens=ai.max_tokens,
        system=_SYSTEM,
        # If a safety classifier declines, re-run on a fallback model instead of returning nothing.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": build_analysis_prompt(summary)}],
    )
    if message.stop_reason == "refusal":
        return None
    parts = [block.text for block in message.content if block.type == "text"]
    return "\n".join(parts).strip() or None
