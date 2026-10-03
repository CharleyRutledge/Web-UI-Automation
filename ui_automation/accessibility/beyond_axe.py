"""WCAG checks that axe-core does not do on its own, run in the page as a person would use it.

- 2.1.1 Keyboard: things that react to the mouse (pointer cursor) but cannot be reached with Tab
- 2.1.2 No keyboard trap: Tab stops moving on, or keeps cycling through a few elements
- 2.4.7 Focus visible: an element gets keyboard focus but nothing about it changes
- 1.1.1 Non-text content: alt text that says nothing ("image", a file name, the same as the file name)
- 1.2.2 Captions: videos without a captions / subtitles track
- 1.4.12 Text spacing: text cut off when line height, letter, word and paragraph spacing are increased
- 2.2.1 Timing adjustable: the page reloads or moves on by itself (meta refresh)
- 2.2.2 Pause, stop, hide: animation that never ends, with no pause or stop button
- 1.3.1 Info and relationships: heading levels that skip (h2 then h4)

Each finding is an accessibility Violation like axe's, so it is grouped, reported and gated the same way.
Still for a person: whether alt text and captions are *accurate*, whether the focus order makes sense,
whether form errors explain the fix, and what screen readers announce.
"""

from __future__ import annotations

from typing import Any

from ui_automation.accessibility import Violation

_UNDERSTANDING = "https://www.w3.org/WAI/WCAG22/Understanding/"

_LABEL_JS = """el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
    + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\\s+/)[0] : '')"""

# The element that has keyboard focus, and whether focusing it changed how it looks.
FOCUS_JS = """() => {
    const el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    const label = """ + _LABEL_JS + """;
    const look = e => { const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow,
        s.borderTopColor, s.borderBottomColor, s.borderBottomWidth, s.backgroundColor, s.color, s.textDecorationLine].join('|'); };
    const s = getComputedStyle(el);
    const outline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
    const focused = look(el);
    el.blur();
    const plain = look(el);
    el.focus({preventScroll: true});
    let path = [], e = el;
    while (e && e !== document.body) { path.unshift(e.tagName + ':' + [...(e.parentElement || document.body).children].indexOf(e)); e = e.parentElement; }
    return {key: path.join('/'), label: label(el) + ' "' + (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 30) + '"',
            visible: outline || focused !== plain};
}"""

COUNT_FOCUSABLE_JS = """() => [...document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex="-1"]), [contenteditable=true]')]
    .filter(e => !e.disabled && e.getClientRects().length).length"""

# Mouse-only controls: a pointer cursor on something that is not focusable and holds nothing focusable.
MOUSE_ONLY_JS = """() => {
    const label = """ + _LABEL_JS + """;
    const focusable = 'a[href], button, input, select, textarea, summary, label, [tabindex], [contenteditable]';
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
        if (out.length >= 5) break;
        if (getComputedStyle(el).cursor !== 'pointer' || el.closest(focusable) || el.querySelector(focusable)) continue;
        const p = el.parentElement;
        if (p && p !== document.body && getComputedStyle(p).cursor === 'pointer') continue;  // report the outermost only
        const r = el.getBoundingClientRect();
        if (r.width && r.height) out.push(label(el) + ' "' + (el.innerText || '').trim().slice(0, 30) + '"');
    }
    return out;
}"""

PAGE_JS = """() => {
    const label = """ + _LABEL_JS + """;
    const shown = el => el.getClientRects().length > 0;
    const vague = /^(image|img|picture|photo|graphic|icon|banner|untitled|spacer|alt|null|undefined|-|\\s*)$/i;
    const alt = [];
    for (const img of document.querySelectorAll('img[alt]')) {
        const a = img.getAttribute('alt').trim(), file = (img.getAttribute('src') || '').split(/[?#]/)[0].split('/').pop();
        if (!a || !shown(img)) continue;  // alt="" is right for decoration
        if (vague.test(a) || /\\.(png|jpe?g|gif|svg|webp|avif)$/i.test(a) || (file && a === file) || a.length > 150)
            alt.push(label(img) + ' alt="' + a.slice(0, 60) + '"');
    }
    const captions = [...document.querySelectorAll('video')].filter(v => shown(v)
        && !v.querySelector('track[kind=captions], track[kind=subtitles]')).map(v => label(v) + ' ' + (v.currentSrc || v.src || '').split('/').pop());
    const refresh = document.querySelector('meta[http-equiv="refresh" i]');
    const moving = (document.getAnimations ? document.getAnimations() : []).filter(a => a.playState === 'running'
        && a.effect && a.effect.getComputedTiming().iterations === Infinity && a.effect.target && shown(a.effect.target))
        .map(a => label(a.effect.target));
    moving.push(...[...document.querySelectorAll('marquee')].map(label));
    const pause = [...document.querySelectorAll('button, [role=button], a')].some(b => /pause|stop/i.test(
        (b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')));
    const headings = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6')].filter(shown);
    const skips = [];
    headings.forEach((h, i) => { const lvl = +h.tagName[1], prev = i ? +headings[i - 1].tagName[1] : 0;
        if (prev && lvl > prev + 1) skips.push('h' + prev + ' then ' + label(h) + ' "' + h.innerText.trim().slice(0, 30) + '"'); });
    return {alt: alt.slice(0, 5), captions: captions.slice(0, 5), refresh: refresh ? refresh.getAttribute('content') : '',
            moving: pause ? [] : [...new Set(moving)].slice(0, 5), skips: skips.slice(0, 5)};
}"""

