"""Logging in as a role (settings: auth.roles) for the site audit.

The login runs in its own browser context that is never recorded: no video, no trace and no screenshot can
contain a password. Only the resulting session (cookies and local storage) is handed to the audit.
Credentials come from environment variables or the git-ignored .env file, never from the settings file.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Browser, Error as PlaywrightError, Locator, Page

from ui_automation.config import AuthSettings, RoleSettings, Settings, accepts_self_signed

USERNAME_SELECTORS = (
    "input[autocomplete='username']", "input[type='email']",
    "input[name*='email' i]", "input[name*='user' i]", "input[name*='login' i]",
    "input[id*='email' i]", "input[id*='user' i]", "input[id*='login' i]",
    "input[type='text']",
)
SUBMIT_NAMES = re.compile(r"^\s*(log\s*-?\s*in|sign\s*-?\s*in|continue|next|submit|enter)\b", re.I)
# Where login forms show why a login failed.
MESSAGE_SELECTORS = ("[role='alert']", "[aria-live]", "[role='status']", ".error", ".alert", "[class*='error' i]",
                     "[class*='toast' i]", "[data-sonner-toast]")
ERROR_WORDS = re.compile(r"invalid|incorrect|wrong|not (found|recogni[sz]ed|verified|confirmed)|failed|error|locked|"
                         r"too many|try again|disabled|denied|unauthori[sz]ed|verify|confirm your", re.I)
LOGIN_WAIT_MS = 15_000  # a login can take a while: password hashing, then a redirect
FIRST_TRY_WAIT_MS = 8_000  # when the first try changed nothing at all, try again sooner


class LoginError(Exception):
    """Logging in as a role did not work; the message says why (never containing the password)."""


def _env_name(value: str) -> str:
    m = re.search(r"\$\{([^}]+)\}", value)
    return m.group(1) if m else ""


def check_credentials(role: RoleSettings) -> None:
    for what, value in (("username", role.username), ("password", role.password)):
        if not value:
            raise LoginError(f"role '{role.name}': no {what} in the settings (auth.roles.{role.name}.{what})")
        if "${" in value:
            raise LoginError(f"role '{role.name}': the {what} comes from {_env_name(value)}, which is not set. "
                             f"Add {_env_name(value)}=... to the .env file (or your environment) and run again.")


def _first_visible(page: Page, selectors: tuple[str, ...] | list[str]) -> Locator | None:
    for selector in selectors:
        loc = page.locator(selector)
        try:
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except PlaywrightError:
            continue
    return None


def _password_box(page: Page, auth: AuthSettings) -> Locator | None:
    return _first_visible(page, [auth.password_field] if auth.password_field else ["input[type='password']"])


def _submit(page: Page, auth: AuthSettings, near: Locator) -> None:
    if auth.submit:
        page.locator(auth.submit).first.click()
        return
    form_buttons = near.locator("xpath=ancestor::form[1]").locator("button, input[type='submit']")
    try:
        if form_buttons.count():
            for i in range(form_buttons.count()):
                b = form_buttons.nth(i)
                if b.is_visible() and (SUBMIT_NAMES.search(b.inner_text() or b.get_attribute("value") or "")
                                       or (b.get_attribute("type") or "").lower() == "submit"):
                    b.click()
                    return
    except PlaywrightError:
        pass
    named = page.get_by_role("button", name=SUBMIT_NAMES)
    if named.count() and named.first.is_visible():
        named.first.click()
        return
    near.press("Enter")


def _settle(page: Page, timeout_ms: int = 10_000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightError:
        pass


def _logged_in(page: Page, auth: AuthSettings, login_url: str) -> bool:
    check = auth.logged_in_check
    if check:
        if check.startswith("/") or "://" in check:
            return check in page.url
        if re.match(r"^[#.\[]|^[a-z]+[#.\[]", check):  # looks like a CSS selector
            return page.locator(check).first.is_visible()
        return page.get_by_text(check, exact=False).first.is_visible()
    # Automatic: the password box has gone (a failed login shows the form again, usually with an error).
    return _password_box(page, auth) is None


def _page_message(page: Page, secrets: tuple[str, ...]) -> str:
    """What the login page says about the failure (an error box or alert), with the credentials removed."""
    texts: list[str] = []
    for selector in MESSAGE_SELECTORS:
        loc = page.locator(selector)
        try:
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    texts.append(loc.nth(i).inner_text(timeout=1_000))
        except PlaywrightError:
            continue
    if not any(t.strip() for t in texts):
        try:
            texts.append(page.get_by_text(ERROR_WORDS).first.inner_text(timeout=1_000))
        except PlaywrightError:
            pass
    message = ""
    for text in texts:
        text = " ".join(text.split())
        if text and text not in message:
            message = f"{message} / {text}" if message else text
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return message[:200] + ("..." if len(message) > 200 else "")


def _wait_until_logged_in(page: Page, auth: AuthSettings, login_url: str, timeout_ms: int) -> bool:
    """Wait for the logged-in sign; stop early when the page shows an error (like "wrong password") instead."""
    page.wait_for_timeout(500)
    _settle(page)
    waited = 0
    while True:
        try:
            if _logged_in(page, auth, login_url):
                return True
        except PlaywrightError:
            pass  # the page is navigating: look again
        if waited >= timeout_ms or ERROR_WORDS.search(_page_message(page, ())):
            return False
        page.wait_for_timeout(250)
        waited += 250


class _Evidence:
    """What the page did after the form was sent: requests and their answers, and browser errors.

    Only methods, addresses (without query strings, which can hold form values) and status codes are kept,
    plus error messages with the username and password removed. Never request or response bodies.
    """

    def __init__(self, page: Page, base_url: str, secrets: tuple[str, ...]) -> None:
        self.base_host = urlparse(base_url).netloc
        self.secrets = secrets
        self.recording = False
        self.events: list[str] = []
        page.on("response", self._response)
        page.on("requestfailed", self._failed)
        page.on("console", self._console)
        page.on("pageerror", lambda exc: self._add(f"page error: {exc}"))

    def _where(self, url: str) -> str:
        u = urlparse(url)
        return (u.path or "/") if u.netloc == self.base_host else f"{u.netloc}{u.path or '/'}"

    def _add(self, text: str) -> None:
        if not self.recording or len(self.events) >= 12:
            return
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "***")
        text = " ".join(text.split())[:160]
        if text not in self.events:
            self.events.append(text)

    def _response(self, response: Any) -> None:
        request = response.request
        if request.resource_type in ("document", "fetch", "xhr"):
            self._add(f"{request.method} {self._where(request.url)} -> {response.status}")

    def _failed(self, request: Any) -> None:
        if request.resource_type in ("document", "fetch", "xhr"):
            self._add(f"{request.method} {self._where(request.url)} -> no answer ({request.failure or 'failed'})")

    def _console(self, message: Any) -> None:
        if message.type == "error":
            self._add(f"browser console error: {message.text}")

    def summary(self) -> str:
        if not self.events:
            return "after pressing the button the page sent no request at all"
        return "after pressing the button: " + "; ".join(self.events)


def _on_login_page(page: Page, login_url: str) -> bool:
    return urlparse(page.url).path.rstrip("/") == urlparse(login_url).path.rstrip("/")


def _fill_and_submit(page: Page, auth: AuthSettings, role: RoleSettings, login_url: str,
                     evidence: _Evidence) -> None:
    user_box = (_first_visible(page, [auth.username_field]) if auth.username_field
                else _first_visible(page, USERNAME_SELECTORS))
    password = _password_box(page, auth)
    if user_box is None and password is None:
        raise LoginError(f"role '{role.name}': no username or password box found on {login_url}. "
                         "Set auth.username_field / auth.password_field to their CSS selectors.")
    if user_box is not None:
        user_box.fill(role.username)
    if password is None:  # two-step login: the password is asked for on the next screen
        _submit(page, auth, user_box)  # type: ignore[arg-type]
        try:
            page.locator(auth.password_field or "input[type='password']").first.wait_for(
                state="visible", timeout=15_000)
        except PlaywrightError:
            raise LoginError(f"role '{role.name}': after entering the username, no password box appeared") from None
        password = _password_box(page, auth)
    password.fill(role.password)  # type: ignore[union-attr]
    # An app that is still starting up (hydrating) can empty the boxes again: fill them once more if so.
    page.wait_for_timeout(300)
    for box, value in ((user_box, role.username), (password, role.password)):
        try:  # (in a two-step login the username box is on the previous screen: nothing to check)
            if box is not None and box.is_visible() and box.input_value(timeout=2_000) != value:
                box.fill(value)
        except PlaywrightError:
            pass
    evidence.events.clear()
    evidence.recording = True
    _submit(page, auth, password)  # type: ignore[arg-type]


def log_in(browser: Browser, settings: Settings, role: RoleSettings) -> dict[str, Any]:
    """Log in as `role` and return the session (Playwright storage state). Raises LoginError."""
    check_credentials(role)
    auth = settings.auth
    login_url = urljoin(settings.base_url.rstrip("/") + "/", auth.login_url.lstrip("/"))
    # No record_video_dir, no tracing: nothing from this context is ever written to disk.
    context = browser.new_context(ignore_https_errors=accepts_self_signed(settings))
    context.set_default_timeout(min(settings.timeout_ms, 20_000))
    page = context.new_page()
    evidence = _Evidence(page, settings.base_url, (role.password, role.username))
    try:
        try:
            page.goto(login_url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise LoginError(f"role '{role.name}': the login page {login_url} did not open "
                             f"({exc.message.splitlines()[0]})") from None
        _settle(page, 5_000)
        _fill_and_submit(page, auth, role, login_url, evidence)
        logged_in = _wait_until_logged_in(page, auth, login_url, FIRST_TRY_WAIT_MS)
        if not logged_in and _on_login_page(page, login_url) and not _page_message(page, ()):
            # Nothing visible happened. Some apps show the form before the code behind the button has loaded,
            # so a click that comes too early does nothing: try once more on a settled page. The evidence of
            # both tries is kept, so the report shows what really happened.
            first_try = evidence.summary()
            evidence.recording = False
            page.goto(login_url, wait_until="domcontentloaded")
            _settle(page, 10_000)
            page.wait_for_timeout(2_000)
            _fill_and_submit(page, auth, role, login_url, evidence)
            logged_in = _wait_until_logged_in(page, auth, login_url, LOGIN_WAIT_MS)
        else:
            first_try = ""
        if not logged_in:
            evidence.recording = False
            said = _page_message(page, (role.password, role.username))
            where = urlparse(page.url).path or "/"
            left_login = not _on_login_page(page, login_url)
            if said:
                reason = f"the page said: \"{said}\""
            elif left_login and auth.logged_in_check:
                reason = "the login may have worked: check that auth.logged_in_check matches the page after logging in"
            else:
                reason = "the page showed no error message: try the username and password from .env by hand"
            check = f" (auth.logged_in_check {auth.logged_in_check!r} was not found)" if auth.logged_in_check else ""
            what = evidence.summary() + (f" (first try: {first_try[0].lower() + first_try[1:]})"
                                         if first_try and first_try != evidence.summary() else "")
            raise LoginError(f"role '{role.name}': login did not succeed, now on {where}{check}; {reason}. "
                             f"Evidence: {what}")
        return context.storage_state()
    finally:
        context.close()


def is_refused(status: int | None, final_url: str, page_has_password_box: bool, login_url: str) -> bool:
    """A protected page counts as refused when the server says no, or it sends the user to log in."""
    if status in (401, 403, 404):
        return True
    if urlparse(final_url).path.rstrip("/") == urlparse(login_url).path.rstrip("/"):
        return True
    return page_has_password_box
