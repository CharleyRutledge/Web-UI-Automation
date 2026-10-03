"""Automated accessibility checks with axe-core (Deque, MPL-2.0), run inside the real browser.

Standards (EU Web Accessibility Directive / European Accessibility Act -> EN 301 549 -> WCAG 2.1 AA):
  - "wcag21aa": WCAG 2.0 + 2.1, levels A and AA (what EN 301 549 requires today)
  - "wcag22aa": the above plus the WCAG 2.2 AA additions (current W3C recommendation)

Automated rules find a large share of real problems but cannot prove conformance: keyboard use,
meaningful alt text, reading order, captions and similar need a person (see MANUAL_CHECKS).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ui_automation.accessibility.fixes import suggest

AXE_VERSION = "4.13.0"
_AXE_SHA256 = "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1"
_AXE_PATH = Path(__file__).with_name("vendor") / "axe.min.js"

STANDARDS: dict[str, list[str]] = {
    "wcag21aa": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
    "wcag22aa": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
}
IMPACTS = ("minor", "moderate", "serious", "critical")

# Checked automatically by the site audit, beyond axe's rules (ui_automation/accessibility/beyond_axe.py).
AUTOMATED_CHECKS = (
    ("2.1.1 / 2.1.2", "Keyboard: everything clickable can be reached with Tab, and focus never gets stuck."),
    ("2.4.7", "Focus is visible: each element reached with Tab changes how it looks."),
    ("1.1.1", "Alt text is not empty talk such as \"image\" or a file name."),
    ("1.2.2", "Videos have a captions or subtitles track."),
    ("1.3.1", "Heading levels do not skip (h2 then h4)."),
    ("1.4.10 / 1.4.12", "Pages reflow at 320 px, and no text is cut off with increased text spacing."),
    ("2.2.1 / 2.2.2", "No automatic page refresh, and endless animation has a pause button."),
    ("2.5.8", "Buttons and links are big enough to tap on a phone."),
)

# What automated tools cannot decide (WCAG-EM step 4: these need a human evaluator).
MANUAL_CHECKS = (
    ("2.4.3", "Focus moves in an order that makes sense for the page."),
    ("1.1.1", "Alt text describes what each image shows or does."),
    ("1.2.2 / 1.2.5", "Captions are accurate, and videos have audio description where needed."),
    ("1.3.1 / 1.3.2", "Headings, lists and reading order match what the page looks like."),
    ("2.2.1", "Time limits (e.g. a session that logs out) can be turned off or extended."),
    ("3.3.1 / 3.3.3", "Form errors are described in text, with suggestions to fix them."),
    ("4.1.3", "Status messages are announced by screen readers without moving focus."),
)


def axe_source() -> str:
    data = _AXE_PATH.read_bytes()
    # Git on Windows may have turned the file's LF line endings into CRLF on checkout (older clones,
    # before .gitattributes). That changes no code, so the checksum is taken over LF endings.
    data = data.replace(b"\r\n", b"\n")
    if hashlib.sha256(data).hexdigest() != _AXE_SHA256:
        raise RuntimeError(f"{_AXE_PATH} does not match axe-core {AXE_VERSION}; refusing to inject it")
    return data.decode("utf-8")


def _criteria(tags: list[str]) -> list[str]:
    """axe tags like 'wcag143' / 'wcag1410' -> WCAG success criteria '1.4.3' / '1.4.10'."""
    out = []
    for tag in tags:
        m = re.fullmatch(r"wcag(\d)(\d)(\d{1,2})", tag)
        if m:
            out.append(".".join(m.groups()))
    return out


@dataclass
class Violation:
    rule: str
    impact: str
    help: str
    help_url: str
    criteria: list[str]
    targets: list[str] = field(default_factory=list)
    count: int = 0
    fixes: list[dict] = field(default_factory=list)  # per element: target, html, fix (copy-pasteable), note

    @classmethod
    def from_axe(cls, raw: dict[str, Any]) -> Violation:
        nodes = raw.get("nodes") or []
        return cls(
            rule=raw.get("id", ""),
            impact=raw.get("impact") or "minor",
            help=raw.get("help", ""),
            help_url=raw.get("helpUrl", ""),
            criteria=_criteria(raw.get("tags") or []),
            targets=[" ".join(map(str, n.get("target", []))) for n in nodes[:5]],
            count=len(nodes),
            fixes=[suggest(raw.get("id", ""), n) for n in nodes[:5]],
        )


def scan(page: Any, standard: str = "wcag21aa") -> list[Violation]:
    """Run axe on the page as it is now and return its violations (worst first)."""
    if standard not in STANDARDS:
        raise ValueError(f"accessibility standard must be one of {sorted(STANDARDS)}")
    if not page.evaluate("() => typeof window.axe !== 'undefined'"):
        # Evaluated through the DevTools protocol rather than a <script> tag, which a site's
        # Content-Security-Policy would block (the scan must also work on well-secured sites).
        page.evaluate(axe_source() + "\n;void 0")
    result = page.evaluate(
        "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        STANDARDS[standard],
    )
    violations = [Violation.from_axe(v) for v in result.get("violations", [])]
    violations.sort(key=lambda v: (-IMPACTS.index(v.impact) if v.impact in IMPACTS else 0, v.rule))
    return violations


def scan_rules(page: Any, rules: list[str]) -> list[Violation]:
    """Run only these axe rules (e.g. ["target-size"] on a phone), whatever standard they belong to."""
    if not page.evaluate("() => typeof window.axe !== 'undefined'"):
        page.evaluate(axe_source() + "\n;void 0")
    result = page.evaluate(
        "rules => axe.run(document, {runOnly: {type: 'rule', values: rules}, resultTypes: ['violations']})", rules)
    return [Violation.from_axe(v) for v in result.get("violations", [])]


def at_or_above(violations: list[Violation], threshold: str) -> list[Violation]:
    level = IMPACTS.index(threshold)
    return [v for v in violations if v.impact in IMPACTS and IMPACTS.index(v.impact) >= level]


def describe(violations: list[Violation]) -> str:
    lines = [f"{len(violations)} accessibility issue(s):"]
    for v in violations:
        crit = f" (WCAG {', '.join(v.criteria)})" if v.criteria else ""
        where = f" e.g. {v.targets[0]}" if v.targets else ""
        lines.append(f"- [{v.impact}] {v.help}{crit}: {v.count} element(s){where}")
    return "\n".join(lines)
