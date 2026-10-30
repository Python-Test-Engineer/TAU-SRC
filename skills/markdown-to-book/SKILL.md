---
name: markdown-to-book
description: Convert a Markdown file into a self-contained styled HTML book page — fixed sidebar TOC with filter and scroll-spy, reading progress bar, fixed top controls bar (page zoom + an appearance panel that edits type, layout, fonts, and colours), copy buttons on code blocks, and print support. Use when asked to turn a .md document into a readable single-file web page or "book", or to restyle other documents like EXPLAINER_EVENTS.html.
---

# Markdown → Book

Turn any Markdown file into one self-contained HTML book page — no external
CSS, JS, or fonts — in the style of `EXPLAINERS/EXPLAINER_EVENTS.html` (which
matches the nanocode `book.html` feature set).

## When to use

- The user asks to convert or build a web page from a `.md` file (docs, READMEs, dev notes, blogs).
- The user wants a document restyled "like the tau events explainer" or "like book.html".
- Producing a polished, printable, single-file artifact from Markdown.

## How to run

Run the converter with Python. Inside the tau repo use `uv run python`; outside
it, `python3` works (the converter is pure standard library):

```bash
uv run python "skills/markdown-to-book/scripts/md_to_book.py" "path/to/input.md" [options]
```

Options:

| Flag | Meaning | Default |
|---|---|---|
| `-o, --output` | output `.html` path | beside the source, same stem |
| `--title` | page `<title>` and settings-store key | first `#` heading, else file name |
| `--description` | meta description | none |
| `--zoom` | initial page zoom (0.5–5) | 1.5 |
| `--mode` | initial colour mode: `auto` / `light` / `dark` | `auto` |
| `--no-footer` | omit the "Generated from …" footer | footer shown |

Skips are unexpected: the script throws if a `{{...}}` placeholder was not
filled, so a missing source or template produces a clear error instead of a
broken page.

## What the generated page includes (all client-side, no dependencies)

- Fixed left sidebar TOC with search filter; scroll-spy keeps the active section visible in the sidebar.
- Reading progress bar along the top.
- Fixed top controls bar: page zoom (`−` / value / `+`, presets to 1×–5×) and the `Aa` appearance button.
- Appearance panel: Auto/Light/Dark; sliders for book text size, line heights, sidebar width, reading column width; font stacks (sans/serif/mono); per-mode colour swatches. Changes persist to localStorage (key derived from the title); Reset and Copy TOML actions.
- Copy buttons on every code block, collapsible sections, print styles (chrome hidden, details opened).

## Markdown support

- ATX headings `#`–`######` (the book heading stays as the page title; the TOC is built from `h2` + `h3` automatically, so use `h2` for sections and `h3` for subsections).
- Fenced code blocks: `python`/`py`, `bash`/`sh`/`shell`, `json`, `js`/`ts` get client-side syntax highlighting; any other language renders plain but still copies.
- GFM tables, bullet and numbered lists (nested), blockquotes (rendered as callouts), horizontal rules, and inline code / bold / italic / links / images.

## Workflow

1. Confirm or choose the input Markdown file; check the output path a sensible default (`.html` beside the source).
2. Run the converter and capture its output line (`wrote <path> (N bytes)`).
3. Verify: open the generated file or grep it — it must contain no un-replaced `{{` sequences and should show the fixed controls bar.
4. Tell the user where the page was written and what is customisable via `Aa`.

If the page needs a different default theme, edit `assets/template.html` (CSS custom properties in `:root` and the `DEFAULTS` object in the appearance-panel script) rather than the generated output.
