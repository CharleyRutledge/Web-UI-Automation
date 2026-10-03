"""Checking the app's API with the calls its own pages made while crawling (site_audit.conftest.ApiCall).

Only GET requests are ever replayed: nothing that could create, change or delete data. Login tokens and cookies
are used in memory only and never appear in a report.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError, Playwright

from site_audit.conftest import SKIP_LINKS, ApiCall, SiteMap
from ui_automation.config import Settings, accepts_self_signed


def path(url: str) -> str:
    """What reports show of an address: host (when not the site's own) and path, never the query string."""
    u = urlparse(url)
    return u.path or "/"


def where(call: ApiCall, home: str) -> str:
    u, h = urlparse(call.url), urlparse(home)
    return (u.path or "/") if u.netloc == h.netloc else f"{u.netloc}{u.path or '/'}"


def replayable(site: SiteMap) -> list[ApiCall]:
    """The successful read requests of a role, once each (never anything that changes data)."""
    seen: set[str] = set()
    out: list[ApiCall] = []
    for call in site.api:
        key = call.url
        if call.method != "GET" or not call.body_hash or key in seen or SKIP_LINKS.search(path(call.url)):
            continue
        seen.add(key)
        out.append(call)
    return out[:60]


@dataclass
class Replay:
    status: int
    same_data: bool
    error: str = ""


def replay(playwright: Playwright, settings: Settings, call: ApiCall, *, as_site: SiteMap | None) -> Replay:
    """GET `call` again: logged out (as_site=None, no cookies, no token) or as another role (its session and token)."""
    headers = {}
    if as_site is not None:
        token = next((c.auth for c in as_site.api if c.auth and urlparse(c.url).netloc == urlparse(call.url).netloc), "")
        if token:
            headers["authorization"] = token
    context = playwright.request.new_context(
        ignore_https_errors=accepts_self_signed(settings),
        storage_state=as_site.state if as_site is not None else None,
        extra_http_headers=headers or None,
    )
    try:
        r = context.get(call.url, max_redirects=0, fail_on_status_code=False, timeout=min(settings.timeout_ms, 20_000))
        same = r.ok and hashlib.sha256(r.body()).hexdigest() == call.body_hash
        return Replay(r.status, same)
    except PlaywrightError as exc:
        return Replay(0, False, exc.message.splitlines()[0][:120])
    finally:
        context.dispose()


def is_public(call: ApiCall, settings: Settings, public: SiteMap | None) -> bool:
    """Meant to work without logging in: listed in audit.public_api, or called by the logged-out visitor."""
    p = path(call.url)
    if any(p == x or p.startswith(x.rstrip("/") + "/") or re.fullmatch(x.replace("*", ".*"), p)
           for x in settings.audit.public_api):
        return True
    return public is not None and any(path(c.url) == p for c in public.api)
