from tau_coding.commands import create_default_command_registry
from tau_coding.rich_autocomplete import command_suggestions


def test_command_suggestions_lists_all_commands_for_slash() -> None:
    registry = create_default_command_registry()

    suggestions = command_suggestions("/", registry)

    values = {suggestion.text for suggestion in suggestions}
    assert "/model" in values
    assert "/quit" in values
    assert "/session" in values


def test_command_suggestions_filters_by_typed_prefix() -> None:
    registry = create_default_command_registry()

    suggestions = command_suggestions("/mod", registry)

    assert [suggestion.text for suggestion in suggestions] == ["/model"]


def test_command_suggestions_include_aliases() -> None:
    registry = create_default_command_registry()

    suggestions = command_suggestions("/exi", registry)

    assert [suggestion.text for suggestion in suggestions] == ["/exit"]


def test_command_suggestions_stop_after_command_argument() -> None:
    registry = create_default_command_registry()

    assert command_suggestions("/model gpt", registry) == ()
    assert command_suggestions("hello", registry) == ()
