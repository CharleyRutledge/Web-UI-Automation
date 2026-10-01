"""Recognise when a website refuses to serve an automated browser (rate limits, anti-bot challenges).

Shared CI runners are often rate-limited or challenged. That says nothing about the site or the tests,
so suites report it as "not tested" with the reason, never as a pass or a failure. Ordinary errors
(403, 404, 500, ...) are not blocks.
"""

from __future__ import annotations

from typing import Any


def blocked_reason(response: Any, page: Any) -> str:
    """Why the site refused to serve this browser, or '' when it did not."""
    final = page.url
    if "google.com/sorry" in final:
        return f"Google's 'unusual traffic' check blocked this runner ({final.split('?')[0]})"
    if response is not None and response.status == 429:
        return f"the site rate-limited this runner (HTTP 429 at {response.url.split('?')[0]})"
    if response is not None and response.status in (403, 503):
        title = page.title().lower()
        if "just a moment" in title or "attention required" in title or "captcha" in title:
            return f"an anti-bot challenge blocked this runner (HTTP {response.status}, page '{page.title()}')"
    return ""
