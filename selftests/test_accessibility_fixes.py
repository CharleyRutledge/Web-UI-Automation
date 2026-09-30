"""The suggested fixes: each one must be real, working code for that exact element."""

from __future__ import annotations

import pytest

from ui_automation.accessibility.fixes import _rgb, accessible_color, contrast, suggest, with_attr


def fix(rule: str, html: str, target: str = "x", **node) -> dict[str, str]:
    return suggest(rule, {"html": html, "target": [target], **node})


@pytest.mark.parametrize(
    "html, name, value, expected",
    [
        ('<img src="a.png">', "alt", "A", '<img src="a.png" alt="A">'),
        ('<img src="a.png" alt>', "alt", "A", '<img src="a.png" alt alt="A">'),
        ('<img src="a.png" ALT="old">', "alt", "New", '<img src="a.png" ALT="New">'),
        ("<input type=text id=q />", "aria-label", "Search", '<input type=text id=q aria-label="Search" />'),
        ('<a href="#">text</a>', "aria-label", 'Say "hi"', '<a href="#" aria-label="Say &quot;hi&quot;">'),
    ],
)
def test_with_attr(html: str, name: str, value: str, expected: str) -> None:
    assert with_attr(html, name, value) == expected


def test_image_alt_is_guessed_from_the_file_name() -> None:
    f = fix("image-alt", '<img src="/static/images/company-logo.png?v=3" class="hero">', "img")
    assert f["fix"] == '<img src="/static/images/company-logo.png?v=3" class="hero" alt="Company logo">'
    assert 'alt=""' in f["note"], "tells the reader what to do for decorative images"


def test_embedded_image_gets_a_placeholder_not_base64() -> None:
    f = fix("image-alt", '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">', "img")
    assert f["fix"].endswith('alt="Describe this image">')


def test_label_points_at_the_field_by_id() -> None:
    f = fix("label", '<input type="text" id="txtDate" class="form-control">', "#txtDate")
    assert f["fix"] == '<label for="txtDate">Date</label>\n<input type="text" id="txtDate" class="form-control">'


def test_label_without_id_uses_aria_label() -> None:
    assert fix("label", '<input type="email" name="userEmail">')["fix"] == \
        '<input type="email" name="userEmail" aria-label="User email">'


@pytest.mark.parametrize(
    "rule, html, expected",
    [
        ("button-name", '<button class="close-btn"></button>', 'aria-label="Close"'),
        ("link-name", '<a href="https://en.wikipedia.org/wiki/Test" class="wiki"></a>', 'aria-label="Wikipedia"'),
        ("link-name", '<a href="#"></a>', 'aria-label="Describe where this link goes"'),
        ("frame-title", '<iframe src="https://www.youtube.com/embed/x"></iframe>', 'title="X"'),
        ("html-has-lang", '<html class="no-js">', '<html class="no-js" lang="en">'),
        ("scrollable-region-focusable", '<div class="table-wrap">', 'tabindex="0"'),
    ],
)
def test_naming_rules(rule: str, html: str, expected: str) -> None:
    assert expected in fix(rule, html)["fix"]


@pytest.mark.parametrize(
    "fg, bg, needed",
    [("#bbbbbb", "#ffffff", 4.5), ("#ff7777", "#ffffff", 4.5), ("#555555", "#222222", 4.5),
     ("#777777", "#ffffff", 7.0), ("#1d4ed8", "#0b1020", 4.5), ("rgb(150, 150, 150)", "rgb(255, 255, 255)", 3.0)],
)
def test_contrast_fix_really_passes(fg: str, bg: str, needed: float) -> None:
    f = fix("color-contrast", "<p>", ".muted", any=[{"id": "color-contrast", "data": {
        "fgColor": fg, "bgColor": bg, "contrastRatio": round(contrast(_rgb(fg), _rgb(bg)), 2),
        "expectedContrastRatio": f"{needed}:1"}}])
    new = f["fix"].split("color: ")[1].split(";")[0]
    assert f["fix"].startswith(".muted {\n  color: #")
    assert contrast(_rgb(new), _rgb(bg)) >= needed, (new, contrast(_rgb(new), _rgb(bg)))


def test_contrast_fix_is_the_nearest_shade() -> None:
    new = accessible_color("#bbbbbb", "#ffffff", 4.55)
    assert contrast(_rgb(new), _rgb("#ffffff")) < 4.9, "not needlessly darker than required"


def test_unmeasurable_contrast_explains_instead_of_guessing() -> None:
    f = fix("color-contrast", "<p>", ".on-photo", any=[{"id": "color-contrast", "data": {"expectedContrastRatio": "4.5:1"}}])
    assert f["fix"] == "" and "solid background" in f["note"]


def test_link_in_text_block_is_underlined() -> None:
    assert fix("link-in-text-block", "<a href=/x>", ".feed-link")["fix"] == ".feed-link {\n  text-decoration: underline;\n}"


def test_rules_without_a_safe_rewrite_fall_back_to_axes_advice() -> None:
    f = fix("list", "<ul><div>x</div></ul>", "ul",
            failureSummary="Fix all of the following:\n  List element has direct children that are not allowed: div")
    assert f["fix"] == "" and "• List element has direct children" in f["note"] and f["html"].startswith("<ul>")


def test_broken_markup_never_breaks_the_scan() -> None:
    for html in ("", "no tag at all", "<", "<img src='", "<<>>", "<img " + "a" * 5000):
        for rule in ("image-alt", "label", "button-name", "color-contrast", "html-has-lang"):
            assert isinstance(suggest(rule, {"html": html, "target": ["x"]}), dict)
