from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunSummary:
    exit_status: int
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    report_html: Path | None = None
    run_dir: Path | None = None
    video_files: list[Path] = field(default_factory=list)
    trace_files: list[Path] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped + self.errors

    @property
    def ok(self) -> bool:
        return self.exit_status == 0 and self.failed == 0 and self.errors == 0

    def short_status(self) -> str:
        return (
            f"{'PASSED' if self.ok else 'FAILED'} — "
            f"{self.passed} passed, {self.failed} failed, {self.skipped} skipped, {self.errors} errors"
        )
