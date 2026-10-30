#!/usr/bin/env python3
"""md_to_book.py — convert a Markdown file into a self-contained styled HTML book.

The generated page is a single file with no external CSS/JS/fonts: fixed sidebar
TOC (filter + scroll-spy), reading progress bar, fixed top controls bar (page
zoom + appearance panel editing type/layout/fonts/colours), copy buttons on code
blocks, and print support. The page chrome lives in assets/template.html and is
filled at runtime by the page's own JavaScript; this script only converts the
Markdown body.

Usage:
    python md_to_book.py SOURCE.md [--output OUT.html] [--title T] [--description D]
                                    [--zoom 1.5] [--mode auto|light|dark] [--no-footer]

Supported Markdown: ATX headings (#..######), paragraphs, fenced code blocks
(python/bash/json/js get syntax highlighting; other languages render plain),
GFM tables, bullet and numbered lists (nested), blockquotes (callouts),
horizontal rules, and inline code/links/images/bold/italic.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
from datetime import date
from pathlib import Path

ASSET_TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "template.html"
PLACEHOLDERS = {
    "{{TITLE}}",
    "{{DESCRIPTION}}",
    "{{STORE}}",
    "{{MODE}}",
    "{{ZOOM}}",
    "{{ZOOM_LABEL}}",
    "{{BODY}}",
}


# --------------------------------------------------------------------------
# inline formatting (escapes, then code, images, links, bold, italic)
# --------------------------------------------------------------------------
def _stash_codes(text: str) -> tuple[str, list[str]]:
    codes: list[str] = []

    def stash(m: re.Match[str]) -> str:
        token = "\0%d\0" % len(codes)
        codes.append(m.group(1))
        return token

    return re.sub(r"`([^`]+)`", stash, text), codes


def _unstash_codes(text: str, codes: list[str]) -> str:
    def restore(m: re.Match[str]) -> str:
        return "<code class='inline'>%s</code>" % html.escape(codes[int(m.group(1))], quote=False)

    return re.sub(r"\0(\d+)\0", restore, text)


def _attr(value: str) -> str:
    return html.escape(value, quote=True)


def _inline(text: str) -> str:
    stashed, codes = _stash_codes(text)
    text = html.escape(stashed, quote=False)
    text = _unstash_codes(text, codes)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)",
        lambda m: "<img src='%s' alt='%s'%s>" % (
            _attr(m.group(2)), _attr(m.group(1)),
            ' title="%s"' % _attr(m.group(3)) if m.group(3) else "",
        ),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)",
        lambda m: "<a href='%s'%s>%s</a>" % (
            _attr(m.group(2)),
            ' title="%s"' % _attr(m.group(3)) if m.group(3) else "",
            m.group(1),
        ),
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<em>\1</em>", text)
    return text


# --------------------------------------------------------------------------
# block constructs
# --------------------------------------------------------------------------
_FENCE = re.compile(r"^```([A-Za-z0-9_+.-]*)")
_HR = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")
_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")

_LANG_TO_CLASS = {
    "python": "python", "py": "python",
    "bash": "bash", "sh": "bash", "shell": "bash", "zsh": "bash",
    "json": "json",
    "js": "js", "javascript": "js", "ts": "js", "typescript": "js",
}


def _is_table_start(lines: list[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    if "|" not in lines[idx]:
        return False
    cells = _split_row(lines[idx + 1])
    return (
        "-" in lines[idx + 1]
        and bool(cells)
        and all(re.fullmatch(r":?-+:?", c.strip()) for c in cells)
    )


def _split_row(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [part.replace("\\|", "|") for part in row.split("|")]


def _consume_list(lines: list[str], start: int) -> tuple[str, int]:
    """Parse a (possibly nested) list starting at lines[start]; return (html, next index)."""
    out: list[str] = []
    i = start
    n = len(lines)
    uses_ordered = False
    while i < n:
        m = _LIST_ITEM.match(lines[i])
        if not m:
            break
        indent = len(m.group(1))
        marker = m.group(2)
        uses_ordered = uses_ordered or marker[0].isdigit()
        item_text = _inline(m.group(3))
        i += 1
        extras: list[str] = []
        nested: str | None = None
        while i < n and lines[i].strip():
            m2 = _LIST_ITEM.match(lines[i])
            if m2:
                if len(m2.group(1)) > indent:
                    nested_html, i = _consume_list(lines, i)
                    nested = nested_html
                    continue
                break
            if len(lines[i]) - len(lines[i].lstrip()) > indent:
                extras.append(lines[i].strip())
                i += 1
                continue
            break
        inner = ""
        if extras:
            inner += "<p>%s</p>" % _inline(" ".join(extras))
        if nested:
            inner += nested
        out.append("<li>%s%s</li>" % (item_text, inner))
        if i < n and not lines[i].strip():
            break
    tag = "ol" if uses_ordered else "ul"
    return ("<%s>%s</%s>" % (tag, "".join(out), tag)), i


def _code_block(lang: str, code: str) -> str:
    cls = "lang-" + _LANG_TO_CLASS.get(lang, "text")
    label = _LANG_TO_CLASS.get(lang, lang or "text")
    escaped = html.escape(code)
    return (
        "<div class='codewrap'><span class='copy-btn' data-copy>&#10809; copy</span>"
        "<span class='code-label'>%s</span><pre><code class='%s'>%s</code></pre></div>"
        % (html.escape(label), cls, escaped)
    )


def _table(lines: list[str], start: int) -> tuple[str, int]:
    header = [_inline(c.strip()) for c in _split_row(lines[start])]
    i = start + 2  # skip the delimiter row
    body: list[list[str]] = []
    n = len(lines)
    while i < n and lines[i].strip() and "|" in lines[i]:
        body.append([_inline(c.strip()) for c in _split_row(lines[i].strip())])
        i += 1
    thead = "".join("<th>%s</th>" % c for c in header)
    rows = "".join(
        "<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in row) for row in body
    )
    return (
        "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (thead, rows),
        i,
    )


def md_to_html(document: str) -> str:
    lines = document.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        fence = _FENCE.match(stripped)
        if fence:
            lang = fence.group(1)
            buf: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # skip closing fence (or end)
            out.append(_code_block(lang, "\n".join(buf)))
            continue

        head = _HEAD.match(stripped)
        if head:
            level = len(head.group(1))
            out.append("<h%d>%s</h%d>" % (level, _inline(head.group(2)), level))
            i += 1
            continue

        if _HR.match(stripped):
            out.append("<hr class='rule'>")
            i += 1
            continue

        if _is_table_start(lines, i):
            table_html, i = _table(lines, i)
            out.append(table_html)
            continue

        if stripped.startswith(">"):
            buf = []
            while i < n and _QUOTE.match(lines[i]):
                buf.append(_QUOTE.match(lines[i]).group(1).strip())
                i += 1
            out.append("<div class='callout'>%s</div>" % _inline(" ".join(buf)))
            continue

        if _LIST_ITEM.match(line):
            list_html, i = _consume_list(lines, i)
            out.append(list_html)
            continue

        # paragraph: gather until a line that starts a new block
        buf = [stripped]
        i += 1
        while i < n:
            s = lines[i].strip()
            if not s:
                break
            if (
                s.startswith("#")
                or s.startswith("```")
                or s.startswith(">")
                or _HR.match(s)
                or _LIST_ITEM.match(lines[i])
                or _is_table_start(lines, i)
            ):
                break
            buf.append(s)
            i += 1
        out.append("<p>%s</p>" % _inline(" ".join(buf)))

    return "\n".join(out)


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------
def first_h1(document: str) -> str | None:
    for line in document.splitlines():
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return m.group(1).strip()
    return None


def build_page(
    source: Path,
    body_html: str,
    *,
    title: str,
    description: str,
    zoom: float,
    mode: str,
    footer: bool,
) -> str:
    store = "tau-theme:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
    zoom_label = str(int(zoom)) if zoom == int(zoom) else str(zoom)
    tpl = ASSET_TEMPLATE.read_text(encoding="utf-8")

    replacements = {
        "{{TITLE}}": html.escape(title, quote=True),
        "{{DESCRIPTION}}": html.escape(description, quote=True),
        "{{STORE}}": store,
        "{{MODE}}": mode,
        "{{ZOOM}}": str(zoom),
        "{{ZOOM_LABEL}}": zoom_label + "\u00d7",
        "{{BODY}}": body_html,
    }
    if footer:
        stamp = (
            "<hr style='border-color: var(--line); margin: 40px 0;'>\n"
            "<p class='subtitle' style='margin-top:0;'>Generated from "
            "<code class='inline'>%s</code> by the <em>markdown-to-book</em> "
            "page, %s.</p>" % (html.escape(source.name), date.today().isoformat())
        )
        body_html = body_html + "\n" + stamp

    for key, value in replacements.items():
        tpl = tpl.replace(key, value)

    leftovers = PLACEHOLDERS.intersection(re.findall(r"\{\{[A-Z_]+\}\}", tpl))
    if leftovers:
        raise RuntimeError("placeholder not filled: %s" % sorted(leftovers))

    return tpl


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert a Markdown file into a self-contained styled HTML book page."
    )
    ap.add_argument("source", help="input Markdown file")
    ap.add_argument("-o", "--output", help="output .html path (default: beside the source)")
    ap.add_argument("--title", help="page title (default: first # heading, else file name)")
    ap.add_argument("--description", default="", help="meta description")
    ap.add_argument("--zoom", type=float, default=1.5, help="initial page zoom, 0.5-5 (default 1.5)")
    ap.add_argument("--mode", choices=["auto", "light", "dark"], default="auto", help="initial colour mode")
    ap.add_argument("--no-footer", action="store_true", help="omit the generated-from footer")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.exists():
        raise SystemExit("source not found: %s" % source)
    document = source.read_text(encoding="utf-8")

    heading = first_h1(document)
    title = args.title or heading or source.stem.replace("-", " ").title()
    body_html = md_to_html(document)
    page = build_page(
        source,
        body_html,
        title=title,
        description=args.description,
        zoom=args.zoom,
        mode=args.mode,
        footer=not args.no_footer,
    )

    dest = Path(args.output) if args.output else source.with_suffix(".html")
    dest.write_text(page, encoding="utf-8")
    print("wrote %s (%d bytes)" % (dest, len(page.encode("utf-8"))))


if __name__ == "__main__":
    main()
