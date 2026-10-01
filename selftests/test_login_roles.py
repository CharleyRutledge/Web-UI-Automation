"""Logging in for the site audit: roles, permission checks, two-step logins, and passwords never leaking.

The app below has real sessions (a cookie), an admin-only page, and a /logout link that ends the session
on the server: if the crawler ever followed it, the rest of that role's pages would fail.
"""

from __future__ import annotations

import secrets
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs

import pytest

from harness import CliRun, base_config, invoke_cli

PASSWORDS = {"admin": "Adm1n-SENTINEL-pw", "viewer": "V1ewer-SENTINEL-pw"}


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>'
            f"<body><main><h1>{title}</h1>{body}</main></body></html>")


class App:
    """A small app with sessions and two roles."""

    def __init__(self, *, reports_allowed_for_viewer: bool = True) -> None:
        self.sessions: dict[str, str] = {}
        self.requests: list[str] = []
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _user(self) -> str | None:
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "sid":
                        return app.sessions.get(v)
                return None

            def _send(self, code: int, html: str = "", headers: dict | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "text/html")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(html.encode())

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                app.requests.append(path)
                user = self._user()
                if path == "/":
                    return self._send(200, _page("Home", '<a href="/login">Log in</a> <a href="/about">About</a>'))
                if path == "/about":
                    return self._send(200, _page("About", '<a href="/">Home</a>'))
                if path == "/login":
                    return self._send(200, _page("Log in", '<form method="post" action="/login">'
                                      '<label>Email <input type="email" name="email"></label>'
                                      '<label>Password <input type="password" name="password"></label>'
                                      '<button type="submit">Log in</button></form>'))
                if path == "/login2":  # two-step: email first
                    return self._send(200, _page("Sign in", '<form method="post" action="/login2">'
                                      '<label>Email <input type="email" name="email"></label>'
                                      '<button type="submit">Next</button></form>'))
                if path == "/login-late":  # the button does nothing until the page's script has started
                    return self._send(200, _page("Sign in", '<form method="post" action="/login" id="f">'
                                      '<label>Email <input type="email" name="email"></label>'
                                      '<label>Password <input type="password" name="password"></label>'
                                      '<button type="button" id="go">Sign in</button></form>'
                                      "<script>setTimeout(function () { document.getElementById('go')"
                                      ".onclick = function () { document.getElementById('f').submit(); }; }, 1500);"
                                      "</script>"))
                if user is None:
                    return self._send(302, headers={"Location": "/login"})
                if path == "/logout":
                    app.sessions = {k: v for k, v in app.sessions.items() if v != user}
                    return self._send(302, headers={"Location": "/"})
                nav = ('<a href="/dashboard">Dashboard</a> <a href="/reports">Reports</a> '
                       + ('<a href="/admin">Admin</a> ' if user == "admin" else "")
                       + '<a href="/logout">Log out</a> <a href="/items/7/delete">Delete item</a>')
                if path == "/dashboard":
                    return self._send(200, _page(f"Dashboard for {user}", nav))
                if path == "/reports":
                    if user == "viewer" and not app.reports_allowed_for_viewer:
                        return self._send(403, _page("Forbidden", ""))
                    return self._send(200, _page("Reports", nav))
                if path == "/admin":
                    if user != "admin":
                        return self._send(403, _page("Forbidden", ""))
                    return self._send(200, _page("Admin", nav + ' <a href="/admin/users">Users</a>'))
                if path == "/admin/users":
                    return self._send(200 if user == "admin" else 403, _page("Users", nav))
                return self._send(404, _page("Not found", ""))

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                app.requests.append("POST " + path)
                form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
                email = (form.get("email") or [""])[0]
                user = email.split("@")[0]
                if path == "/login2" and "password" not in form:
                    return self._send(200, _page("Password", '<form method="post" action="/login2">'
                                      f'<input type="hidden" name="email" value="{email}">'
                                      '<label>Password <input type="password" name="password"></label>'
                                      '<button type="submit">Sign in</button></form>'))
                if PASSWORDS.get(user) == (form.get("password") or [""])[0]:
                    sid = secrets.token_hex(8)
                    app.sessions[sid] = user
                    return self._send(302, headers={"Location": "/dashboard", "Set-Cookie": f"sid={sid}; Path=/"})
                return self._send(200, _page("Log in", '<p role="alert">Wrong email or password</p>'
                                  '<form method="post"><input type="email" name="email">'
                                  '<input type="password" name="password"><button>Log in</button></form>'))

        self.reports_allowed_for_viewer = reports_allowed_for_viewer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"


