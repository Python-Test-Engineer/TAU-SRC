"""Durable selection of Tau's interactive frontend."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from json import JSONDecodeError, loads
from os import environ
from pathlib import Path
from typing import Any, Literal, cast

from tau_coding.paths import TauPaths

type UiMode = Literal["textual", "rich"]

__all__ = [
    "UiConfigError",
    "UiMode",
    "UiSettings",
    "load_ui_settings",
    "resolve_ui_mode",
    "ui_settings_from_json",
    "ui_settings_path",
]


class UiConfigError(ValueError):
    """Raised when Tau's interactive UI configuration is invalid."""


@dataclass(frozen=True, slots=True)
class UiSettings:
    """General interactive-frontend settings loaded from Tau home."""

    ui: UiMode = "textual"

    def to_json(self) -> dict[str, str]:
        """Serialize these settings to JSON-compatible data."""
        return {"ui": self.ui}


def ui_settings_path(paths: TauPaths | None = None) -> Path:
    """Return the durable general settings path used for the UI choice."""
    return (paths or TauPaths()).home / "settings.json"


def load_ui_settings(paths: TauPaths | None = None) -> UiSettings:
    """Load the interactive UI choice, falling back to Textual when unset."""
    path = ui_settings_path(paths)
    if not path.exists():
        return UiSettings()
    try:
        raw = loads(path.read_text(encoding="utf-8"))
    except JSONDecodeError as exc:
        raise UiConfigError(f"Tau settings are not valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise UiConfigError("Tau settings must be a JSON object")
    return ui_settings_from_json(raw)


def ui_settings_from_json(data: dict[str, Any]) -> UiSettings:
    """Parse the UI choice from general settings, ignoring unknown fields."""
    raw_ui = data.get("ui", "textual")
    if not isinstance(raw_ui, str) or raw_ui not in {"textual", "rich"}:
        raise UiConfigError("ui must be 'textual' or 'rich'")
    return UiSettings(ui=cast(UiMode, raw_ui))


def resolve_ui_mode(
    cli_mode: str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    paths: TauPaths | None = None,
) -> UiMode:
    """Resolve the interactive UI with CLI > environment > settings precedence."""
    if cli_mode is not None:
        return _ui_mode(cli_mode, "CLI --ui")
    env = environ if environment is None else environment
    raw_environment = env.get("TAU_UI")
    if raw_environment is not None:
        return _ui_mode(raw_environment, "TAU_UI")
    return load_ui_settings(paths).ui


def _ui_mode(value: str, source: str) -> UiMode:
    normalized = value.strip().lower()
    if normalized not in {"textual", "rich"}:
        raise UiConfigError(f"{source} must be 'textual' or 'rich'")
    return cast(UiMode, normalized)
