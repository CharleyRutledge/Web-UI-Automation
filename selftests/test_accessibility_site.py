"""Accessibility scanning of the site under test, through the real CLI and the real UI test
(tests/test_accessibility.py) against an accessible and a deliberately broken page."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness import CliRun, base_config, invoke_cli
from ui_automation.config import load_settings


def run_a11y(workdir: Path, site, **a11y) -> CliRun:
    cfg = base_config(site.url, accessibility={"pages": ["/a11y-good", "/a11y-bad"], **a11y})
    return invoke_cli(workdir, ["tests/test_accessibility.py"], config=cfg)


@pytest.fixture(scope="module")
def default_run(tmp_path_factory: pytest.TempPathFactory, site) -> CliRun:
    run = run_a11y(tmp_path_factory.mktemp("a11y"), site)
    assert run.run_dir is not None, run.output
    return run


def test_accessible_page_passes(default_run: CliRun) -> None:
    t = default_run.test("test_page_is_accessible[/a11y-good")
    assert t["outcome"] == "passed"
    [scan] = t["accessibility"]
    assert scan["url"].endswith("/a11y-good") and scan["standard"] == "wcag21aa" and scan["violations"] == []


def test_broken_page_fails_with_wcag_references(default_run: CliRun) -> None:
    t = default_run.test("test_page_is_accessible[/a11y-bad")
    assert t["outcome"] == "failed" and default_run.returncode == 1
    rules = {v["rule"]: v for v in t["accessibility"][0]["violations"]}
    expected = {"image-alt": "1.1.1", "html-has-lang": "3.1.1", "color-contrast": "1.4.3",
                "button-name": "4.1.2", "label": "4.1.2", "link-name": "2.4.4"}
    for rule, criterion in expected.items():
        assert rule in rules, f"{rule} not reported: {sorted(rules)}"
        assert criterion in rules[rule]["criteria"], (rule, rules[rule]["criteria"])
    assert rules["image-alt"]["targets"] == ["img"] and rules["image-alt"]["help_url"].startswith("https://")
    assert "accessibility issue(s)" in t["message"]


def test_report_has_accessibility_section(default_run: CliRun) -> None:
    html = (default_run.run_dir / "summary.html").read_text(encoding="utf-8")
    assert "Accessibility: WCAG 2.1 AA (EN 301 549)" in html
    assert "on 1 of 2 page(s)" in html and "Images must have alternative text" in html
    assert "WCAG 1.1.1" in html and "Still needs a person to check" in html and "WCAG-EM" in html


def test_every_finding_on_the_broken_page_comes_with_a_working_fix(default_run: CliRun) -> None:
    from ui_automation.accessibility.fixes import _rgb, contrast

    t = default_run.test("test_page_is_accessible[/a11y-bad")
    fixes = {v["rule"]: v["fixes"][0] for v in t["accessibility"][0]["violations"]}
    assert fixes["image-alt"]["fix"].startswith("<img src=\"data:image/gif;base64,") and fixes["image-alt"]["fix"].endswith('alt="Describe this image">')
    assert fixes["label"]["fix"] == '<input type="text" name="email" aria-label="Email">'
    assert fixes["html-has-lang"]["fix"] == '<html lang="en">'
    assert 'aria-label="' in fixes["button-name"]["fix"] and 'aria-label="' in fixes["link-name"]["fix"]
    css = fixes["color-contrast"]["fix"]
    new = css.split("color: ")[1].split(";")[0]
    assert contrast(_rgb(new), _rgb("#ffffff")) >= 4.5, css  # the page's grey #bbb on white, measured by axe


def test_report_shows_fixes_with_copy_buttons(default_run: CliRun) -> None:
    html = (default_run.run_dir / "summary.html").read_text(encoding="utf-8")
    assert html.count("Suggested fix") >= 6 and html.count('class="copy"') >= 6
    assert "&lt;input type=&quot;text&quot; name=&quot;email&quot; aria-label=&quot;Email&quot;&gt;" in html


def test_report_only_mode_records_but_does_not_fail(tmp_path: Path, site) -> None:
    run = run_a11y(tmp_path, site, fail_on="none")
    assert run.returncode == 0, run.output
    bad = run.test("test_page_is_accessible[/a11y-bad")
    assert bad["outcome"] == "passed" and len(bad["accessibility"][0]["violations"]) >= 6


def test_threshold_only_blocks_at_or_above(tmp_path: Path, site) -> None:
    run = run_a11y(tmp_path, site, fail_on="critical")
    bad = run.test("test_page_is_accessible[/a11y-bad")
    impacts = {v["impact"] for v in bad["accessibility"][0]["violations"]}
    assert bad["outcome"] == ("failed" if "critical" in impacts else "passed")
    assert "[serious]" not in bad["message"]


def test_wcag22_standard(tmp_path: Path, site) -> None:
    run = run_a11y(tmp_path, site, standard="wcag22aa")
    assert run.test("test_page_is_accessible[/a11y-bad")["accessibility"][0]["standard"] == "wcag22aa"
    assert "WCAG 2.2 AA" in (run.run_dir / "summary.html").read_text(encoding="utf-8")


def test_disabled(tmp_path: Path, site) -> None:
    run = run_a11y(tmp_path, site, enabled=False)
    s = run.summary
    assert run.returncode == 0 and s["skipped"] == 1 and s["failed"] == 0


@pytest.mark.parametrize(
    "raw, match",
    [({"standard": "wcag3"}, "standard"), ({"fail_on": "sometimes"}, "fail_on"), ({"pages": [1]}, "pages"),
     ({"pages": [""]}, "pages"), ({"enabled": "maybe"}, "accessibility.enabled")],
)
def test_config_validation(tmp_path: Path, raw: dict, match: str) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"base_url": "http://x", "accessibility": raw}))
    with pytest.raises(ValueError, match=match):
        load_settings(p)


def test_config_normalises_pages(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"base_url": "http://x", "accessibility": {"pages": ["contact", "/", "https://a.b/c"]}}))
    assert load_settings(p).accessibility.pages == ("/contact", "/", "https://a.b/c")
