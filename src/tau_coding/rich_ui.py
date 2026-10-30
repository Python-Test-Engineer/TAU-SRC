"""Minimal Rich-based interactive frontend for Tau coding sessions.

This is deliberately a scrollback REPL rather than a full-screen TUI. Rich
renders messages, tool activity, and status; terminal line input handles the
prompt. The Textual frontend remains the full-featured option.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import anyio
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.status import Status
from rich.text import Text

from tau_agent.events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tau_agent.messages import AssistantMessage, CustomMessage, UserMessage
from tau_agent.types import JSONValue
from tau_ai.events import TextDeltaEvent, ThinkingDeltaEvent
from tau_coding.events import (
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    CodingSessionEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    SessionAgentEndEvent,
)
from tau_coding.extensions import StderrUiBridge
from tau_coding.project_trust import TrustOverride
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    ProviderSettings,
    load_provider_settings,
    resolve_provider_selection,
    resolve_startup_thinking_level,
)
from tau_coding.provider_runtime import ClosableModelProvider, create_model_provider
from tau_coding.session import (
    CodingSession,
    CodingSessionConfig,
    jsonl_session_storage,
    parse_terminal_command,
)
from tau_coding.session_manager import CodingSessionRecord, SessionManager
from tau_coding.session_preparation import prepare_coding_session
from tau_coding.shell_config import load_shell_settings
from tau_coding.thinking import ThinkingLevel

__all__ = ["run_rich_ui"]


class RichSessionUi:
    """Render coding-session events into terminal scrollback."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._status: Status | None = None

    def start_turn(self) -> None:
        self._start_status("Thinking...")

    def render(self, event: CodingSessionEvent) -> None:
        if isinstance(event, AgentStartEvent):
            self._start_status("Thinking...")
            return
        if isinstance(event, MessageStartEvent) and isinstance(event.message, AssistantMessage):
            return
        if isinstance(event, MessageUpdateEvent):
            nested = event.assistant_message_event
            if isinstance(nested, TextDeltaEvent):
                self._update_status("Streaming response...")
            elif isinstance(nested, ThinkingDeltaEvent):
                self._update_status("Thinking...")
            return
        if isinstance(event, ToolExecutionStartEvent):
            self._stop_status()
            self.console.print(
                Text(f"-> {event.tool_name} {_format_tool_args(event.args)}", style="cyan")
            )
            return
        if isinstance(event, ToolExecutionUpdateEvent):
            if event.partial_result.text:
                self.console.print(Text(f"  ... {event.partial_result.text}", style="bright_black"))
            return
        if isinstance(event, ToolExecutionEndEvent):
            status = "x" if event.is_error else "ok"
            style = "red" if event.is_error else "green"
            self.console.print(Text(f"[{status}] {event.tool_name}", style=style))
            self._print_result_preview(event.result.text)
            return
        if isinstance(event, MessageEndEvent):
            message = event.message
            if isinstance(message, UserMessage):
                self.console.print(
                    Panel(Text(message.text), title="you", title_align="left", border_style="cyan")
                )
            elif isinstance(message, CustomMessage) and message.display:
                self.console.print(Text(message.text))
            elif isinstance(message, AssistantMessage):
                self._stop_status()
                if message.stop_reason in {"error", "aborted"}:
                    error = message.error_message or "Request failed"
                    self.console.print(Text(f"Error: {error}", style="red"))
                elif message.text:
                    self.console.print(Markdown(message.text))
            return
        if isinstance(event, AutoRetryStartEvent):
            self._stop_status()
            self.console.print(
                Text(f"... {event.error_message} Retrying ({event.attempt}/{event.max_attempts})")
            )
            return
        if isinstance(event, AutoRetryEndEvent) and not event.success:
            self._stop_status()
            if event.final_error:
                self.console.print(Text(f"Error: {event.final_error}", style="red"))
            return
        if isinstance(event, CompactionStartEvent):
            self._stop_status()
            self.console.print(Text("... Compacting context"))
            return
        if isinstance(event, CompactionEndEvent):
            if event.error_message:
                self.console.print(Text(f"Compaction failed: {event.error_message}", style="red"))
            elif not event.aborted:
                self.console.print(Text("Context compacted", style="bright_black"))
            return
        if isinstance(event, (AgentEndEvent, SessionAgentEndEvent)):
            self._stop_status()

    def finish_turn(self) -> None:
        self._stop_status()

    def _start_status(self, message: str) -> None:
        self._stop_status()
        self._status = self.console.status(Text(message, style="bright_black"), spinner="dots")
        self._status.start()

    def _update_status(self, message: str) -> None:
        if self._status is None:
            self._start_status(message)
        else:
            self._status.update(Text(message, style="bright_black"))

    def _stop_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _print_result_preview(self, output: str) -> None:
        lines = output.splitlines()
        if not lines:
            return
        preview = lines[:20]
        for line in preview:
            self.console.print(Text(f"  {line}"))
        if len(lines) > len(preview):
            self.console.print(Text(f"  ... {len(lines) - len(preview)} more lines", style="dim"))


