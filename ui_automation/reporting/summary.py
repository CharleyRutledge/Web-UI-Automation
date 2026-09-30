from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TestResult:
    __test__ = False  # a data class, not a pytest test class
    nodeid: str
    outcome: str  # passed | failed | skipped | error
    duration: float = 0.0
    message: str = ""  # one-line reason for failed / skipped / error
    details: str = ""  # traceback tail for failed / error
    screenshots: list[str] = field(default_factory=list)  # step screenshots, in order
    steps: list[str] = field(default_factory=list)  # step labels, in order
    # Paths relative to the run folder (so a copied folder, e.g. reports/latest, still resolves).
    failure_screenshots: list[str] = field(default_factory=list)  # taken by Playwright at the failure
    videos: list[str] = field(default_factory=list)  # the same recording: .mp4 (if converted) then .webm
    traces: list[str] = field(default_factory=list)  # Playwright trace .zip (open at trace.playwright.dev)
    accessibility: list[dict] = field(default_factory=list)  # one entry per page scanned by expect_accessible
    compliance: list[dict] = field(default_factory=list)  # one entry per website compliance check

    @property
    def title(self) -> str:
        """'test_home_page_loads[chromium]' -> 'Home page loads'."""
        base = self.name.split("[", 1)[0].removeprefix("test_").replace("_", " ").strip()
        return base[:1].upper() + base[1:] if base else self.name

    @property
    def variant(self) -> str:
        """'test_x[chromium]' -> 'chromium' (the parametrize id), '' when there is none."""
        if "[" not in self.name:
            return ""
        return self.name.split("[", 1)[1].rstrip("]").replace("_", " ")

    @property
    def last_step(self) -> str:
        return self.steps[-1] if self.steps else ""

    @property
    def name(self) -> str:
        return self.nodeid.split("::")[-1]

    @property
    def file(self) -> str:
        return self.nodeid.split("::")[0]


@dataclass
class RunSummary:
    exit_status: int
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration: float = 0.0
    report_html: Path | None = None
    run_dir: Path | None = None
    video_files: list[Path] = field(default_factory=list)
    trace_files: list[Path] = field(default_factory=list)
    tests: list[TestResult] = field(default_factory=list)
    video_mode: str = ""
    tracing_mode: str = ""
    base_url: str = ""
    run_url: str = ""  # link to the CI run, when running in GitHub Actions

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped + self.errors

    @property
    def ok(self) -> bool:
        return self.exit_status == 0 and self.failed == 0 and self.errors == 0

    @property
    def problems(self) -> list[TestResult]:
        return [t for t in self.tests if t.outcome in ("failed", "error")]

    @property
    def failure_details(self) -> list[str]:
        return [f"{t.nodeid}\n{t.details}" for t in self.problems]

    def short_status(self) -> str:
        return (
            f"{'PASSED' if self.ok else 'FAILED'} — "
            f"{self.passed} passed, {self.failed} failed, {self.skipped} skipped, {self.errors} errors"
        )

    def headline(self) -> str:
        """One human line, e.g. '✅ PASSED — 11/11 tests (15s)'."""
        secs = f"{self.duration:.0f}s"
        if self.ok:
            skipped = f", {self.skipped} skipped" if self.skipped else ""
            return f"✅ PASSED — {self.passed}/{self.total} tests{skipped} ({secs})"
        bad = self.failed + self.errors
        return f"❌ FAILED — {bad} of {self.total} tests failed ({secs})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_dir.name if self.run_dir else None,
            "exit_status": self.exit_status,
            "ok": self.ok,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
            "total": self.total,
            "duration": round(self.duration, 2),
            "report_html": self.report_html.name if self.report_html else None,
            "video_count": len(self.video_files),
            "trace_count": len(self.trace_files),
            "video_mode": self.video_mode,
            "tracing_mode": self.tracing_mode,
            "base_url": self.base_url,
            "tests": [asdict(t) for t in self.tests],
        }

    @classmethod
    def load(cls, run_dir: Path) -> RunSummary:
        data = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        summary = cls(
            exit_status=int(data.get("exit_status", 1)),
            passed=int(data.get("passed", 0)),
            failed=int(data.get("failed", 0)),
            skipped=int(data.get("skipped", 0)),
            errors=int(data.get("errors", 0)),
            duration=float(data.get("duration", 0.0)),
            run_dir=run_dir,
            video_mode=data.get("video_mode", ""),
            tracing_mode=data.get("tracing_mode", ""),
            base_url=data.get("base_url", ""),
            tests=[TestResult(**t) for t in data.get("tests", [])],
        )
        report = run_dir / (data.get("report_html") or "report.html")
        summary.report_html = report if report.is_file() else None
        summary.video_files = sorted((run_dir / "videos").rglob("*.webm"))
        summary.trace_files = sorted((run_dir / "traces").rglob("*.zip"))
        return summary
