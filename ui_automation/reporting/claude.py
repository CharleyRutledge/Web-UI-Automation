from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui_automation.config import AiSettings
    from ui_automation.reporting.summary import RunSummary


def build_analysis_prompt(summary: RunSummary) -> str:
    lines = [
        "You are a senior QA engineer reviewing a Playwright pytest run.",
        f"Status: {summary.short_status()}",
        f"Exit code: {summary.exit_status}",
        f"Videos saved: {len(summary.video_files)}",
        f"Traces saved: {len(summary.trace_files)}",
    ]
    if summary.report_html:
        lines.append(f"HTML report: {summary.report_html}")
    if summary.failure_details:
        lines.append("\nFailures:\n" + "\n\n".join(summary.failure_details)[:12000])
    lines.append(
        "\nProvide: (1) one-line verdict, (2) likely root causes if failed, "
        "(3) top 3 next debugging steps using Playwright best practices."
    )
    return "\n".join(lines)


def analyze_run(summary: RunSummary, ai: AiSettings) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not ai.enabled or not api_key:
        return None

    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    client = Anthropic(api_key=api_key)
    message = client.messages.create(
        model=ai.model,
        max_tokens=ai.max_tokens,
        messages=[
            {
                "role": "user",
                "content": build_analysis_prompt(summary),
            }
        ],
    )
    parts = []
    for block in message.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip() or None
