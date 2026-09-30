"""Every setting in settings.yaml: valid values, boundaries, wrong types, env substitution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ui_automation.config import load_settings

REPO = Path(__file__).resolve().parents[1]


def load(tmp_path: Path, text: str):
    p = tmp_path / "s.yaml"
    p.write_text(text, encoding="utf-8")
    return load_settings(p)


BASE = "base_url: http://x\n"


@pytest.mark.parametrize("path", ["config/settings.yaml", "config/settings.example.yaml"])
def test_shipped_configs_load(path: str) -> None:
    s = load_settings(REPO / path)
    assert s.base_url.startswith("https://")


def test_defaults(tmp_path: Path) -> None:
    s = load(tmp_path, BASE)
    assert (s.browser, s.headless, s.timeout_ms, s.slow_mo_ms) == ("chromium", True, 30000, 0)
    assert (s.viewport_width, s.viewport_height, s.strict_selectors) == (1280, 720, True)
    assert s.ai.model == "claude-sonnet-5-5" and s.ai.timeout_seconds == 60
    assert s.notifications.email.use_ssl is None and s.notifications.email.timeout_seconds == 30
    assert s.notifications.telegram.timeout_seconds == 60


@pytest.mark.parametrize(
    "url, expected",
    [("https://a.example/", "https://a.example"), ("  http://a:8080/x/  ", "http://a:8080/x"), ("HTTP://A", "HTTP://A")],
)
def test_base_url_normalised(tmp_path: Path, url: str, expected: str) -> None:
    assert load(tmp_path, f"base_url: '{url}'\n").base_url == expected


@pytest.mark.parametrize("url", ["", "playwright.dev", "ftp://x", "http://", "5", "null", "/relative"])
def test_base_url_rejected(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        load(tmp_path, f"base_url: {url}\n")


BOOL_FIELDS = ["headless", "strict_selectors", "screenshot_full_page"]


@pytest.mark.parametrize("field", BOOL_FIELDS)
@pytest.mark.parametrize(
    "raw, expected",
    [("true", True), ("false", False), ("'yes'", True), ("'no'", False), ("'1'", True), ("'0'", False),
     ("'TRUE'", True), ("'off'", False), ("1", True), ("0", False)],
)
def test_booleans(tmp_path: Path, field: str, raw: str, expected: bool) -> None:
    assert getattr(load(tmp_path, BASE + f"{field}: {raw}\n"), field) is expected


@pytest.mark.parametrize("field", BOOL_FIELDS)
@pytest.mark.parametrize("raw", ["maybe", "'2x'", "[1]"])
def test_booleans_rejected(tmp_path: Path, field: str, raw: str) -> None:
    with pytest.raises(ValueError, match=field):
        load(tmp_path, BASE + f"{field}: {raw}\n")


@pytest.mark.parametrize("env_value, expected", [("false", False), ("true", True), ("", False)])
def test_boolean_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_value: str, expected: bool) -> None:
    monkeypatch.setenv("UI_HEADLESS", env_value)
    assert load(tmp_path, BASE + 'headless: "${UI_HEADLESS}"\n').headless is expected


@pytest.mark.parametrize(
    "field, raw, expected",
    [("timeout_ms", "1", 1), ("timeout_ms", "'2500'", 2500), ("slow_mo_ms", "0", 0), ("slow_mo_ms", "250", 250)],
)
def test_integers(tmp_path: Path, field: str, raw: str, expected: int) -> None:
    assert getattr(load(tmp_path, BASE + f"{field}: {raw}\n"), field) == expected


@pytest.mark.parametrize(
    "field, raw",
    [("timeout_ms", "0"), ("timeout_ms", "-5"), ("timeout_ms", "abc"), ("timeout_ms", "1.5x"),
     ("slow_mo_ms", "-1"), ("slow_mo_ms", "fast")],
)
def test_integers_rejected(tmp_path: Path, field: str, raw: str) -> None:
    with pytest.raises(ValueError, match=field):
        load(tmp_path, BASE + f"{field}: {raw}\n")


@pytest.mark.parametrize("raw", ["{width: 0, height: 10}", "{width: 10, height: -1}", "{width: wide}", "5", "[1, 2]"])
def test_viewport_rejected(tmp_path: Path, raw: str) -> None:
    with pytest.raises(ValueError, match="viewport"):
        load(tmp_path, BASE + f"viewport: {raw}\n")


@pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit", "Firefox"])
def test_browsers(tmp_path: Path, browser: str) -> None:
    assert load(tmp_path, BASE + f"browser: {browser}\n").browser == browser.lower()


@pytest.mark.parametrize("browser", ["chrome", "edge", "''"])
def test_browser_rejected(tmp_path: Path, browser: str) -> None:
    with pytest.raises(ValueError, match="browser"):
        load(tmp_path, BASE + f"browser: {browser}\n")


@pytest.mark.parametrize(
    "raw, video, tracing",
    [("{video: off}", "off", "retain-on-failure"), ("{video: false, tracing: on}", "off", "on"),
     ("{video: retain-on-failure, tracing: 'off'}", "retain-on-failure", "off")],
)
def test_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str, video: str, tracing: str) -> None:
    monkeypatch.delenv("CI", raising=False)
    a = load(tmp_path, BASE + f"artifacts: {raw}\n").artifacts
    assert (a.video, a.tracing) == (video, tracing)


@pytest.mark.parametrize("ci", ["true", "1", "yes"])
def test_ci_forces_video_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ci: str) -> None:
    monkeypatch.setenv("CI", ci)
    assert load(tmp_path, BASE + "artifacts: {video: off}\n").artifacts.video == "on"


@pytest.mark.parametrize(
    "raw, match",
    [("{video: sometimes}", "video"), ("{tracing: always}", "tracing"),
     ("{navigation_wait_until: never}", "navigation_wait_until"), ("on", "artifacts")],
)
def test_artifacts_rejected(tmp_path: Path, raw: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load(tmp_path, BASE + f"artifacts: {raw}\n")


@pytest.mark.parametrize(
    "raw, expected",
    [("a@x.io", ("a@x.io",)), ("'a@x.io, b@x.io,, '", ("a@x.io", "b@x.io")), ("[a@x.io, ' ', b@x.io]", ("a@x.io", "b@x.io"))],
)
def test_email_recipients(tmp_path: Path, raw: str, expected: tuple[str, ...]) -> None:
    assert load(tmp_path, BASE + f"notifications: {{email: {{to_addrs: {raw}}}}}\n").notifications.email.to_addrs == expected


@pytest.mark.parametrize(
    "raw, match",
    [("{email: {smtp_port: 0}}", "smtp_port"), ("{email: {smtp_port: http}}", "smtp_port"),
     ("{email: {use_ssl: perhaps}}", "use_ssl"), ("{email: {timeout_seconds: 0}}", "timeout_seconds"),
     ("{telegram: {timeout_seconds: -1}}", "timeout_seconds"), ("{telegram: {enabled: sure}}", "telegram.enabled"),
     ("{email: [1]}", "email"), ("[]", "notifications")],
)
def test_notifications_rejected(tmp_path: Path, raw: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load(tmp_path, BASE + f"notifications: {raw}\n")


def test_unresolved_env_placeholders_stay_literal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    t = load(tmp_path, BASE + 'notifications: {telegram: {bot_token: "${NOPE_TOKEN}"}}\n').notifications.telegram
    assert t.bot_token == "${NOPE_TOKEN}"


@pytest.mark.parametrize(
    "raw, match",
    [("{max_tokens: 0}", "max_tokens"), ("{max_tokens: lots}", "max_tokens"), ("{timeout_seconds: nope}", "timeout_seconds"),
     ("{enabled: kinda}", "ai.enabled")],
)
def test_ai_rejected(tmp_path: Path, raw: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load(tmp_path, BASE + f"ai: {raw}\n")


def test_ai_enabled_follows_api_key_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert load(tmp_path, BASE + "ai: {model: m}\n").ai.enabled is True
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert load(tmp_path, BASE + "ai: {model: m}\n").ai.enabled is False


@pytest.mark.parametrize("text", ["", "# only a comment\n", "- a\n", "just text\n", "42\n"])
def test_config_root_must_be_mapping(tmp_path: Path, text: str) -> None:
    with pytest.raises(ValueError):
        load(tmp_path, text)


def test_broken_yaml_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises((ValueError, yaml.YAMLError)):
        load(tmp_path, "base_url: [unclosed\n")


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        load_settings(tmp_path / "nope.yaml")


def test_web_ui_config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "other.yaml"
    p.write_text("base_url: http://from-env\n", encoding="utf-8")
    monkeypatch.setenv("WEB_UI_CONFIG", str(p))
    assert load_settings().base_url == "http://from-env"
