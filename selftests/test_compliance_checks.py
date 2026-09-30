"""Irish / EU website requirement checks, through the real CLI and the real UI test (tests/test_compliance.py)
against a compliant shop page and a non-compliant one on the local site."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness import CliRun, base_config, invoke_cli
from ui_automation.compliance import ALL_CHECKS
from ui_automation.config import load_settings


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory, site) -> CliRun:
    cfg = base_config(site.url, compliance={"enabled": True, "pages": ["/legal-good", "/legal-bad"],
                                            "checks": list(ALL_CHECKS)})
    result = invoke_cli(tmp_path_factory.mktemp("compliance"), ["tests/test_compliance.py"], config=cfg)
    assert result.run_dir is not None, result.output
    return result


def results(run: CliRun, page: str) -> dict[str, dict]:
    t = run.test(f"test_page_meets_website_requirements[{page}")
    [entry] = t["compliance"]
    return {r["check"]: r for r in entry["results"]}


def test_compliant_page_passes_every_check(run: CliRun) -> None:
    t = run.test("test_page_meets_website_requirements[/legal-good")
    assert t["outcome"] == "passed", t["message"]
    r = results(run, "/legal-good")
    assert set(r) == set(ALL_CHECKS) and all(x["passed"] for x in r.values())
    assert "IE1234567T" in r["company_details"]["detail"]


def test_tracking_before_consent_is_caught(run: CliRun) -> None:
    detail = results(run, "/legal-bad")["cookie_consent"]["detail"]
    assert "_ga" in detail and "_fbp" in detail, detail  # server-set and script-set tracking cookies
    assert "googletagmanager.com" in detail, detail  # tracker contacted before consent
    assert "without an equally easy 'reject'" in detail


def test_missing_and_broken_links_are_caught(run: CliRun) -> None:
    r = results(run, "/legal-bad")
    assert not r["privacy_notice"]["passed"] and "does not load" in r["privacy_notice"]["detail"]
    assert not r["accessibility_statement"]["passed"] and "No link" in r["accessibility_statement"]["detail"]
    assert not r["terms"]["passed"] and not r["contact_details"]["passed"]
    assert not r["company_details"]["passed"]
    assert "company registration number" in r["company_details"]["detail"]


def test_each_result_names_its_legal_basis(run: CliRun) -> None:
    r = results(run, "/legal-bad")
    assert "S.I. 336/2011" in r["cookie_consent"]["law"] and "GDPR" in r["privacy_notice"]["law"]
    assert "Companies Act 2014" in r["company_details"]["law"]


def test_bad_page_fails_the_run(run: CliRun) -> None:
    t = run.test("test_page_meets_website_requirements[/legal-bad")
    assert t["outcome"] == "failed" and run.returncode == 1
    assert "Cookie consent:" in t["message"] and "Privacy notice:" in t["message"]


def test_trackers_are_blocked_during_tests(run: CliRun) -> None:
    """The check itself must never send analytics hits from CI: tracker requests are recorded, then aborted."""
    detail = results(run, "/legal-bad")["cookie_consent"]["detail"]
    assert "contacted trackers" in detail


def test_report_section(run: CliRun) -> None:
    html = (run.run_dir / "summary.html").read_text(encoding="utf-8")
    assert "Website requirements (Ireland / EU)" in html and "ePrivacy Regulations 2011" in html


def test_off_by_default(tmp_path: Path, site) -> None:
    r = invoke_cli(tmp_path, ["tests/test_compliance.py"], config=base_config(site.url))
    assert r.returncode == 0 and r.summary["skipped"] == 1


def test_only_selected_checks_run(tmp_path: Path, site) -> None:
    cfg = base_config(site.url, compliance={"enabled": True, "pages": ["/legal-bad"], "checks": ["privacy_notice"]})
    r = invoke_cli(tmp_path, ["tests/test_compliance.py"], config=cfg)
    assert set(results(r, "/legal-bad")) == {"privacy_notice"}


@pytest.mark.parametrize(
    "raw, match",
    [({"checks": ["gdpr_magic"]}, "unknown"), ({"checks": []}, "checks"), ({"pages": []}, "pages"),
     ({"enabled": "perhaps"}, "compliance.enabled")],
)
def test_config_validation(tmp_path: Path, raw: dict, match: str) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"base_url": "http://x", "compliance": raw}))
    with pytest.raises(ValueError, match=match):
        load_settings(p)
