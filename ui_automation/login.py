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
        _submit(page, auth, password)  # type: ignore[arg-type]
        page.wait_for_timeout(500)
        _settle(page)
        if not _logged_in(page, auth, login_url):
            hint = ("check the username and password in .env" if not auth.logged_in_check
                    else f"auth.logged_in_check ({auth.logged_in_check!r}) was not found")
            raise LoginError(f"role '{role.name}': login did not succeed (still on {urlparse(page.url).path or '/'}); {hint}")
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