@pytest.fixture
def app() -> Iterator[App]:
    a = App()
    yield a
    a.server.shutdown()


ENV = {"T_ADMIN_USER": "admin@example.ie", "T_ADMIN_PW": PASSWORDS["admin"],
       "T_VIEWER_USER": "viewer@example.ie", "T_VIEWER_PW": PASSWORDS["viewer"]}


def roles_config(url: str, **auth) -> dict:
    return base_config(url, artifacts={"video": "on", "tracing": "on"}, audit={"max_pages": 10, "check_external_links": False},
                       auth={"login_url": "/login", "roles": [
                           {"name": "admin", "username": "${T_ADMIN_USER}", "password": "${T_ADMIN_PW}", "start": "/dashboard"},
                           {"name": "viewer", "username": "${T_VIEWER_USER}", "password": "${T_VIEWER_PW}",
                            "start": "/dashboard", "must_not_access": ["/admin", "/admin/users"]},
                       ], **auth})


def outcomes(run: CliRun) -> dict[str, str]:
    return {t["nodeid"].split("::")[1]: t["outcome"] for t in run.summary["tests"]}


def scanned(run: CliRun, role: str) -> list[str]:
    t = run.test(f"test_pages_are_accessible[{role}-")
    return sorted("/" + s["url"].split("/", 3)[3] for s in t["accessibility"])


@pytest.fixture(scope="module")
def roles_run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[CliRun, App]]:
    """One full audit with roles, shared by the tests that read its results."""
    shared = App()
    try:
        yield invoke_cli(tmp_path_factory.mktemp("roles"), ["site_audit"], config=roles_config(shared.url), env=ENV), shared
    finally:
        shared.server.shutdown()


def test_each_role_is_audited_behind_the_login(roles_run) -> None:
    run, app = roles_run
    assert run.returncode == 0, run.output
    o = outcomes(run)
    assert {k for k in o if k.startswith("test_crawl")} == {
        "test_crawl_found_the_site[public-chromium]", "test_crawl_found_the_site[admin-chromium]",
        "test_crawl_found_the_site[viewer-chromium]"}
    assert scanned(run, "public") == ["/", "/about", "/login"]
    assert scanned(run, "admin") == ["/admin", "/admin/users", "/dashboard", "/reports"]
    assert scanned(run, "viewer") == ["/dashboard", "/reports"]  # no admin link for viewers


def test_logout_and_delete_links_are_never_followed(roles_run) -> None:
    run, app = roles_run
    assert "/logout" not in app.requests and not any("delete" in r for r in app.requests), app.requests


def test_permission_checks_pass_when_the_app_refuses(roles_run) -> None:
    run, _ = roles_run
    assert run.test("test_role_is_refused_restricted_pages[viewer-")["outcome"] == "passed"
    assert run.test("test_role_is_refused_restricted_pages[admin-")["outcome"] == "skipped"


def test_site_wide_checks_run_once(roles_run) -> None:
    run, _ = roles_run
    assert run.test("test_served_securely[admin-")["outcome"] == "skipped"
    assert "checked once for the whole site" in run.test("test_meets_website_requirements[viewer-")["message"]


def test_permission_hole_is_reported(tmp_path: Path) -> None:
    app = App()
    try:
        cfg = roles_config(app.url)
        cfg["auth"]["roles"][1]["must_not_access"] = ["/admin", "/reports"]  # but the app lets viewers see reports
        run = invoke_cli(tmp_path, ["site_audit", "-k", "refused"], config=cfg, env=ENV)
    finally:
        app.server.shutdown()
    t = run.test("test_role_is_refused_restricted_pages[viewer-")
    assert t["outcome"] == "failed"
    assert "/reports: opened for role 'viewer' (HTTP 200" in t["message"] + t["details"]
    assert "/admin:" not in t["message"] + t["details"]


