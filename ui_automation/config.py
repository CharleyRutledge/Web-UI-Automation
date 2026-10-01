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
    timeout_seconds: float = 60.0


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
    use_ssl: bool | None = None  # implicit TLS (SMTPS); None = only on port 465
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class AccessibilitySettings:
    enabled: bool = True
    standard: str = "wcag21aa"  # EN 301 549 today; "wcag22aa" adds the WCAG 2.2 criteria
    fail_on: str = "serious"  # minor | moderate | serious | critical | none (report only)
    pages: tuple[str, ...] = ("/",)


DEFAULT_COMPLIANCE_CHECKS = ("privacy_notice", "cookie_consent", "accessibility_statement", "company_details",
                             "contact_details")


@dataclass(frozen=True)
class ComplianceSettings:
    enabled: bool = True  # always on unless a settings file turns it off
    report_only: bool = False  # true: list problems in the report without failing the run (others' sites)
    pages: tuple[str, ...] = ("/",)
    checks: tuple[str, ...] = DEFAULT_COMPLIANCE_CHECKS


@dataclass(frozen=True)
class AuditSettings:
    """Whole-site audit (site_audit/): pages are discovered by following the site's own links."""
    max_pages: int = 15  # pages visited, home first, breadth-first
    load_budget_ms: int = 5000  # a page slower than this to become usable is reported
    check_external_links: bool = True  # also check links to other sites (capped at 60)
    # HTTPS and security headers: "auto" checks public sites and skips local ones (localhost, 192.168.x, ...),
    # where development servers rarely have them; "on" / "off" force it.
    security_checks: str = "auto"


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
    accessibility: AccessibilitySettings = field(default_factory=AccessibilitySettings)
    compliance: ComplianceSettings = field(default_factory=ComplianceSettings)
    audit: AuditSettings = field(default_factory=AuditSettings)
    # Self-signed HTTPS certificates: "auto" accepts them for local addresses only (localhost, 192.168.x, ...);
    # "on" / "off" force it. Public sites are always held to real certificates under "auto".
    allow_self_signed: str = "auto"
    name: str = ""  # what the reports call this run, e.g. "statespend.ie site audit" (default: the site's host)

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


def _as_seconds(value: Any, default: float, name: str) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"settings.yaml: {name} must be a number of seconds, got {value!r}") from None
    if not number > 0:
        raise ValueError(f"settings.yaml: {name} must be > 0, got {number}")
    return number


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
        use_ssl=None if raw.get("use_ssl") is None else _as_bool(raw["use_ssl"], False, "notifications.email.use_ssl"),
        timeout_seconds=_as_seconds(raw.get("timeout_seconds"), 30.0, "notifications.email.timeout_seconds"),
    )


