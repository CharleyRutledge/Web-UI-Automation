"""Testing apps that run on this computer or the local network (localhost, 127.0.0.1, 192.168.x.x, *.local).

- is_local(url): such sites often use self-signed HTTPS (or none), so certificate errors are accepted for
  them and the HTTPS/security-header checks are left to the deployed site.
- check_running(url): a clear message when nothing is listening, instead of a wall of browser errors.
- AppServer: start the app with a command (e.g. "npm run dev"), wait until it answers, stop it afterwards.
"""

from __future__ import annotations

import ipaddress
import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

_LOCAL_NAMES = {"localhost", "host.docker.internal"}
_LOCAL_SUFFIXES = (".localhost", ".local", ".test", ".internal", ".lan", ".home.arpa")


def is_local(url: str) -> bool:
    """True for this machine and private networks: localhost, 127.x, ::1, 10.x, 172.16-31.x, 192.168.x, *.local."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified


def _probe(url: str, timeout: float) -> str:
    """'' when something answers at url (any HTTP status counts: the server is up), else why not."""
    context = ssl.create_default_context()
    handlers: list = []
    if is_local(url):
        # Self-signed development certificates are normal on local servers, and a proxy (HTTP_PROXY) cannot
        # reach this computer's localhost, so local addresses are asked directly.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers, urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "ui-automation-readiness-check"})
    try:
        # http(s) only: check_running and AppServer are only given http:// or https:// addresses.
        with opener.open(request, timeout=timeout):  # nosec B310
            return ""
    except urllib.error.HTTPError:
        return ""  # 404/500 still means a server is listening
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return str(reason) or type(exc).__name__


def check_running(url: str, timeout: float = 5.0) -> str:
    """'' when the app answers; otherwise a message saying what to do."""
    if not url.lower().startswith(("http://", "https://")):
        return f"{url!r} is not an http(s) address"
    problem = _probe(url, timeout)
    if not problem:
        return ""
    where = urlparse(url)
    message = f"Nothing is answering at {where.scheme}://{where.netloc} ({problem})."
    if os.environ.get("GITHUB_ACTIONS") == "true" and is_local(url):
        return (message + " On GitHub, 'localhost' means GitHub's own machine, not your computer: run local "
                "apps from your computer with python -m ui_automation --url ...")
    return message + " Start the app first, or let the tests start it with --start \"<command>\"."


class AppServer:
    """Runs the app's start command in its own process group and stops all of it afterwards."""

    def __init__(self, command: str, url: str, *, timeout: float = 120.0, cwd: Path | None = None,
                 log: Path | None = None) -> None:
        self.command, self.url, self.timeout, self.cwd, self.log = command, url, timeout, cwd, log
        self.process: subprocess.Popen | None = None
        self._log_file = None

    def start(self) -> str:
        """Start and wait until the app answers. '' when ready, otherwise what went wrong (it is stopped)."""
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.log, "wb")  # noqa: SIM115 - closed in stop()
        output = self._log_file or subprocess.DEVNULL
        kwargs: dict = {"cwd": self.cwd, "stdout": output, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True  # its own process group: npm -> node -> ... all stop together
        # The user's own start command (like an npm script), run on their machine: a shell is what they expect.
        self.process = subprocess.Popen(self.command, shell=True, **kwargs)  # noqa: S602  # nosec B602
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                code = self.process.returncode
                self.stop()
                return (f"The start command exited (code {code}) before {self.url} answered"
                        + (f"; its output is in {self.log}" if self.log else "") + ".")
            if not _probe(self.url, 2.0):
                return ""
            time.sleep(0.5)
        self.stop()
        return (f"{self.url} did not answer within {self.timeout:g} s of running {self.command!r}"
                + (f"; its output is in {self.log}" if self.log else "") + ".")

    def stop(self) -> None:
        proc, self.process = self.process, None
        if proc is not None and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    if sys.platform != "win32":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                    proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if self._log_file:
            self._log_file.close()
            self._log_file = None