async def run_rich_ui(
    *,
    model: str | None,
    cwd: Path,
    session_id: str | None = None,
    new_session: bool = False,
    provider_name: str | None = None,
    auto_compact_token_threshold: int | None = None,
    initial_prompt: str | None = None,
    session_manager: SessionManager | None = None,
    startup_notice: str | None = None,
    startup_update_notice: str | None = None,
    startup_notices: Sequence[str] = (),
    extension_paths: tuple[Path, ...] = (),
    extensions_enabled: bool = True,
    project_extensions_enabled: bool = False,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    trust_override: TrustOverride | None = None,
    thinking_level_override: ThinkingLevel | None = None,
) -> str | None:
    """Run the Rich scrollback REPL and return the resumable session id."""
    if new_session and session_id is not None:
        raise RuntimeError("--session and --new-session cannot be used together")

    settings = load_provider_settings()
    shell_settings = load_shell_settings()
    manager = session_manager or SessionManager()
    record = _resolve_record(
        manager,
        cwd=cwd,
        session_id=session_id,
        settings=settings,
        provider_name=provider_name,
        model=model,
    )
    try:
        selection = resolve_provider_selection(
            settings,
            provider_name=record.provider_name or provider_name,
            model=record.model or model,
        )
    except ProviderConfigError as exc:
        raise RuntimeError(
            f"{exc}\n\nRich UI mode requires a configured provider. "
            "Use the Textual UI for `/login` or run `tau setup` first."
        ) from exc

    inference_provider = _inference_provider(selection.provider, selection.model)
    initial_provider: ClosableModelProvider = create_model_provider(
        selection.provider,
        model=selection.model,
        inference_provider=inference_provider,
        thinking_level=resolve_startup_thinking_level(
            selection.provider,
            selection.model,
            cli_override=thinking_level_override,
        ),
    )
    session: CodingSession | None = None
    try:
        index_on_first_persist = manager.get_session(record.id) is None
        prepared = await prepare_coding_session(
            CodingSessionConfig(
                provider=initial_provider,
                model=selection.model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                session_id=record.id,
                session_manager=manager,
                provider_name=selection.provider.name,
                inference_provider=inference_provider,
                inference_provider_mode=(
                    "fixed" if inference_provider is not None else "automatic"
                ),
                requested_provider=provider_name if provider_name is not None else None,
                requested_model=model if model is not None else None,
                session_provider_name=record.provider_name,
                provider_settings=settings,
                runtime_provider_config=selection.provider,
                auto_compact_token_threshold=auto_compact_token_threshold,
                index_on_first_persist=index_on_first_persist,
                shell_command_prefix=shell_settings.shell_command_prefix,
                extension_paths=extension_paths,
                extensions_enabled=extensions_enabled,
                project_extensions_enabled=project_extensions_enabled,
                custom_system_prompt=custom_system_prompt,
                append_system_prompt=append_system_prompt,
                thinking_level_override=thinking_level_override,
                trust_override=trust_override,
                trust_default=shell_settings.default_project_trust,
                trust_interactive=False,
                defer_authoritative_writes=True,
                owns_initial_provider=True,
            ),
            session_loader=CodingSession,
        )
        session = await prepared.adopt()
        session.extension_runtime.set_ui_bridge(StderrUiBridge())
        await session.emit_pending_session_start()

        console = Console()
        ui = RichSessionUi(console)
        for notice in (
            *((startup_notice,) if startup_notice else ()),
            *((startup_update_notice,) if startup_update_notice else ()),
            *startup_notices,
        ):
            console.print(Text(notice, style="bright_black"))

        pending_prompt = initial_prompt.strip() if initial_prompt else ""
        while True:
            if pending_prompt:
                text = pending_prompt
                pending_prompt = ""
            else:
                try:
                    text = await anyio.to_thread.run_sync(_read_prompt, console)
                except EOFError:
                    break
                except KeyboardInterrupt:
                    console.print()
                    break
                text = text.strip()
                if not text:
                    continue
            if text in {"/exit", "/quit"}:
                break
            input_result = await _handle_text_input(session, text, console)
            if input_result == "exit":
                break
            if input_result == "handled":
                continue
            ui.start_turn()
            try:
                async for event in session.prompt(text):
                    ui.render(event)
            finally:
                ui.finish_turn()
    finally:
        if session is not None:
            await session.aclose()
        else:
            await initial_provider.aclose()

    active_session_id = session.session_id
    if active_session_id is None or manager.get_session(active_session_id) is None:
        return None
    return active_session_id


