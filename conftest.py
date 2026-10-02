from __future__ import annotations

import base64
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import BrowserContext, Playwright, expect

from ui_automation.config import Settings, accepts_self_signed, load_settings
from ui_automation.reporting.pipeline import finalize_run

_VSCODE_PYTHON_EXTENSION_ID = "ms-python.python"
_START_KEY = pytest.StashKey[float]()


def _debugger_attached() -> bool:
    pydevd = sys.modules.get("pydevd")
    if not pydevd or not hasattr(pydevd, "get_global_debugger"):
        return False
    debugger = pydevd.get_global_debugger()
    if not debugger or not hasattr(debugger, "is_attached"):
        return False
    return bool(debugger.is_attached())


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--config",
        action="store",
        default=None,
        help="Path to settings YAML (default: config/settings.yaml or WEB_UI_CONFIG)",
    )


def _argv_has(flag: str) -> bool:
    return any(a == flag or a.startswith(f"{flag}=") for a in sys.argv)


def pytest_configure(config: pytest.Config) -> None:
    """Apply settings.yaml for browser, video, and tracing when CLI flags are omitted."""
    try:
        settings = load_settings(config.getoption("--config"))
    except Exception:
        return

    if not config.getoption("--browser"):
        config.option.browser = list(settings.browsers or (settings.browser,))

    if not _argv_has("--video"):
        config.option.video = settings.video_mode
    if not _argv_has("--tracing"):
        config.option.tracing = settings.tracing_mode


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[_START_KEY] = time.monotonic()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Record results to summary.json. Notifications are sent by `python -m ui_automation` after
    pytest exits, so a plain `pytest` run never emails, messages or spends API credit."""
    run_dir = Path(os.environ.get("WEB_UI_RUN_DIR", "reports/latest"))
    duration = time.monotonic() - session.config.stash.get(_START_KEY, time.monotonic())
    try:
        settings = load_settings(session.config.getoption("--config"))
    except Exception:
        settings = None
    try:
        finalize_run(session, exitstatus, run_dir.resolve(), duration=duration, settings=settings)
    except Exception as exc:
        print(f"Pipeline finalize skipped: {exc}")


@pytest.fixture(scope="session")
def settings(request: pytest.FixtureRequest) -> Settings:
    cfg = request.config.getoption("--config")
    return load_settings(cfg)


@pytest.fixture(scope="session")
def run_dir(request: pytest.FixtureRequest) -> Path:
    env = os.environ.get("WEB_UI_RUN_DIR")
    if env:
        p = Path(env)
    else:
        p = Path("reports") / "latest"
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


@pytest.fixture(autouse=True)
def _init_step_screenshots(request: pytest.FixtureRequest) -> None:
    request.node.step_screenshots = []


@pytest.fixture
def test_artifacts_dir(run_dir: Path, request: pytest.FixtureRequest) -> Path:
    safe = _safe_filename(request.node.name)
    d = run_dir / "screenshots" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def base_url(settings: Settings) -> str:
    """pytest-base-url / pytest-playwright: enables relative navigation (page.goto('/path'))."""
    return settings.base_url


@pytest.fixture(scope="session")
def browser_type_launch_args(pytestconfig: pytest.Config, settings: Settings) -> dict[str, Any]:
    """Align launch options with settings.yaml; keep pytest-playwright CLI flags."""
    launch_options: dict[str, Any] = {}
    if pytestconfig.getoption("--headed"):
        launch_options["headless"] = False
    elif _VSCODE_PYTHON_EXTENSION_ID in sys.argv[0] and _debugger_attached():
        launch_options["headless"] = False
    else:
        launch_options["headless"] = settings.headless

    browser_channel_option = pytestconfig.getoption("--browser-channel")
    if browser_channel_option:
        launch_options["channel"] = browser_channel_option

    slowmo_option = int(pytestconfig.getoption("--slowmo") or 0)
    if slowmo_option:
        launch_options["slow_mo"] = slowmo_option
    elif settings.slow_mo_ms:
        launch_options["slow_mo"] = settings.slow_mo_ms
    return launch_options


@pytest.fixture(scope="session")
def browser_context_args(
    pytestconfig: pytest.Config,
    playwright: Playwright,
    device: str | None,
    base_url: str | None,
    _pw_artifacts_folder: Any,
    settings: Settings,
) -> dict[str, Any]:
    """Mirror pytest-playwright context defaults plus viewport / strictness from YAML."""
    context_args: dict[str, Any] = {}
    if device:
        context_args.update(playwright.devices[device])
    if base_url:
        context_args["base_url"] = base_url

    video_option = pytestconfig.getoption("--video")
    if video_option in ("on", "retain-on-failure"):
        context_args["record_video_dir"] = _pw_artifacts_folder.name

    context_args["viewport"] = {
        "width": settings.viewport_width,
        "height": settings.viewport_height,
    }
    context_args["strict_selectors"] = settings.strict_selectors
    if accepts_self_signed(settings):
        # Apps on this computer / the local network often use self-signed development certificates.
        context_args["ignore_https_errors"] = True
    return context_args


@pytest.fixture
def context(
    new_context: Any,
    settings: Settings,
) -> BrowserContext:
    ctx = new_context()
    ctx.set_default_timeout(settings.timeout_ms)
    # expect() has its own timeout (5s default); keep it in line with timeout_ms from settings.yaml.
    expect.set_options(timeout=settings.timeout_ms)
    return ctx


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE)
    return name.strip("_") or "test"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> None:
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    for key in ("accessibility", "compliance"):
        recorded = getattr(item, key, None)
        if recorded:
            report.user_properties.append((key, recorded))

    early_steps = getattr(item, "step_screenshots", []) or []
    if early_steps:
        report.user_properties.append(("step_screenshots", [path for _, path in early_steps]))
        report.user_properties.append(("step_labels", [label for label, _ in early_steps]))

    try:
        import pytest_html
    except ImportError:
        return

    if not hasattr(report, "extras"):
        report.extras = []

    steps = getattr(item, "step_screenshots", []) or []
    for label, img_path in steps:
        p = Path(img_path)
        if p.is_file():
            report.extras.append(
                pytest_html.extras.html(f"<p><strong>Step:</strong> {label}</p>")
            )
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            report.extras.append(pytest_html.extras.image(b64))

    if report.failed and not steps:
        report.extras.append(
            pytest_html.extras.html(
                "<p><strong>No step screenshots captured.</strong> "
                "Check Playwright artifacts under <code>playwright-output/</code> "
                "(trace/video when enabled).</p>"
            )
        )