# WCAG 1.4.12: with this spacing applied, no text may be cut off. Applied as a constructed stylesheet
# (document.adoptedStyleSheets), which a Content-Security-Policy allows, unlike an added <style> tag, and which
# is removed again afterwards without touching any element.
SPACING_JS = """() => {
    const label = """ + _LABEL_JS + """;
    const sheet = new CSSStyleSheet();
    sheet.replaceSync('* { line-height: 1.5 !important; letter-spacing: 0.12em !important; '
                      + 'word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }');
    const before = document.adoptedStyleSheets;
    document.adoptedStyleSheets = [...before, sheet];
    const out = [];
    try {
        for (const el of document.querySelectorAll('body *')) {
            if (out.length >= 5) break;
            const s = getComputedStyle(el);
            const hides = ['hidden', 'clip'].includes(s.overflowY) || ['hidden', 'clip'].includes(s.overflowX);
            const text = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 2);
            if (hides && text && el.getClientRects().length
                && (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2))
                out.push(label(el) + ' "' + (el.innerText || '').trim().slice(0, 30) + '"');
        }
    } finally {
        document.adoptedStyleSheets = before;
    }
    return out;
}"""


def _violation(rule: str, impact: str, help_: str, criterion: str, slug: str, targets: list[str], note: str) -> Violation:
    return Violation(rule=rule, impact=impact, help=help_, help_url=_UNDERSTANDING + slug, criteria=[criterion],
                     targets=targets[:5], count=len(targets),
                     fixes=[{"target": t, "html": "", "fix": "", "note": note} for t in targets[:3]])


def keyboard(page: Any, max_stops: int = 40) -> list[Violation]:
    """Tab through the page like a keyboard user: focus visible at every stop, no trap, nothing mouse-only."""
    found: list[Violation] = []
    page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); }")
    total = page.evaluate(COUNT_FOCUSABLE_JS)
    seen: list[str] = []
    invisible: list[str] = []
    trapped = ""
    for _ in range(min(total + 2, max_stops)):
        page.keyboard.press("Tab")
        info = page.evaluate(FOCUS_JS)
        if info is None:
            if seen:
                break  # focus left the page: the end was reached
            continue
        if not info["visible"] and info["label"] not in invisible:
            invisible.append(info["label"])
        recent = seen[-6:]
        if info["key"] in recent and len(set(seen)) < total and len(seen) >= 3:
            trapped = info["label"]  # back to an element seen a few stops ago, with others never reached
            break
        seen.append(info["key"])
    if trapped:
        found.append(_violation("keyboard-trap", "critical", "Keyboard focus gets stuck", "2.1.2", "no-keyboard-trap",
                                [trapped], "Tab keeps returning here, so the rest of the page cannot be reached "
                                           "with a keyboard. Let Tab (and Shift+Tab, Esc for dialogs) move on."))
    if invisible:
        found.append(_violation("focus-visible", "serious", "No visible focus indicator", "2.4.7", "focus-visible",
                                invisible, "Nothing shows which element has keyboard focus. Add a style such as "
                                           ":focus-visible { outline: 3px solid #4c7cf0; outline-offset: 2px; }"))
    mouse_only = page.evaluate(MOUSE_ONLY_JS)
    if mouse_only:
        found.append(_violation("mouse-only", "serious", "Works with a mouse but not a keyboard", "2.1.1", "keyboard",
                                mouse_only, "This reacts to clicks but cannot be reached with Tab. Use a <button> "
                                            "or <a href>, or add tabindex=\"0\" and Enter / Space handling."))
    return found


def page_checks(page: Any) -> list[Violation]:
    """Alt text quality, captions, moving content, automatic refresh and heading levels, as the page is now."""
    r = page.evaluate(PAGE_JS)
    found: list[Violation] = []
    if r["alt"]:
        found.append(_violation("alt-meaningless", "moderate", "Alt text that says nothing about the image", "1.1.1",
                                "non-text-content", r["alt"],
                                "Describe what the image shows or does (e.g. alt=\"Bar chart of deposits by month\"), "
                                "or use alt=\"\" if it is only decoration."))
    if r["captions"]:
        found.append(_violation("video-captions", "serious", "Video without captions", "1.2.2", "captions-prerecorded",
                                r["captions"], "Add <track kind=\"captions\" src=\"captions.vtt\" srclang=\"en\"> "
                                               "inside the <video>."))
    if r["refresh"]:
        found.append(_violation("auto-refresh", "serious", "The page reloads or moves on by itself", "2.2.1",
                                "timing-adjustable", [f'meta http-equiv="refresh" content="{r["refresh"]}"'],
                                "Remove the automatic refresh, or let people turn it off or extend it."))
    if r["moving"]:
        found.append(_violation("endless-motion", "serious", "Animation that never stops, with no pause button",
                                "2.2.2", "pause-stop-hide", r["moving"],
                                "Add a pause / stop button, stop it after 5 seconds, or honour "
                                "@media (prefers-reduced-motion: reduce)."))
    if r["skips"]:
        found.append(_violation("heading-skip", "moderate", "Heading levels skip", "1.3.1", "info-and-relationships",
                                r["skips"], "Use heading levels in order (h2 after h1, h3 after h2) so screen reader "
                                            "users can follow the page's structure."))
    return found


def text_spacing(page: Any) -> list[Violation]:
    """WCAG 1.4.12: apply the larger spacing some readers need, and look for text that gets cut off."""
    clipped = page.evaluate(SPACING_JS)
    if not clipped:
        return []
    return [_violation("text-spacing", "serious", "Text is cut off when spacing is increased", "1.4.12",
                       "text-spacing", clipped, "Let boxes grow with their text: avoid fixed heights with "
                                                "overflow: hidden on text (use min-height instead).")]


def check(page: Any) -> list[Violation]:
    """Everything above, worst first (keyboard, then the rest, then text spacing, which changes the page last)."""
    return keyboard(page) + page_checks(page) + text_spacing(page)
