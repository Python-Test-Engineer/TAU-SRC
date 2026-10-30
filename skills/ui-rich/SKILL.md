---
name: ui-rich
description: Add a complete Rich-based interactive frontend to a Textual-only Tau checkout while preserving Textual as the default/full TUI. Use when asked to port Tau's TUI to Rich, add a `--ui rich` option, create a repo/home UI-selection setting, implement a Rich scrollback REPL, command autocomplete, a model/context footer, or Rich equivalents of Tau slash commands.
---

# Textual Tau → Rich Tau

Turn a Textual-only Tau checkout into a checkout with two interactive frontends:

```text
textual  → existing full-featured Textual TUI (keep it)
rich     → Rich scrollback REPL with Tau session semantics
```

Do **not** replace or delete Textual. Both frontends must share the same
`CodingSession`, provider setup, session persistence, extensions, and
`CodingSessionEvent` contract.

## Canonical implementation

When available in the same repository, read and reuse these files as the
reference implementation. Resolve paths relative to this skill file:

```text
../../src/tau_coding/ui_config.py
../../src/tau_coding/rich_ui.py
../../src/tau_coding/rich_autocomplete.py
../../src/tau_coding/cli.py
../../tests/test_ui_config.py
../../tests/test_rich_autocomplete.py
../../EXPLAINERS/explainer-ui.md
```

If those files are absent, implement the equivalent behavior described below.
Do not copy Textual imports into the Rich frontend.

## Architecture constraints

```text
tau_agent     portable agent harness; must stay UI-free
tau_coding    coding-session environment and frontends
rich_ui       one interactive frontend; must not import tau_coding.tui
textual TUI   another interactive frontend
```

Important: importing `tau_coding.tui.state` also executes
`tau_coding/tui/__init__.py`, which imports Textual. Keep the Rich frontend out
of the `tau_coding.tui` package. Put shared UI-choice and Rich files directly in
`tau_coding`.

## 1. Add a durable UI selector

Create `src/tau_coding/ui_config.py`.

Requirements:

- `UiMode = Literal["textual", "rich"]`
- `UiSettings(ui: UiMode = "textual")`
- user path: `<TAU_HOME>/settings.json`
- project path: `<cwd>/.tau/settings.json`
- parse only the `"ui"` key; ignore unknown fields
- reject values other than `textual`/`rich` with a clear `UiConfigError`
- a project file without `"ui"` must fall through to the user file

Precedence must be exactly:

```text
1. --ui textual|rich
2. TAU_UI
3. <cwd>/.tau/settings.json
4. <TAU_HOME>/settings.json
5. default: textual
```

The project file may use the same small shape:

```json
{
  "ui": "rich"
}
```

Load the project file before trust/session startup. It only selects a
presentation layer; it must not grant project-resource trust or execute code.

## 2. Add the CLI switch

Modify `src/tau_coding/cli.py`.

- Add `--ui textual|rich`.
- In **interactive mode only**, call `resolve_ui_mode(ui, cwd=cwd or Path.cwd())`.
- Dispatch:

```text
textual → run_openai_tui(...)
rich    → run_openai_rich(...)
```

- Keep print mode, `--mode json`, `--mode transcript`, RPC, export, and utility
  commands untouched.
- Preserve existing positional arguments and the trust override path.
- Keep `run_openai_rich` thin: gather the same startup notices and forward the
  same session-facing arguments to `run_rich_ui`.
- Prefer a lazy import of the Rich runner so Textual is not constructed on the
  Rich path.

Useful shape:

```python
ui_mode = resolve_ui_mode(ui, cwd=cwd or Path.cwd())
interactive_runner = run_openai_tui if ui_mode == "textual" else run_openai_rich
if thinking_level_override is not None:
    interactive_runner = partial(
        interactive_runner,
        thinking_level_override=thinking_level_override,
    )
```

## 3. Add Rich prompt autocomplete

Add `prompt-toolkit` to `pyproject.toml`:

```toml
"prompt-toolkit>=3.0",
```

Create `src/tau_coding/rich_autocomplete.py`.

- Build suggestions from the live session command registry:
  `getattr(session, "command_registry", None)`.
- Fall back to `create_default_command_registry()`.
- Include canonical command names and aliases.
- Only complete while the cursor is in an unfinished slash-command token:
  starts with `/`, no whitespace yet.
- Return no suggestions once arguments are being typed.
- Use `prompt_toolkit.completion.Completer` and `Completion`.
- Display the command usage/name and description in the menu.

In `rich_ui.py`, create one:

```python
PromptSession(
    completer=CommandCompleter(registry),
    complete_while_typing=True,
    complete_style=CompleteStyle.MULTI_COLUMN,
    reserve_space_for_menu=8,
)
```

Use `await prompt_session.prompt_async(...)`, not the synchronous
`console.input`, for the main REPL prompt.

## 4. Build the Rich REPL

Create `src/tau_coding/rich_ui.py` with `run_rich_ui(...)`.

This is a **scrollback REPL**, not a full-screen Rich layout. Do not try to
recreate every Textual widget with `rich.live`.

### Startup

Reuse the coding-session setup rather than bypassing it:

1. Load provider settings and shell settings.
2. Resolve or create a `CodingSessionRecord` via `SessionManager`.
3. Resolve provider/model selection.
4. Construct the runtime provider with `create_model_provider`.
5. Prepare a normal `CodingSession` with `prepare_coding_session` and
   `CodingSessionConfig`.
6. Use:

```python
storage=jsonl_session_storage(record.path)
session_id=record.id
session_manager=manager
provider_settings=settings
runtime_provider_config=selection.provider
defer_authoritative_writes=True
owns_initial_provider=True
trust_interactive=False
```

