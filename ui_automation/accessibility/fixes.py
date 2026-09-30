"""Concrete, copy-pasteable fixes for axe-core findings.

For each failing element axe gives us its HTML, a CSS selector and (for some rules) measured data such as
the colours behind a contrast failure. From that we write the corrected markup or CSS for that exact element.
Wording that only a person can judge (alt text, labels) is a best guess from the element's own attributes
and is marked for review in the note.
"""

from __future__ import annotations

import re
from typing import Any, Callable

_TAG_RE = re.compile(r"<([a-zA-Z][\w-]*)([^>]*?)(/?)>", re.S)
_ATTR_RE = r'(\s{name}\s*=\s*)("[^"]*"|\'[^\']*\'|[^\s>]+)'

REVIEW = "Check the wording: it should say what this is for, in your site's language."


def _attr(html: str, name: str) -> str:
    m = _TAG_RE.match(html.strip())
    if not m:
        return ""
    found = re.search(_ATTR_RE.format(name=re.escape(name)), m.group(2), re.I)
    return found.group(2).strip("\"'") if found else ""


def _tag(html: str) -> str:
    m = _TAG_RE.match(html.strip())
    return m.group(1).lower() if m else ""


def _opening(html: str) -> str:
    m = _TAG_RE.match(html.strip())
    tag = m.group(0) if m else html.strip()
    # An empty style="" does nothing; browsers and test tools add it at run time, so keep it out of the fix.
    return re.sub(r'\s+style=(""|\'\')', "", tag)


def with_attr(html: str, name: str, value: str) -> str:
    """The element's opening tag with `name="value"` set (added, or replacing an existing value)."""
    tag = _opening(html)
    m = _TAG_RE.match(tag)
    if not m:
        return tag
    value = value.replace('"', "&quot;")
    attrs = m.group(2)
    pattern = re.compile(_ATTR_RE.format(name=re.escape(name)), re.I)
    if pattern.search(attrs):
        attrs = pattern.sub(lambda a: f'{a.group(1)}"{value}"', attrs, count=1)
    else:
        attrs = f'{attrs.rstrip()} {name}="{value}"'
    return f"<{m.group(1)}{attrs}{' /' if m.group(3) else ''}>"


