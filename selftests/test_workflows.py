"""Nothing runs on its own: no schedules, and nothing that visits a website or sends a report runs on push.

The owner asked that suites and reports only run when they start them (the site audit only with a URL
they give). These tests keep it that way if a workflow is edited later.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
SECRETS_THAT_REPORT = ("TELEGRAM_BOT_TOKEN", "EMAIL_SMTP_PASSWORD", "ANTHROPIC_API_KEY")


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(wf: dict) -> dict:
    return wf.get(True) or wf.get("on") or {}  # YAML reads a bare `on:` key as True


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_no_workflow_is_scheduled(path: Path) -> None:
    assert "schedule" not in triggers(load(path.name)), f"{path.name} would run on its own"


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_jobs_that_can_report_only_run_by_hand(path: Path) -> None:
    wf = load(path.name)
    automatic = set(triggers(wf)) - {"workflow_dispatch"}
    for name, job in wf["jobs"].items():
        uses_secrets = any(s in yaml.safe_dump(job) for s in SECRETS_THAT_REPORT)
        if uses_secrets and automatic:
            assert job.get("if") == "github.event_name == 'workflow_dispatch'", (
                f"{path.name}:{name} can send reports and would run on {sorted(automatic)}")


def test_site_audit_needs_a_url_and_has_no_default_site() -> None:
    wf = load("site-audit.yml")
    assert set(triggers(wf)) == {"workflow_dispatch"}
    url = triggers(wf)["workflow_dispatch"]["inputs"]["url"]
    assert url["required"] is True and "default" not in url
    script = wf["jobs"]["audit"]["steps"][3]["run"]
    assert '--url "$SITE_URL"' in script and "${{" not in script, "the URL is passed via env, never pasted into the script"