7. Adopt the prepared session.
8. Install `StderrUiBridge`.
9. Call `await session.emit_pending_session_start()`.
10. Enter the read/render loop.

Close the session in `finally`; only close the initial provider directly when
no session adopted it. Do not double-close a provider owned by the session.

Rich mode has no login/trust screen. If credentials are missing, raise a clear
error that tells the user to run `tau setup` or use the Textual UI for `/login`.
With `trust_interactive=False`, the shell `default_project_trust` policy applies;
headless `ask` safely declines.

### Prompt presentation

Use Rich rules and a footer around the prompt:

```text
────────────────────────────────────────
TAU> 
────────────────────────────────────────
~/repo (branch)
provider:model (thinking)              used/limit
```

Implement the footer from the session:

- working directory, shortened against `Path.home()`
- git branch via `git -C <cwd> branch --show-current`
- provider and model
- thinking level, when available
- context usage:
  - used = `?` unless `session.has_provider_context_usage`
  - limit = `auto_compact_token_threshold` when positive, else
    `context_window_tokens`
  - compact counts as `0k`, `<1k`, or rounded `Nk`

Print the footer at startup and after each submitted prompt.

### Event rendering

Consume `CodingSessionEvent` and render:

- assistant deltas as a Rich status/spinner while streaming
- final assistant text as `rich.markdown.Markdown`
- user messages as panels
- tool start/update/end as colored status lines and bounded result previews
- provider errors and aborted turns in red
- auto-retry and compaction notices
- `AgentEndEvent` / `SessionAgentEndEvent` / `agent_settled` by stopping status

Do not render raw `message_update` deltas as final text; the canonical
`MessageEndEvent` message is authoritative.

## 5. Slash-command support

Handle Textual commands in a Rich-appropriate way. At minimum implement:

```text
/new
/compact [instructions]
/export [--format html|jsonl] [destination]
/resume [session-id]
/tree
/model
/model provider:model
/thinking <level>
/scoped-models
/tools
/skills
/prompts
/name <name>
/reload
/session
/system
/help
/hotkeys
```

Also handle terminal commands such as `!ls`.

Use the session APIs directly:

```text
session.new_session()
session.compact(instructions or None)
session.export(destination, format=...)
session.resume(session_id)
session.tree_choices() + session.branch_to_entry(entry_id)
session.preview_model_choice() / select_provider_model() / set_model_choice()
session.preview_thinking_level() / set_thinking_level()
session.handle_command(...)
parse_terminal_command(...)
```

For `/model`, `/resume`, and `/tree`, use numbered terminal pickers. For
`/model`, prefer `preview_model_choice` (the next prompt commits it) to match the
Textual TUI. For `/scoped-models`, list the configured scoped models and point
to `/model`; do not pretend to edit scopes.

If a command still needs a Textual screen, print a focused message rather than a
generic failure. Remaining Textual-only commands are expected to be:

```text
/login, /logout, /theme picker, /local, /sidebar, extension widget screens
```

`/theme` should explain that Rich uses the terminal palette. `/sidebar` should
say the sidebar is unavailable.

## 6. Tests

Add focused tests without launching an interactive terminal:

```text
tests/test_ui_config.py
```

Cover:

- default `textual`
- home `settings.json` rich
- project `.tau/settings.json` overrides home
- env overrides project
- CLI overrides env
- project file without `ui` falls through
- invalid UI values rejected
- precedence and path helpers

Add:

```text
tests/test_rich_autocomplete.py
```

Cover:

- `/` lists commands
- `/mod` filters to `/model`
- aliases appear (`/exi` → `/exit`)
- no completions after arguments or for ordinary text

For CLI tests, add an autouse fixture that sets:

```python
monkeypatch.setenv("TAU_UI", "textual")
```

so tests do not depend on a developer's repo-local `.tau/settings.json`. UI tests
that need rich must delete `TAU_UI` first.

## 7. Documentation

Update:

```text
EXPLAINERS/explainer-ui.md
website/content/guides/tui.md
website/content/reference/cli.md
website/content/reference/configuration.md
src/tau_coding/data/docs/cli.md
```

Document:

- `--ui`
- `TAU_UI`
- repo `.tau/settings.json` and home `~/.tau/settings.json`
- exact precedence
- Rich scrollback behavior and command support
- which commands remain Textual-only

## Definition of done

- `tau` still opens Textual by default.
- `tau --ui rich` opens the Rich REPL.
- `TAU_UI=rich tau` and repo/home settings select Rich.
- `--ui` and `TAU_UI` override both settings files.
- Prompt shows `TAU>`, white rules, and the session footer.
- Typing `/` shows live command completions.
- `/model`, `/thinking`, `/new`, `/compact`, `/export`, `/resume`, `/tree`,
  `/tools`, `/skills`, and `/prompts` work in Rich or degrade with an explicit
  message.
- Textual extensions that need widgets remain usable in Textual mode.
- Tests pass, and no Textual import appears in `rich_ui.py` or
  `rich_autocomplete.py`.

Suggested verification:

```bash
uv run pytest tests/test_ui_config.py tests/test_rich_autocomplete.py -q
uv run pytest tests/test_cli.py -q -k ui
uv run ruff check src/tau_coding/ui_config.py src/tau_coding/rich_ui.py src/tau_coding/rich_autocomplete.py
uv run mypy src/tau_coding/ui_config.py src/tau_coding/rich_ui.py src/tau_coding/rich_autocomplete.py
```

If an already-running Tau process prevents `uv sync` from replacing
`.venv/Scripts/tau.exe`, close Tau and rerun `uv sync`, or install the new
dependency directly with `uv pip install 'prompt-toolkit>=3.0'` before testing.