def _read_prompt(console: Console) -> str:
    console.print(Rule(style="white"))
    try:
        return console.input("[bold cyan]TAU>[/] ")
    finally:
        console.print(Rule(style="white"))


async def _handle_text_input(
    session: CodingSession,
    text: str,
    console: Console,
) -> Literal["handled", "unhandled", "exit"]:
    """Handle a local command and report whether it owned the input."""
    terminal_command = parse_terminal_command(text)
    if terminal_command is not None:
        result = await session.run_terminal_command(
            terminal_command.command,
            add_to_context=terminal_command.add_to_context,
        )
        style = "green" if result.ok else "red"
        console.print(Text(f"$ {result.command}", style=style))
        if result.output:
            console.print(result.output)
        return "handled"

    command = session.handle_command(text)
    if not command.handled:
        return "unhandled"
    if command.exit_requested:
        return "exit"
    if command.clear_requested:
        console.clear()
        return "handled"
    if command.reload_requested:
        try:
            summary = await session.reload()
        except ValueError as exc:
            console.print(Text(f"Could not reload: {exc}", style="red"))
        else:
            console.print(Text(f"Reloaded: {summary}"))
        return "handled"
    if command.session_name is not None:
        try:
            renamed = await session.set_session_name(command.session_name)
        except ValueError as exc:
            console.print(Text(f"Could not rename session: {exc}", style="red"))
        else:
            console.print(Text(f"Session renamed: {renamed}"))
        return "handled"
    if command.message:
        console.print(Text(command.message))
        return "handled"
    console.print(Text("This command is only available in the Textual UI.", style="yellow"))
    return "handled"


def _resolve_record(
    manager: SessionManager,
    *,
    cwd: Path,
    session_id: str | None,
    settings: ProviderSettings,
    provider_name: str | None,
    model: str | None,
) -> CodingSessionRecord:
    if session_id is not None:
        record = manager.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")
        return record
    selection = resolve_provider_selection(
        settings,
        provider_name=provider_name,
        model=model,
    )
    return manager.prepare_session(
        cwd=cwd,
        model=selection.model,
        provider_name=selection.provider.name,
        inference_provider=_inference_provider(selection.provider, selection.model),
    )


def _inference_provider(provider: object, model: str) -> str | None:
    if isinstance(provider, OpenAICompatibleProviderConfig) and provider.name == "huggingface":
        return provider.inference_providers.get(model)
    return None


def _format_tool_args(args: Mapping[str, JSONValue]) -> str:
    if not args:
        return ""
    compact = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    if len(compact) > 120:
        compact = compact[:117] + "..."
    return compact
