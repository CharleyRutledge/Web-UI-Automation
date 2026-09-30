from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from ui_automation.env import resolve_env

_VALID_BROWSERS = frozenset({"chromium", "firefox", "webkit"})
_VALID_VIDEO = frozenset({"on", "off", "retain-on-failure"})
_VALID_TRACING = frozenset({"on", "off", "retain-on-failure"})
_VALID_WAIT_UNTIL = frozenset({"commit", "domcontentloaded", "load", "networkidle"})


@dataclass(frozen=True)
class ArtifactSettings:
    video: str = "on"
    tracing: str = "retain-on-failure"
    navigation_wait_until: str = "domcontentloaded"


@dataclass(frozen=True)
class AiSettings:
    enabled: bool = False
    model: str = "claude-sonnet-5-5"
    max_tokens: int = 2048


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    use_tls: bool = True
    from_addr: str = ""
    to_addrs: tuple[str, ...] = ()


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass(frozen=True)
class NotificationSettings:
    email: EmailSettings = field(default_factory=EmailSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)


@dataclass(frozen=True)
class Settings:
    base_url: str
    browser: str
    headless: bool
    timeout_ms: int
    slow_mo_ms: int
    viewport_width: int
    viewport_height: int
    strict_selectors: bool
    screenshot_full_page: bool
    artifacts: ArtifactSettings
    ai: AiSettings
    notifications: NotificationSettings

    @property
    def video_mode(self) -> str:
        return self.artifacts.video

    @property
    def tracing_mode(self) -> str:
        return self.artifacts.tracing

    @property
    def navigation_wait_until(self) -> str:
        return self.artifacts.navigation_wait_until


def _as_mapping(data: Any, what: str = "Config root") -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{what} must be a mapping")
    return data


def _section(raw: Mapping[str, Any] | None, key: str) -> Mapping[str, Any]:
    """Return raw[key] as a mapping; a missing/empty section is {} and a wrong type is an error."""
    value = (raw or {}).get(key)
    if value is None:
        return {}
    return _as_mapping(value, f"settings.yaml: '{key}'")


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def _as_bool(value: Any, default: bool = False, name: str = "value") -> bool:
    """Parse booleans, including strings that come from ${ENV_VAR} substitution ("false" is False)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"settings.yaml: {name} must be a boolean, got {value!r}")


def _as_int(value: Any, default: int, name: str, *, minimum: int = 0) -> int:
    if value is None:
        return default
    try:
        number = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise ValueError(f"settings.yaml: {name} must be an integer, got {value!r}") from None
    if number < minimum:
        raise ValueError(f"settings.yaml: {name} must be >= {minimum}, got {number}")
    return number


def _load_email(raw: Mapping[str, Any] | None) -> EmailSettings:
    raw = raw or {}
    to_raw = raw.get("to_addrs") or raw.get("to") or []
    if isinstance(to_raw, str):
        to_addrs = tuple(a.strip() for a in to_raw.split(",") if a.strip())
    else:
        to_addrs = tuple(str(a).strip() for a in to_raw if str(a).strip())
    password = str(raw.get("smtp_password", "") or os.environ.get("EMAIL_SMTP_PASSWORD", ""))
    return EmailSettings(
        enabled=_as_bool(raw.get("enabled"), False, "notifications.email.enabled"),
        smtp_host=str(raw.get("smtp_host") or ""),
        smtp_port=_as_int(raw.get("smtp_port"), 587, "notifications.email.smtp_port", minimum=1),
        smtp_user=str(raw.get("smtp_user", "") or os.environ.get("EMAIL_SMTP_USER", "")),
        smtp_password=password,
        use_tls=_as_bool(raw.get("use_tls"), True, "notifications.email.use_tls"),
        from_addr=str(raw.get("from_addr", "") or raw.get("from", "")),
        to_addrs=to_addrs,
    )


def _load_telegram(raw: Mapping[str, Any] | None) -> TelegramSettings:
    raw = raw or {}
    return TelegramSettings(
        enabled=_as_bool(raw.get("enabled"), False, "notifications.telegram.enabled"),
        bot_token=str(raw.get("bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")),
        chat_id=str(raw.get("chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", "")),
    )


def _normalize_artifact_flag(value: Any, default: str) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value or default).lower()


def _load_artifacts(raw: Mapping[str, Any] | None) -> ArtifactSettings:
    raw = raw or {}
    video = _normalize_artifact_flag(raw.get("video", "on"), "on")
    tracing = _normalize_artifact_flag(raw.get("tracing", "retain-on-failure"), "retain-on-failure")
    wait = str(raw.get("navigation_wait_until", "domcontentloaded")).lower()
    if video not in _VALID_VIDEO:
        raise ValueError(f"artifacts.video must be one of {_VALID_VIDEO}")
    if tracing not in _VALID_TRACING:
        raise ValueError(f"artifacts.tracing must be one of {_VALID_TRACING}")
    if wait not in _VALID_WAIT_UNTIL:
        raise ValueError(f"navigation_wait_until must be one of {_VALID_WAIT_UNTIL}")
    if os.environ.get("CI", "").lower() in ("1", "true", "yes"):
        video = "on"
    return ArtifactSettings(video=video, tracing=tracing, navigation_wait_until=wait)


def _load_ai(raw: Mapping[str, Any] | None) -> AiSettings:
    raw = raw or {}
    if raw.get("enabled") is not None:
        enabled = _as_bool(raw["enabled"], False, "ai.enabled")
    else:
        enabled = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return AiSettings(
        enabled=enabled,
        model=str(raw.get("model", "claude-sonnet-5-5")),
        max_tokens=_as_int(raw.get("max_tokens"), 2048, "ai.max_tokens", minimum=1),
    )


def load_settings(path: str | Path | None = None) -> Settings:
    cfg_path = Path(
        path
        or os.environ.get("WEB_UI_CONFIG")
        or Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    )
    raw = resolve_env(yaml.safe_load(cfg_path.read_text(encoding="utf-8")))
    m = _as_mapping(raw)

    base_url = str(m.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("settings.yaml: base_url is required")
    if not re.match(r"^https?://[^/\s]+", base_url):
        raise ValueError(f"settings.yaml: base_url must start with http:// or https://, got {base_url!r}")

    browser = str(m.get("browser", "chromium")).lower()
    if browser not in _VALID_BROWSERS:
        raise ValueError(f"settings.yaml: browser must be one of {_VALID_BROWSERS}")

    vp = _section(m, "viewport")
    notif_raw = _section(m, "notifications")

    return Settings(
        base_url=base_url,
        browser=browser,
        headless=_as_bool(m.get("headless"), True, "headless"),
        timeout_ms=_as_int(m.get("timeout_ms"), 30_000, "timeout_ms", minimum=1),
        slow_mo_ms=_as_int(m.get("slow_mo_ms"), 0, "slow_mo_ms"),
        viewport_width=_as_int(vp.get("width"), 1280, "viewport.width", minimum=1),
        viewport_height=_as_int(vp.get("height"), 720, "viewport.height", minimum=1),
        strict_selectors=_as_bool(m.get("strict_selectors"), True, "strict_selectors"),
        screenshot_full_page=_as_bool(m.get("screenshot_full_page"), True, "screenshot_full_page"),
        artifacts=_load_artifacts(_section(m, "artifacts")),
        ai=_load_ai(_section(m, "ai")),
        notifications=NotificationSettings(
            email=_load_email(_section(notif_raw, "email")),
            telegram=_load_telegram(_section(notif_raw, "telegram")),
        ),
    )