def test_wrong_password_fails_that_role_clearly(tmp_path: Path, app: App) -> None:
    run = invoke_cli(tmp_path, ["site_audit", "-k", "crawl"], config=roles_config(app.url),
                     env={**ENV, "T_VIEWER_PW": "not-the-password"})
    assert run.test("test_crawl_found_the_site[admin-")["outcome"] == "passed"
    t = run.test("test_crawl_found_the_site[viewer-")
    assert t["outcome"] == "error"  # the role's checks could not run at all
    assert "Could not log in: role 'viewer': login did not succeed, now on /login" in t["message"]
    text = t["message"] + t["details"]
    assert 'the page said: "Wrong email or password"' in text  # why, in the app's own words
    assert "Evidence: after pressing the button: POST /login -> 200" in text  # what the browser saw
    assert "not-the-password" not in run.output + t["message"]


def test_missing_password_says_which_variable(tmp_path: Path, app: App) -> None:
    env = {k: v for k, v in ENV.items() if k != "T_ADMIN_PW"}
    run = invoke_cli(tmp_path, ["site_audit", "-k", "crawl"], config=roles_config(app.url), env=env)
    t = run.test("test_crawl_found_the_site[admin-")
    assert t["outcome"] == "error"
    assert "the password comes from T_ADMIN_PW, which is not set" in t["message"] and ".env" in t["message"]


def test_login_form_that_only_works_once_the_page_has_started(tmp_path: Path, app: App) -> None:
    """Apps that show the form before their script is ready (common with React, Vue and similar): a click
    that comes too early does nothing, so the login is tried again on a settled page."""
    cfg = roles_config(app.url, login_url="/login-late", include_public=False)
    run = invoke_cli(tmp_path, ["site_audit", "-k", "crawl"], config=cfg, env=ENV)
    assert set(outcomes(run).values()) == {"passed"}, run.output


def test_two_step_login(tmp_path: Path, app: App) -> None:
    cfg = roles_config(app.url, login_url="/login2", include_public=False)
    run = invoke_cli(tmp_path, ["site_audit", "-k", "crawl"], config=cfg, env=ENV)
    assert outcomes(run) == {"test_crawl_found_the_site[admin-chromium]": "passed",
                             "test_crawl_found_the_site[viewer-chromium]": "passed"}, run.output


def test_shipped_meridian_config_has_no_credentials() -> None:
    import yaml

    text = (Path(__file__).resolve().parents[1] / "config" / "meridian-data.yaml").read_text()
    for role in yaml.safe_load(text)["auth"]["roles"]:
        assert role["username"].startswith("${") and role["password"].startswith("${"), role["name"]


def test_logged_in_check_text(tmp_path: Path, app: App) -> None:
    cfg = roles_config(app.url, include_public=False, logged_in_check="Dashboard for")
    run = invoke_cli(tmp_path, ["site_audit", "-k", "crawl"], config=cfg, env=ENV)
    assert set(outcomes(run).values()) == {"passed"}, run.output
    cfg = roles_config(app.url, include_public=False, logged_in_check="Welcome back, captain")
    run = invoke_cli(tmp_path / "2", ["site_audit", "-k", "crawl"], config=cfg, env=ENV)
    t = run.test("test_crawl_found_the_site[admin-")
    text = t["message"] + t["details"]
    assert "now on /dashboard (auth.logged_in_check 'Welcome back, captain' was not found)" in text
    assert "the login may have worked" in text


def test_passwords_never_reach_output_reports_traces_or_videos(roles_run) -> None:
    run, _ = roles_run
    leaks = [pw for pw in PASSWORDS.values() if pw in run.output]
    for path in run.run_dir.rglob("*"):
        if not path.is_file():
            continue
        blobs = [path.read_bytes()]
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                blobs += [z.read(n) for n in z.namelist()]
        for blob in blobs:
            leaks += [f"{path.name}: {pw}" for pw in PASSWORDS.values() if pw.encode() in blob]
    assert not leaks, leaks
    assert list(run.run_dir.rglob("*.zip")), "traces were on (tracing: on), so this checked real trace files"


@pytest.mark.parametrize(
    "roles, message",
    [([{"name": "admin"}, {"name": "admin"}], "used twice or reserved"),
     ([{"name": "public"}], "used twice or reserved"),
     ([{"name": "Bad Name!"}], "must be a short word"),
     ("admin", "must be a list")],
)
def test_role_settings_rejected(tmp_path: Path, roles, message: str) -> None:
    import yaml

    from ui_automation.config import load_settings

    cfg = tmp_path / "s.yaml"
    cfg.write_text(yaml.safe_dump({"base_url": "http://localhost:1", "auth": {"roles": roles}}))
    with pytest.raises(ValueError, match=message):
        load_settings(cfg)