def _words(text: str) -> str:
    """'txtDate' / 'single-file_input' / 'logo.png' -> 'Date' / 'Single file input' / 'Logo'."""
    text = re.sub(r"\.(png|jpe?g|gif|svg|webp|avif|ico)$", "", text.rsplit("/", 1)[-1].split("?")[0], flags=re.I)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[_\-.#]+", " ", text).strip()
    text = re.sub(r"^(txt|inp|input|btn|ddl|chk|lbl|img|ico|icon|fld)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(btn|button|link|icon|img|image)$", "", text, flags=re.I)
    text = " ".join(text.split()).lower()
    return text[:1].upper() + text[1:] if text else ""


def _guess_name(html: str, fallback: str) -> str:
    for attr in ("placeholder", "title", "value", "name", "id", "src", "href", "class"):
        raw = _attr(html, attr)
        if raw.startswith(("data:", "blob:")):  # embedded content: no name to borrow
            continue
        if attr == "href" and (not raw or raw.startswith(("#", "javascript:"))):
            continue
        if attr == "href" and "://" in raw:
            host = [p for p in raw.split("/")[2].split(":")[0].split(".") if p not in ("www", "en", "m")]
            raw = max(host[:-1] or host, key=len) if host else ""
        if attr == "class":
            raw = raw.split()[0] if raw.split() else ""
        words = _words(raw)
        if words and not words.isdigit():
            return words
    return fallback


# ------------------------------------------------------------------------------------------------ colours


def _rgb(color: str) -> tuple[float, float, float] | None:
    color = color.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", color):
        color = "#" + "".join(c * 2 for c in color[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", color):
        return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]
    m = re.fullmatch(r"rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+).*\)", color)
    if m:
        return tuple(float(x) / 255 for x in m.groups())  # type: ignore[return-value]
    return None


def _luminance(rgb: tuple[float, float, float]) -> float:
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def accessible_color(fg: str, bg: str, ratio: float) -> str | None:
    """The colour closest to `fg` (same hue, darker or lighter) that reaches `ratio` against `bg`."""
    f, b = _rgb(fg), _rgb(bg)
    if not f or not b:
        return None
    target = (0.0, 0.0, 0.0) if _luminance(b) > 0.18 else (1.0, 1.0, 1.0)
    if contrast(target, b) < ratio:  # the other direction is the only way
        target = (1.0, 1.0, 1.0) if target == (0.0, 0.0, 0.0) else (0.0, 0.0, 0.0)
    for step in range(1, 101):
        t = step / 100
        candidate = tuple(round((fc + (tc - fc) * t) * 255) / 255 for fc, tc in zip(f, target))
        if contrast(candidate, b) >= ratio:  # type: ignore[arg-type]
            return _hex(candidate)  # type: ignore[arg-type]
    return None


# ------------------------------------------------------------------------------------------------ rules


def _check_data(node: dict[str, Any], check: str) -> dict[str, Any]:
    for group in ("any", "all", "none"):
        for c in node.get(group) or []:
            if c.get("id") == check and isinstance(c.get("data"), dict):
                return c["data"]
    return {}


def _alt(node: dict[str, Any], sel: str) -> tuple[str, str]:
    html = node.get("html", "")
    name = _guess_name(html, "Describe this image")
    return with_attr(html, "alt", name), (
        f'{REVIEW} If the image is only decoration, use alt="" instead.')


def _label(node: dict[str, Any], sel: str) -> tuple[str, str]:
    html = node.get("html", "")
    name = _guess_name(html, "Describe this field")
    el = _opening(html)
    if _attr(html, "id") and _attr(html, "type").lower() != "hidden":
        return f'<label for="{_attr(html, "id")}">{name}</label>\n{el}', (
            f"A visible label helps everyone. {REVIEW}")
    return with_attr(html, "aria-label", name), f"No id to point a <label> at, so this names it directly. {REVIEW}"


def _aria_label(fallback: str) -> Callable[[dict[str, Any], str], tuple[str, str]]:
    def fix(node: dict[str, Any], sel: str) -> tuple[str, str]:
        html = node.get("html", "")
        return with_attr(html, "aria-label", _guess_name(html, fallback)), (
            f"Visible text inside the element works too. {REVIEW}")
    return fix


def _title_attr(node: dict[str, Any], sel: str) -> tuple[str, str]:
    html = node.get("html", "")
    return with_attr(html, "title", _guess_name(html, "Describe this frame")), REVIEW


def _contrast(node: dict[str, Any], sel: str) -> tuple[str, str]:
    data = _check_data(node, "color-contrast") or _check_data(node, "color-contrast-enhanced")
    fg, bg = data.get("fgColor", ""), data.get("bgColor", "")
    try:
        needed = float(str(data.get("expectedContrastRatio", "4.5")).split(":")[0])
    except ValueError:
        needed = 4.5
    new = accessible_color(fg, bg, needed + 0.05) if fg and bg else None
    if not new:
        return "", ("The colours could not be measured (e.g. text over an image or gradient). "
                    f"Give the text a solid background, or a colour with at least {needed:g}:1 contrast.")
    now = data.get("contrastRatio")
    return (f"{sel} {{\n  color: {new}; /* was {fg}: {now}:1 on {bg}, now {contrast(_rgb(new), _rgb(bg)):.2f}:1 */\n}}",
            f"Needs {needed:g}:1. The nearest passing shade of the same colour; any darker/lighter brand colour "
            "with the same contrast works too.")


def _underline(node: dict[str, Any], sel: str) -> tuple[str, str]:
    return (f"{sel} {{\n  text-decoration: underline;\n}}",
            "Links inside text need more than colour to stand out; an underline is the usual fix.")


def _lang(node: dict[str, Any], sel: str) -> tuple[str, str]:
    return with_attr(node.get("html", "<html>"), "lang", "en"), "Use your page's language code (e.g. en-IE, ga)."


def _scrollable(node: dict[str, Any], sel: str) -> tuple[str, str]:
    return with_attr(node.get("html", ""), "tabindex", "0"), "Lets keyboard users focus and scroll this area."


def _viewport(node: dict[str, Any], sel: str) -> tuple[str, str]:
    return ('<meta name="viewport" content="width=device-width, initial-scale=1">',
            "Removes maximum-scale / user-scalable=no so people can zoom.")


def _target_size(node: dict[str, Any], sel: str) -> tuple[str, str]:
    return (f"{sel} {{\n  min-width: 24px;\n  min-height: 24px;\n}}",
            "WCAG 2.5.8: touch targets at least 24 by 24 CSS pixels (or enough space around them).")


def _title(node: dict[str, Any], sel: str) -> tuple[str, str]:
    return "<title>Page name – Site name</title>", "Put it inside <head>; make it unique for each page."


def _duplicate_id(node: dict[str, Any], sel: str) -> tuple[str, str]:
    html = node.get("html", "")
    old = _attr(html, "id")
    return with_attr(html, "id", f"{old}-2" if old else "unique-id"), (
        "Every id on a page must be unique; update anything that points at it (label for=, aria-*).")


_FIXERS: dict[str, Callable[[dict[str, Any], str], tuple[str, str]]] = {
    "image-alt": _alt,
    "input-image-alt": _alt,
    "area-alt": _alt,
    "role-img-alt": _aria_label("Describe this image"),
    "svg-img-alt": _aria_label("Describe this image"),
    "object-alt": _aria_label("Describe this content"),
    "label": _label,
    "select-name": _label,
    "button-name": _aria_label("Describe what this button does"),
    "link-name": _aria_label("Describe where this link goes"),
    "input-button-name": lambda n, s: (with_attr(n.get("html", ""), "value", _guess_name(n.get("html", ""), "Submit")), REVIEW),
    "aria-input-field-name": _aria_label("Describe this field"),
    "aria-toggle-field-name": _aria_label("Describe this option"),
    "aria-command-name": _aria_label("Describe this action"),
    "aria-meter-name": _aria_label("Describe this meter"),
    "aria-progressbar-name": _aria_label("Describe this progress"),
    "aria-tooltip-name": _aria_label("Describe this tooltip"),
    "aria-dialog-name": _aria_label("Describe this dialog"),
    "frame-title": _title_attr,
    "color-contrast": _contrast,
    "color-contrast-enhanced": _contrast,
    "link-in-text-block": _underline,
    "html-has-lang": _lang,
    "html-lang-valid": _lang,
    "scrollable-region-focusable": _scrollable,
    "meta-viewport": _viewport,
    "target-size": _target_size,
    "document-title": _title,
    "duplicate-id-aria": _duplicate_id,
    "duplicate-id-active": _duplicate_id,
}


def suggest(rule: str, node: dict[str, Any]) -> dict[str, str]:
    """{'target', 'html', 'fix', 'note'} for one failing element; 'fix' is '' when there is no safe rewrite."""
    target = " ".join(map(str, node.get("target", [])))
    fixer = _FIXERS.get(rule)
    fix, note = ("", "")
    if fixer:
        try:
            fix, note = fixer(node, target)
        except Exception:  # noqa: BLE001 - a fix suggestion must never break the scan
            fix, note = "", ""
    if not fix:
        summary = (node.get("failureSummary") or "").strip()
        note = note or summary.replace("\n  ", "\n• ")
    return {"target": target, "html": (node.get("html") or "")[:300], "fix": fix, "note": note}
