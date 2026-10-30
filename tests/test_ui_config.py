from pathlib import Path

import pytest

from tau_coding.paths import TauPaths
from tau_coding.ui_config import (
    UiConfigError,
    UiSettings,
    load_ui_settings,
    resolve_ui_mode,
    ui_settings_from_json,
    ui_settings_path,
)


def test_ui_settings_path_uses_tau_home_settings(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert ui_settings_path(paths) == tmp_path / ".tau" / "settings.json"


def test_load_ui_settings_defaults_to_textual(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    assert load_ui_settings(paths) == UiSettings()


def test_load_ui_settings_reads_rich_ui(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    path = ui_settings_path(paths)
    path.parent.mkdir(parents=True)
    path.write_text('{"ui": "rich", "future": true}', encoding="utf-8")

    assert load_ui_settings(paths).ui == "rich"


def test_ui_settings_reject_unknown_ui() -> None:
    with pytest.raises(UiConfigError, match="textual.*rich"):
        ui_settings_from_json({"ui": "web"})


def test_resolve_ui_mode_precedence(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")
    path = ui_settings_path(paths)
    path.parent.mkdir(parents=True)
    path.write_text('{"ui": "rich"}', encoding="utf-8")

    assert resolve_ui_mode(paths=paths, environment={}) == "rich"
    assert resolve_ui_mode(paths=paths, environment={"TAU_UI": "textual"}) == "textual"
    assert (
        resolve_ui_mode(
            "textual",
            paths=paths,
            environment={"TAU_UI": "rich"},
        )
        == "textual"
    )


def test_resolve_ui_mode_rejects_invalid_environment(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents")

    with pytest.raises(UiConfigError, match="TAU_UI"):
        resolve_ui_mode(paths=paths, environment={"TAU_UI": "gui"})
