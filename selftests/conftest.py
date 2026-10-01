from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from servers import (
    MailSink,
    ScenarioSite,
    StandInAPI,
    anthropic_message,
    make_cert,
    start_smtp,
    telegram_ok,
    Reply,
)

from harness import SECRET_ENV, CliRun, invoke_cli


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a developer's or CI's real secrets reach a self-test (they must set their own)."""
    for name in SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEB_UI_NO_DOTENV", "1")  # nor the keys in a developer's .env file


@pytest.fixture(scope="session")
def certs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return make_cert(tmp_path_factory.mktemp("certs"))


@pytest.fixture(scope="session")
def server_tls(certs: tuple[Path, Path]) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(*map(str, certs))
    return ctx


@pytest.fixture(scope="session")
def site() -> Iterator[ScenarioSite]:
    server = ScenarioSite()
    yield server
    server.close()


@pytest.fixture(scope="session")
def tls_site(server_tls: ssl.SSLContext) -> Iterator[ScenarioSite]:
    server = ScenarioSite(tls=server_tls)
    yield server
    server.close()


@pytest.fixture
def telegram_api() -> Iterator[StandInAPI]:
    server = StandInAPI(telegram_ok)
    yield server
    server.close()


@pytest.fixture
def anthropic_api() -> Iterator[StandInAPI]:
    server = StandInAPI(lambda path: Reply(200, anthropic_message("What broke: the heading is missing.")))
    yield server
    server.close()


@pytest.fixture
def smtp() -> Iterator[Callable[..., tuple[Any, MailSink]]]:
    started = []

    def factory(**kwargs: Any) -> tuple[Any, MailSink]:
        sink = MailSink(reject=kwargs.pop("reject", None), delay=kwargs.pop("delay", 0.0))
        controller = start_smtp(sink, **kwargs)
        started.append(controller)
        return controller, sink

    yield factory
    for controller in started:
        controller.stop()


@pytest.fixture
def run_cli(tmp_path: Path) -> Callable[..., CliRun]:
    return lambda *args, **kwargs: invoke_cli(tmp_path, *args, **kwargs)
