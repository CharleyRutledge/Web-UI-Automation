"""Real local servers the self-tests talk to over real sockets (no in-process mocks).

- ScenarioSite: a small website with good and deliberately bad pages.
- StandInAPI: a scriptable HTTP server that answers like Telegram / Anthropic, used for the error
  paths (401, 429, 500, timeouts, broken JSON) that the real services cannot produce on demand.
- SMTP servers (aiosmtpd): plain, STARTTLS, implicit TLS, login-required, rejecting, slow.
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import threading
import time
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_cert(directory: Path) -> tuple[Path, Path]:
    """Self-signed certificate for 127.0.0.1 (the openssl CLI exists locally and on CI runners)."""
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
         "-days", "2", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        check=True, capture_output=True,
    )
    return cert, key


class _Server:
    def __init__(self, handler_cls: type[BaseHTTPRequestHandler], tls: ssl.SSLContext | None = None) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.httpd.daemon_threads = True
        if tls is not None:
            self.httpd.socket = tls.wrap_socket(self.httpd.socket, server_side=True)
        self.httpd.owner = self  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self.scheme = "https" if tls else "http"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- scenario website

_PAGE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>'
         "<body><main>{body}</main></body></html>")


A11Y_GOOD = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Accessible page</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body><header><a href="#main">Skip to content</a></header><main id="main"><h1>Contact us</h1>
<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="Our office on Main Street">
<form><label for="email">Email</label> <input id="email" type="email" autocomplete="email">
<button type="submit">Send</button></form><p style="color:#222;background:#fff">Plenty of contrast.</p>
</main><footer><a href="/privacy">Privacy notice</a></footer></body></html>"""

# Deliberately broken: missing lang, image without alt, unlabeled input, empty button, low contrast, bad link.
A11Y_BAD = """<!doctype html><html><head><meta charset="utf-8"><title>Broken page</title></head>
<body><div><h1>Contact us</h1><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<input type="text" name="email"><button></button>
<p style="color:#bbb;background:#fff">Hard to read grey text.</p><a href="#"></a></div></body></html>"""

_FOOTER_GOOD = """<footer><a href="/privacy">Privacy notice</a> · <a href="/cookies">Cookie policy</a> ·
<a href="/accessibility">Accessibility statement</a> · <a href="/terms">Terms and conditions</a> ·
<a href="/contact">Contact us</a><p>Example Trading Ltd, registered in Ireland, company number 654321.
Registered office: 1 Main Street, Dublin 2, D02 X285. VAT No. IE1234567T. Email: <a href="mailto:hello@example.ie">hello@example.ie</a></p></footer>"""

LEGAL_PAGES: dict[str, str] = {
    "/legal-good": f"""<!doctype html><html lang="en"><head><title>Shop</title></head><body><main><h1>Shop</h1>
<div role="dialog" aria-label="Cookies"><p>We use analytics cookies only if you agree.</p>
<button>Accept all</button> <button>Reject all</button></div></main>{_FOOTER_GOOD}</body></html>""",
    # Tracking before consent, accept-only banner, and none of the required information.
    "/legal-bad": """<!doctype html><html lang="en"><head><title>Shop</title>
<script>document.cookie = "_fbp=fb.1.123.456; path=/";</script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TEST0000"></script></head>
<body><main><h1>Shop</h1><div class="banner">We use cookies. <button>Accept</button></div></main>
<footer><a href="/privacy-broken">Privacy</a></footer></body></html>""",
    "/privacy": "<!doctype html><html lang='en'><head><title>Privacy</title></head><body><h1>Privacy notice</h1></body></html>",
    "/cookies": "<!doctype html><html lang='en'><head><title>Cookies</title></head><body><h1>Cookies</h1></body></html>",
    "/accessibility": "<!doctype html><html lang='en'><head><title>Accessibility</title></head><body><h1>Accessibility statement</h1></body></html>",
    "/terms": "<!doctype html><html lang='en'><head><title>Terms</title></head><body><h1>Terms</h1></body></html>",
    "/contact": "<!doctype html><html lang='en'><head><title>Contact</title></head><body><h1>Contact</h1></body></html>",
}
LEGAL_HEADERS: dict[str, dict[str, str]] = {
    "/legal-good": {"Set-Cookie": "sessionid=abc123; Path=/; HttpOnly; SameSite=Lax"},  # strictly necessary: fine
    "/legal-bad": {"Set-Cookie": "_ga=GA1.1.111.222; Path=/"},
}


