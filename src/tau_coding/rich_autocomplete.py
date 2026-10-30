"""Slash-command completion for the Rich interactive frontend."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from tau_coding.commands import CommandRegistry

__all__ = ["CommandCompleter", "CommandSuggestion", "command_suggestions"]


@dataclass(frozen=True, slots=True)
class CommandSuggestion:
    """One slash command or alias matching the current prompt prefix."""

    text: str
    description: str


def command_suggestions(
    text_before_cursor: str,
    registry: CommandRegistry,
) -> tuple[CommandSuggestion, ...]:
    """Return slash-command suggestions for an unfinished command token."""
    if not text_before_cursor.startswith("/") or any(
        character.isspace() for character in text_before_cursor
    ):
        return ()
    prefix = text_before_cursor.casefold()
    suggestions: list[CommandSuggestion] = []
    seen: set[str] = set()
    for command in registry.list_commands():
        for name in (command.name, *command.aliases):
            command_text = f"/{name}"
            if command_text.casefold() in seen:
                continue
            if command_text.casefold().startswith(prefix):
                seen.add(command_text.casefold())
                suggestions.append(
                    CommandSuggestion(text=command_text, description=command.description)
                )
    return tuple(suggestions)


class CommandCompleter(Completer):
    """Prompt-toolkit completer backed by Tau's live command registry."""

    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    def get_completions(
        self,
        document: Document,
        complete_event: object,
    ) -> Iterator[Completion]:
        """Yield command completions for the current input token."""
        del complete_event
        text = document.text_before_cursor
        for suggestion in command_suggestions(text, self.registry):
            yield Completion(
                suggestion.text,
                start_position=-len(text),
                display=suggestion.text,
                display_meta=suggestion.description,
            )
