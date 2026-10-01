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


def log_in(browser: Browser, settings: Settings, role: RoleSettings) -> dict[str, Any]:
    """Log in as `role` and return the session (Playwright storage state). Raises LoginError."""
    check_credentials(role)
    auth = settings.auth
    login_url = urljoin(settings.base_url.rstrip("/") + "/", auth.login_url.lstrip("/"))
    # No record_video_dir, no tracing: nothing from this context is ever written to disk.
    context = browser.new_context(ignore_https_errors=accepts_self_signed(settings))
    context.set_default_timeout(min(settings.timeout_ms, 20_000))
    page = context.new_page()
    try:
        try:
            page.goto(login_url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise LoginError(f"role '{role.name}': the login page {login_url} did not open "
                             f"({exc.message.splitlines()[0]})") from None
        _settle(page, 5_000)
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
        _submit(page, auth, password)  # type: ignore[arg-type]
        if not _wait_until_logged_in(page, auth, login_url, LOGIN_WAIT_MS):
            said = _page_message(page, (role.password, role.username))
            where = urlparse(page.url).path or "/"
            reason = (f"the page said: \"{said}\"" if said else
                      "the page showed no error message, so check the username and password in .env "
                      "by signing in by hand")
            check = (f" (auth.logged_in_check {auth.logged_in_check!r} was not found)"
                     if auth.logged_in_check else "")
            raise LoginError(f"role '{role.name}': login did not succeed, still on {where}{check}; {reason}")
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