class _SiteHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, html: str, headers: dict[str, str] | None = None) -> None:
        data = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/ok"):
            self._send(200, _PAGE.format(title="Playwright test site", body="<h1>Playwright</h1>"))
        elif path == "/docs/intro":
            self._send(200, _PAGE.format(title="Installation | Playwright", body="<h1>Installation</h1>"))
        elif path == "/missing":
            self._send(200, _PAGE.format(title="Playwright", body="<h1>Something else</h1>"))
        elif path == "/slow":
            time.sleep(float(self.headers.get("X-Delay", "6")))
            self._send(200, _PAGE.format(title="Slow", body="<h1>Installation</h1>"))
        elif path == "/error500":
            self._send(500, _PAGE.format(title="Server error", body="<h1>Internal Server Error</h1>"))
        elif path == "/loop":
            self._send(302, "", {"Location": "/loop"})
        elif path == "/tall":
            rows = "".join(f"<p>Row {i}</p>" for i in range(400))
            self._send(200, _PAGE.format(title="Tall", body=f"<h1>Installation</h1>{rows}"))
        elif path == "/a11y-good":
            self._send(200, A11Y_GOOD)
        elif path == "/a11y-bad":
            self._send(200, A11Y_BAD)
        elif path in LEGAL_PAGES:
            self._send(200, LEGAL_PAGES[path], LEGAL_HEADERS.get(path))
        elif path == "/secret":
            self._send(200, _PAGE.format(title="Account", body="<h1>Account</h1><p>token=SENTINEL_PAGE_SECRET</p>"))
        else:
            self._send(404, _PAGE.format(title="Not found", body="<h1>Not found</h1>"))


class ScenarioSite(_Server):
    def __init__(self, tls: ssl.SSLContext | None = None) -> None:
        super().__init__(_SiteHandler, tls)


# --------------------------------------------------------------------------- stand-in HTTP APIs


@dataclass
class Reply:
    status: int = 200
    body: Any = None  # dict/list -> JSON; str/bytes -> raw
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body or b"null")


class _StandInHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def _handle(self) -> None:
        owner: StandInAPI = self.server.owner  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        owner.requests.append(Recorded(self.command, self.path, dict(self.headers), body))
        reply = owner.next_reply(self.path)
        if reply.delay:
            time.sleep(reply.delay)
        payload = reply.body
        if isinstance(payload, (dict, list)) or payload is None:
            data, ctype = json.dumps(payload if payload is not None else {}).encode(), "application/json"
        else:
            data, ctype = (payload.encode() if isinstance(payload, str) else payload), "text/plain"
        try:
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.headers.get("Content-Type", ctype))
            self.send_header("Content-Length", str(len(data)))
            for k, v in reply.headers.items():
                if k != "Content-Type":
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (timeout test)

    do_GET = do_POST = _handle  # noqa: N815


class StandInAPI(_Server):
    """Scripted replies per path substring; unscripted requests get `default`."""

    def __init__(self, default: Callable[[str], Reply] | None = None) -> None:
        self.requests: list[Recorded] = []
        self._script: list[tuple[str, Reply]] = []
        self._lock = threading.Lock()
        self._default = default or (lambda path: Reply(404, {"error": "not scripted"}))
        super().__init__(_StandInHandler)

    def script(self, path_contains: str, *replies: Reply) -> None:
        with self._lock:
            self._script.extend((path_contains, r) for r in replies)

    def next_reply(self, path: str) -> Reply:
        with self._lock:
            for i, (needle, reply) in enumerate(self._script):
                if needle in path:
                    del self._script[i]
                    return reply
        return self._default(path)

    def calls(self, path_contains: str = "") -> list[Recorded]:
        return [r for r in self.requests if path_contains in r.path]


def telegram_ok(path: str) -> Reply:
    if path.endswith("/getUpdates"):
        return Reply(200, {"ok": True, "result": [{"update_id": 1, "message": {"chat": {"id": 4242}}}]})
    return Reply(200, {"ok": True, "result": {"message_id": 1}})


def anthropic_message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def anthropic_error(kind: str, message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": message}}


# --------------------------------------------------------------------------- SMTP


class MailSink:
    """aiosmtpd handler that keeps every accepted message; can reject or stall on demand."""

    def __init__(self, reject: set[str] | None = None, delay: float = 0.0) -> None:
        self.messages: list[tuple[str, list[str], Message]] = []
        self.tls: list[bool] = []  # whether each accepted message arrived over TLS
        self.reject = reject or set()
        self.delay = delay

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: N802
        if address in self.reject:
            return "550 5.1.1 Mailbox unavailable"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), message_from_bytes(envelope.content)))
        self.tls.append(session.ssl is not None)
        return "250 Message accepted"


def start_smtp(
    handler: MailSink,
    *,
    starttls: ssl.SSLContext | None = None,
    implicit_tls: ssl.SSLContext | None = None,
    login: tuple[str, str] | None = None,
):
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult, LoginPassword

    params: dict[str, Any] = {}
    if starttls is not None:
        params.update(tls_context=starttls, require_starttls=True)
    if login is not None:
        user, password = login

        def authenticator(server, session, envelope, mechanism, auth_data):
            ok = isinstance(auth_data, LoginPassword) and auth_data.login.decode() == user and (
                auth_data.password.decode() == password
            )
            return AuthResult(success=ok)

        params.update(authenticator=authenticator, auth_required=True, auth_require_tls=False)
    controller = Controller(
        handler, hostname="127.0.0.1", port=free_port(), ssl_context=implicit_tls, server_hostname="127.0.0.1", **params
    )
    controller.start()
    return controller
