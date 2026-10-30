from pathlib import Path

import pytest

from tau_coding.paths import TauPaths
from tau_coding.ui_config import (
    UiConfigError,
    UiSettings,
    load_ui_settings,
    project_ui_settings_path,
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


def test_project_ui_settings_path_uses_repo_tau_dir(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / "home" / ".tau", agents_home=tmp_path / ".agents")
    cwd = tmp_path / "project"

    assert project_ui_settings_path(cwd, paths) == cwd / ".tau" / "settings.json"


def test_project_ui_overrides_user_ui(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / "home" / ".tau", agents_home=tmp_path / ".agents")
    user_path = ui_settings_path(paths)
    user_path.parent.mkdir(parents=True)
    user_path.write_text('{"ui": "textual"}', encoding="utf-8")
    cwd = tmp_path / "project"
    project_path = project_ui_settings_path(cwd, paths)
    project_path.parent.mkdir(parents=True)
    project_path.write_text('{"ui": "rich"}', encoding="utf-8")

    assert resolve_ui_mode(paths=paths, environment={}, cwd=cwd) == "rich"


def test_project_ui_without_ui_falls_back_to_user_setting(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / "home" / ".tau", agents_home=tmp_path / ".agents")
    user_path = ui_settings_path(paths)
    user_path.parent.mkdir(parents=True)
    user_path.write_text('{"ui": "rich"}', encoding="utf-8")
    cwd = tmp_path / "project"
    project_path = project_ui_settings_path(cwd, paths)
    project_path.parent.mkdir(parents=True)
    project_path.write_text('{"other": true}', encoding="utf-8")

    assert resolve_ui_mode(paths=paths, environment={}, cwd=cwd) == "rich"


def test_environment_and_cli_override_project_ui(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / "home" / ".tau", agents_home=tmp_path / ".agents")
    cwd = tmp_path / "project"
    project_path = project_ui_settings_path(cwd, paths)
    project_path.parent.mkdir(parents=True)
    project_path.write_text('{"ui": "rich"}', encoding="utf-8")

    assert resolve_ui_mode(paths=paths, environment={"TAU_UI": "textual"}, cwd=cwd) == "textual"
    assert (
        resolve_ui_mode(
            "textual",
            paths=paths,
            environment={"TAU_UI": "rich"},
            cwd=cwd,
        )
        == "textual"
    )


def test_invalid_project_ui_is_rejected(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / "home" / ".tau", agents_home=tmp_path / ".agents")
    cwd = tmp_path / "project"
    project_path = project_ui_settings_path(cwd, paths)
    project_path.parent.mkdir(parents=True)
    project_path.write_text('{"ui": "web"}', encoding="utf-8")

    with pytest.raises(UiConfigError, match="textual.*rich"):
        resolve_ui_mode(paths=paths, environment={}, cwd=cwd)


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
