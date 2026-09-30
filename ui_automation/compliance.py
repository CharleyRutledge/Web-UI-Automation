"""Website compliance checks for Irish (and wider EU) requirements, run in the real browser.

These find what is missing or misbehaving (no privacy notice link, tracking before consent, ...).
They cannot judge whether a policy's wording is legally adequate: that needs a person, and legal advice.

Legal basis per check (as commonly applied in Ireland):
  privacy_notice          GDPR Art. 13/14; Data Protection Act 2018
  cookie_consent          ePrivacy Regulations 2011 (S.I. 336/2011) Reg. 5; DPC cookie guidance
  accessibility_statement EU Web Accessibility Directive (S.I. 358/2020); European Accessibility Act (S.I. 636/2023)
  company_details         Companies Act 2014 s.151; E-Commerce Regulations 2003 (S.I. 68/2003) Reg. 7
  contact_details         E-Commerce Regulations 2003 (S.I. 68/2003) Reg. 7
  terms                   Consumer Rights Act 2022 (for sites that sell)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

LAW = {
    "privacy_notice": "GDPR Art. 13/14; Data Protection Act 2018",
    "cookie_consent": "ePrivacy Regulations 2011 (S.I. 336/2011) Reg. 5; DPC cookie guidance",
    "accessibility_statement": "EU Web Accessibility Directive (S.I. 358/2020); European Accessibility Act",
    "company_details": "Companies Act 2014 s.151; E-Commerce Regulations 2003 (S.I. 68/2003)",
    "contact_details": "E-Commerce Regulations 2003 (S.I. 68/2003) Reg. 7",
    "terms": "Consumer Rights Act 2022",
}
ALL_CHECKS = tuple(LAW)
TITLES = {
    "privacy_notice": "Privacy notice",
    "cookie_consent": "Cookie consent",
    "accessibility_statement": "Accessibility statement",
    "company_details": "Company details",
    "contact_details": "Contact details",
    "terms": "Terms and conditions",
}

# Cookies and hosts that mean analytics / advertising, which need consent before they are set or contacted.
TRACKING_COOKIE = re.compile(
    r"^(_ga|_gid|_gat|_gcl_|_fbp|_fbc|fr$|IDE$|DSID$|NID$|__utm|_hj|_clck|_clsk|_uetsid|_uetvid|_pin_|"
    r"_tt_|_ttp|li_|bcookie|muc_ads|_scid|ajs_)"
)
TRACKER_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net", "googleadservices.com",
    "connect.facebook.net", "facebook.com/tr", "hotjar.com", "clarity.ms", "bat.bing.com",
    "snap.licdn.com", "px.ads.linkedin.com", "analytics.tiktok.com", "static.ads-twitter.com",
)
REJECT_WORDS = re.compile(r"\b(reject|decline|refuse|deny|necessary only|only necessary|essential only|"
                          r"only essential|manage|settings|preferences|customi[sz]e)\b", re.I)
ACCEPT_WORDS = re.compile(r"\b(accept|agree|allow all|ok|got it)\b", re.I)


@dataclass
class CheckResult:
    check: str
    passed: bool
    detail: str
    law: str = ""
    title: str = ""

    def __post_init__(self) -> None:
        self.law = self.law or LAW.get(self.check, "")
        self.title = self.title or TITLES.get(self.check, self.check)


class Monitor:
    """Watches requests from the first byte, and blocks tracker hosts so tests never send analytics."""

    def __init__(self, page: Any) -> None:
        self.tracker_requests: list[str] = []
        page.route(lambda url: any(h in url for h in TRACKER_HOSTS), self._block)

    def _block(self, route: Any) -> None:
        self.tracker_requests.append(route.request.url)
        route.abort()


def _links(page: Any) -> list[dict[str, str]]:
    return page.eval_on_selector_all(
        "a[href]", "els => els.map(a => ({text: (a.innerText || a.getAttribute('aria-label') || '').trim(), href: a.href}))"
    )


def _find_link(links: list[dict[str, str]], pattern: str) -> dict[str, str] | None:
    rx = re.compile(pattern, re.I)
    return next((l for l in links if rx.search(l["text"]) or rx.search(urlparse(l["href"]).path)), None)


def _reachable(page: Any, url: str) -> bool:
    try:
        return page.request.get(url, timeout=15000).status < 400
    except Exception:  # noqa: BLE001 - any network problem means "not reachable"
        return False


def _link_check(page: Any, links: list[dict[str, str]], check: str, pattern: str, what: str) -> CheckResult:
    link = _find_link(links, pattern)
    if not link:
        return CheckResult(check, False, f"No link to a {what} on this page.")
    if not _reachable(page, link["href"]):
        return CheckResult(check, False, f"The {what} link ({link['href']}) does not load.")
    return CheckResult(check, True, f"Linked as \"{link['text'] or link['href']}\" and loads.")


def check_page(page: Any, monitor: Monitor, checks: tuple[str, ...] = ALL_CHECKS) -> list[CheckResult]:
    """Run the checks on the page as first loaded (no clicks yet: cookie consent must not be given)."""
    results: list[CheckResult] = []
    links = _links(page)
    text = page.inner_text("body")
    page_host = urlparse(page.url).hostname or ""

    if "cookie_consent" in checks:
        cookies = page.context.cookies()
        tracking = sorted({c["name"] for c in cookies if TRACKING_COOKIE.match(c["name"])})
        third_party = sorted({f"{c['name']} ({c['domain'].lstrip('.')})" for c in cookies
                              if page_host and not (page_host == c["domain"].lstrip(".")
                                                    or page_host.endswith("." + c["domain"].lstrip(".")))})
        problems = []
        if tracking:
            problems.append(f"tracking cookies set before consent: {', '.join(tracking)}")
        if third_party:
            problems.append(f"third-party cookies before consent: {', '.join(third_party)}")
        if monitor.tracker_requests:
            hosts = sorted({urlparse(u).hostname for u in monitor.tracker_requests})
            problems.append(f"contacted trackers before consent: {', '.join(hosts)}")
        buttons = [b.strip() for b in page.eval_on_selector_all(
            "button, [role=button], a", "els => els.map(e => e.innerText || e.getAttribute('aria-label') || '')")]
        has_accept = any(ACCEPT_WORDS.search(b) for b in buttons if b)
        has_reject = any(REJECT_WORDS.search(b) for b in buttons if b)
        if has_accept and not has_reject:
            problems.append("the cookie banner offers 'accept' without an equally easy 'reject'")
        results.append(CheckResult("cookie_consent", not problems, "; ".join(problems).capitalize() + "."
                                   if problems else "No tracking before consent."))

    if "privacy_notice" in checks:
        results.append(_link_check(page, links, "privacy_notice", r"privacy|data protection|gdpr", "privacy notice"))
    if "accessibility_statement" in checks:
        results.append(_link_check(page, links, "accessibility_statement", r"accessibility", "accessibility statement"))
    if "terms" in checks:
        results.append(_link_check(page, links, "terms", r"terms|conditions", "terms and conditions page"))

    if "company_details" in checks:
        missing = []
        if not re.search(r"(company|registration|registered|CRO)\s*(reg(istration)?\.?\s*)?(no\.?|number|#)?\s*[:.]?\s*\d{5,6}\b",
                         text, re.I):
            missing.append("company registration number")
        if not re.search(r"registered\s+(office|address)", text, re.I):
            missing.append("registered office address")
        vat = re.search(r"\bIE\s?\d{7}[A-W][A-I]?\b|\bIE\s?\d[A-Z+*]\d{5}[A-W]\b", text)
        detail = "Registration number and registered office shown." if not missing else \
            f"Not found on this page: {', '.join(missing)}."
        if vat:
            detail += f" VAT number {vat.group(0)} shown."
        results.append(CheckResult("company_details", not missing, detail))

    if "contact_details" in checks:
        email = any(l["href"].startswith("mailto:") for l in links) or re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        form = _find_link(links, r"contact")
        ok = bool(email or form)
        results.append(CheckResult("contact_details", ok, "Email or contact page available." if ok else
                                   "No email address or contact page link found."))
    return results


def as_dicts(results: list[CheckResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
