# How the Interactive UI Switch Works in Tau

An explainer for Tau's two interactive frontends and the setting that chooses between them.

Tau now supports:

```text
textual  → the full-featured Textual TUI
rich     → a smaller Rich-based scrollback REPL
```

The switch applies only to interactive mode. Print mode, JSON output, transcript output, RPC, session export, and the one-shot CLI commands are unchanged.

## The short version

Set the UI in `~/.tau/settings.json`:

```json
{
  "ui": "rich"
}
```

Or choose it for one launch:

```bash
tau --ui rich
```

Or via the environment:

```bash
TAU_UI=rich tau
```

Precedence is:

```text
1. --ui textual|rich
2. TAU_UI
3. ~/.tau/settings.json  {"ui": "..."}
4. default: textual
```

`textual` is the default so existing users see no change after upgrading.

## Why the setting lives in `settings.json`

Tau has several user-level JSON files:

```text
~/.tau/settings.json      general settings, shell settings, project-trust default
~/.tau/tui.json           Textual-specific settings (theme, keybindings, sidebar)
~/.tau/providers.json     provider catalog and defaults
~/.tau/credentials.json   stored credentials
```

The UI choice cannot live in `tui.json` because that file is loaded *inside* the Textual app. Tau must know whether to start the Textual app at all before `tui.json` is relevant. `settings.json` is the general, frontend-neutral location, so the Rich/Textual choice belongs there.

The existing shell-settings parser ignores unknown fields. That means the new `"ui"` field can coexist in `settings.json` without changing shell behavior.

## Where the switch is implemented

### 1. The config parser — `src/tau_coding/ui_config.py`

This module owns:

- the `UiMode` type: `"textual" | "rich"`
- `UiSettings`
- loading and validating `~/.tau/settings.json`
- resolving CLI, environment, and file precedence

The file format is intentionally small:

```json
{
  "ui": "rich"
}
```

An invalid value is a startup error, not a silent fallback:

```text
ui must be 'textual' or 'rich'
```

Unknown fields in `settings.json` are ignored so newer Tau versions can add settings without breaking older installations.

### 2. The CLI option — `src/tau_coding/cli.py`

The interactive branch now accepts:

```bash
tau --ui textual
tau --ui rich
```

Before starting a frontend, the CLI resolves the effective UI:

```text
resolve_ui_mode("--ui value")
```

Then it dispatches to one of two runners:

```text
ui_mode == "textual" → run_openai_tui()
ui_mode == "rich"    → run_openai_rich()
```

Both runners receive the same session-facing arguments: model, cwd, session id, new-session flag, provider, compaction threshold, initial prompt, startup notice, extension paths, system-prompt overrides, and trust override.

The Rich runner lazily imports `tau_coding.rich_ui`, so the Rich path does not need to construct the Textual app.

### 3. The Rich frontend — `src/tau_coding/rich_ui.py`

The Rich frontend is deliberately a **scrollback REPL**, not a full-screen layout.

```text
terminal scrollback
  ├── user message panels
  ├── tool call lines
  ├── tool result previews
  ├── final assistant Markdown
  └── status/error/retry notices

prompt line
  ├── white horizontal rule above
  ├── TAU> input prompt
  ├── white horizontal rule below
  └── session footer:
        working directory (git branch)
        provider:model (thinking)        used/limit context
```

It uses Rich for:

- `Markdown` rendering of final assistant messages
- `Panel` rendering of user messages
- colored tool-call and tool-result status lines
- a `Status` spinner while waiting for or streaming model output
- red error and retry messages
- compaction status

Prompt input uses `prompt_toolkit` so typing `/` opens a live completion menu.
The menu is built from the session's command registry, including aliases and
extension-registered commands.

It does **not** attempt to reproduce:

- Textual modals and pickers
- a sidebar pane
- mouse-driven scrolling
- Textual text selection behavior
- extension widget hosting
- full-screen pane layout

Those remain Textual-only features.

## What a Rich session does

At startup, `run_rich_ui()`:

1. Loads provider settings and shell settings.
2. Resolves the session record, either by resuming `--session` or creating a new one.
3. Resolves the provider/model selection.
4. Builds the runtime provider.
5. Prepares a normal `CodingSession` through `prepare_coding_session()`.
6. Adopts the session, installs the non-Textual UI bridge, and emits `session_start`.
7. Prints startup notices.
8. Enters a read/execute/render loop until EOF, `/exit`, `/quit`, or a command that requests exit.

A turn is:

```text
read prompt
  ↓
handle local command or terminal command?
  ↓ no
session.prompt(text)
  ↓
render each CodingSessionEvent
  ↓
return to prompt
```

The session itself is the same `CodingSession` used by Textual and print mode. The Rich frontend does not bypass the harness, the event contract, persistence, extensions, or compaction.

## Commands and limitations in Rich mode

Rich mode handles the commands that make sense in a line-oriented UI:

- terminal commands such as `!ls`
- `/exit` and `/quit`
- `/clear`
- `/reload`
- `/model` with a numbered provider/model picker
- `/model provider:model`
- `/thinking <level>`
- `/scoped-models` lists the scoped models
- session renaming
- commands that return a plain message

Command names and aliases autocomplete while typing. For example, typing `/mo`
shows `/model` with its description; pressing Tab or Enter accepts the highlighted
completion.

Commands that still require a Textual screen — such as login/logout, resume/tree pickers,
theme selection, local-backend setup, the sidebar toggle, and extension widgets — print:

```text
This command is only available in the Textual UI.
```

Use Textual for login if Rich mode has no configured provider. Rich mode intentionally does not implement an OAuth/login UI.

## Extension behavior

Extensions still load in Rich mode and still receive session events. What changes is the UI bridge:

```text
Textual mode → Textual UI bridge, widget hosting available
Rich mode    → StderrUiBridge, widget hosting unavailable
```

This is the same fallback used by print mode. Extensions that only emit notifications or use string/markup transcript content keep working. Extensions that mount Textual widgets must use Textual mode.

## Tests

The UI-selection behavior is covered by:

```text
tests/test_ui_config.py
```

These tests verify:

- the default is `textual`
- `{"ui": "rich"}` is read from `settings.json`
- unknown UI values are rejected
- `--ui` beats `TAU_UI`
- `TAU_UI` beats the settings file
- invalid environment values are rejected

CLI dispatch tests in `tests/test_cli.py` verify:

- no setting starts the Textual runner
- `{"ui": "rich"}` starts the Rich runner
- `--ui textual` overrides `TAU_UI=rich` and the settings file
- `--ui web` exits with a usage error

Useful commands:

```bash
uv run pytest tests/test_ui_config.py -q
uv run pytest tests/test_cli.py -q -k ui
```

## Design boundary

The existing architecture rule still holds:

```text
AgentHarness = reusable agent brain
CodingSession = coding-agent environment
TUI = one possible frontend
```

Rich mode is a second interactive frontend over the same `CodingSession` and the same `CodingSessionEvent` stream. It is intentionally smaller than the Textual TUI. The switch chooses the presentation layer; it does not fork the agent, session, provider, resource, or persistence layers.

## Files added or changed

```text
src/tau_coding/ui_config.py     new: durable UI choice and precedence
src/tau_coding/rich_ui.py       new: Rich scrollback REPL
src/tau_coding/cli.py           --ui option and frontend dispatch
tests/test_ui_config.py         new: config and precedence tests
tests/test_cli.py               dispatch and override tests
EXPLAINERS/explainer-ui.md      this document
```