def _load_telegram(raw: Mapping[str, Any] | None) -> TelegramSettings:
    raw = raw or {}
    return TelegramSettings(
        enabled=_as_bool(raw.get("enabled"), False, "notifications.telegram.enabled"),
        bot_token=str(raw.get("bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")),
        chat_id=str(raw.get("chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", "")),
        timeout_seconds=_as_seconds(raw.get("timeout_seconds"), 60.0, "notifications.telegram.timeout_seconds"),
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


_A11Y_STANDARDS = ("wcag21aa", "wcag22aa")
_A11Y_FAIL_ON = ("minor", "moderate", "serious", "critical", "none")


def _pages(raw: Any, name: str) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or not raw or not all(isinstance(p, str) and p.strip() for p in raw):
        raise ValueError(f"settings.yaml: {name} must be a list of paths like '/' or '/contact'")
    return tuple(p.strip() if p.strip().startswith(("/", "http://", "https://")) else "/" + p.strip() for p in raw)


def _load_audit(raw: Mapping[str, Any] | None) -> AuditSettings:
    raw = raw or {}
    return AuditSettings(
        max_pages=_as_int(raw.get("max_pages"), 15, "audit.max_pages", minimum=1),
        load_budget_ms=_as_int(raw.get("load_budget_ms"), 5000, "audit.load_budget_ms", minimum=100),
        check_external_links=_as_bool(raw.get("check_external_links"), True, "audit.check_external_links"),
        security_checks=_auto_on_off(raw.get("security_checks"), "audit.security_checks"),
    )


def _auto_on_off(raw: Any, name: str) -> str:
    if raw is None:
        return "auto"
    if isinstance(raw, bool):  # YAML reads on/off/true/false as booleans
        return "on" if raw else "off"
    mode = str(raw).strip().lower()
    if mode not in ("auto", "on", "off"):
        raise ValueError(f"settings.yaml: {name} must be auto, on or off, got {raw!r}")
    return mode


def accepts_self_signed(settings: Settings, url: str | None = None) -> bool:
    """Whether the browser may accept self-signed certificates when testing `url` (default: base_url)."""
    from ui_automation.local import is_local

    mode = settings.allow_self_signed
    return mode == "on" or (mode == "auto" and is_local(url or settings.base_url))


def _load_compliance(raw: Mapping[str, Any] | None) -> ComplianceSettings:
    from ui_automation.compliance import ALL_CHECKS

    raw = raw or {}
    checks_raw = raw.get("checks", list(DEFAULT_COMPLIANCE_CHECKS))
    if isinstance(checks_raw, str):
        checks_raw = [checks_raw]
    if not isinstance(checks_raw, (list, tuple)) or not checks_raw:
        raise ValueError("settings.yaml: compliance.checks must be a list")
    checks = tuple(str(c).strip().lower() for c in checks_raw)
    unknown = [c for c in checks if c not in ALL_CHECKS]
    if unknown:
        raise ValueError(f"settings.yaml: compliance.checks has unknown {unknown}; choose from {list(ALL_CHECKS)}")
    return ComplianceSettings(
        enabled=_as_bool(raw.get("enabled"), True, "compliance.enabled"),
        report_only=_as_bool(raw.get("report_only"), False, "compliance.report_only"),
        pages=_pages(raw.get("pages", ["/"]), "compliance.pages"),
        checks=checks,
    )


def _load_accessibility(raw: Mapping[str, Any] | None) -> AccessibilitySettings:
    raw = raw or {}
    standard = str(raw.get("standard") or "wcag21aa").lower()
    if standard not in _A11Y_STANDARDS:
        raise ValueError(f"settings.yaml: accessibility.standard must be one of {_A11Y_STANDARDS}")
    fail_on = str(raw.get("fail_on") or "serious").lower()
    if fail_on not in _A11Y_FAIL_ON:
        raise ValueError(f"settings.yaml: accessibility.fail_on must be one of {_A11Y_FAIL_ON}")
    pages = _pages(raw.get("pages", ["/"]), "accessibility.pages")
    return AccessibilitySettings(
        enabled=_as_bool(raw.get("enabled"), True, "accessibility.enabled"),
        standard=standard,
        fail_on=fail_on,
        pages=pages,
    )


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
        timeout_seconds=_as_seconds(raw.get("timeout_seconds"), 60.0, "ai.timeout_seconds"),
    )


def load_settings(path: str | Path | None = None) -> Settings:
    cfg_path = Path(
        path
        or os.environ.get("WEB_UI_CONFIG")
        or Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    )
    try:
        parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{cfg_path}: not valid YAML ({exc})".replace("\n", " ")) from None
    raw = resolve_env(parsed)
    m = _as_mapping(raw)

    # WEB_UI_URL (set by `python -m ui_automation --url ...`) tests that site instead of the file's base_url;
    # the run is then named after it too, not after the site the file was written for.
    url_override = os.environ.get("WEB_UI_URL", "").strip()
    base_url = (url_override or str(m.get("base_url") or "")).strip().rstrip("/")
    name_raw = None if url_override else m.get("name")
    if not base_url:
        raise ValueError("settings.yaml: base_url is required")
    if not re.match(r"^https?://[^/\s]+", base_url, re.IGNORECASE):
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
        accessibility=_load_accessibility(_section(m, "accessibility")),
        compliance=_load_compliance(_section(m, "compliance")),
        audit=_load_audit(_section(m, "audit")),
        name=_load_name(name_raw, base_url),
        allow_self_signed=_auto_on_off(m.get("allow_self_signed"), "allow_self_signed"),
        notifications=NotificationSettings(
            email=_load_email(_section(notif_raw, "email")),
            telegram=_load_telegram(_section(notif_raw, "telegram")),
        ),
    )


def _load_name(raw: Any, base_url: str) -> str:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        host = re.sub(r"^https?://", "", base_url, flags=re.IGNORECASE).split("/")[0]
        return host.removeprefix("www.")
    if not isinstance(raw, str):
        raise ValueError(f"settings.yaml: name must be text, got {raw!r}")
    return " ".join(raw.split())[:80]


def reports_root(project_root: Path) -> Path:
    """Where run folders live: WEB_UI_REPORTS_DIR if set, else <project>/reports."""
    override = os.environ.get("WEB_UI_REPORTS_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else project_root / "reports"
