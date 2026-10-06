from pathlib import Path

from gitqt.services.settings import AppSettings, SettingsStore


def test_settings_load_returns_defaults_when_file_is_missing(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")

    settings = store.load()

    assert settings == AppSettings()


def test_settings_save_and_load_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    original = AppSettings(
        recent_repositories=["/tmp/repo-a", "/tmp/repo-b"],
        git_executable="/usr/bin/git",
        last_repository="/tmp/repo-b",
        external_diff_tool="/usr/bin/meld",
        external_merge_tool="/usr/bin/kdiff3",
        crash_reports_enabled=False,
    )

    store.save(original)
    loaded = store.load()

    assert loaded == original


def test_settings_load_migrates_v1_payload(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        """
{
  "repositories": ["/tmp/repo-old", 99],
  "git_path": "/usr/local/bin/git",
  "last_repository": "/tmp/repo-old"
}
""".strip() + "\n",
        encoding="utf-8",
    )
    store = SettingsStore(settings_file)

    loaded = store.load()

    assert loaded.settings_version == SettingsStore.CURRENT_VERSION
    assert loaded.recent_repositories == ["/tmp/repo-old"]
    assert loaded.git_executable == "/usr/local/bin/git"
    assert loaded.crash_reports_enabled is True


def test_settings_load_sanitizes_malformed_fields(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        """
{
  "settings_version": 2,
  "recent_repositories": ["/tmp/repo-a", false, ""],
  "git_executable": 123,
  "last_repository": 456,
  "external_diff_tool": null,
  "external_merge_tool": true,
  "crash_reports_enabled": "yes"
}
""".strip() + "\n",
        encoding="utf-8",
    )
    store = SettingsStore(settings_file)

    loaded = store.load()

    assert loaded.recent_repositories == ["/tmp/repo-a"]
    assert loaded.git_executable is None
    assert loaded.last_repository is None
    assert loaded.external_merge_tool is None
    assert loaded.crash_reports_enabled is True


def test_settings_split_branch_paths_defaults_to_true() -> None:
    defaults = AppSettings()
    assert defaults.split_branch_paths is True


def test_settings_round_trip_includes_split_branch_paths(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    original = AppSettings(split_branch_paths=False)

    store.save(original)
    loaded = store.load()

    assert loaded.split_branch_paths is False


def test_settings_v2_payload_migrates_to_v3_with_split_branch_paths(
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        """
{
  "settings_version": 2,
  "recent_repositories": ["/tmp/repo-a"],
  "git_executable": "/usr/bin/git",
  "last_repository": "/tmp/repo-a",
  "external_diff_tool": null,
  "external_merge_tool": null,
  "crash_reports_enabled": true
}
""".strip() + "\n",
        encoding="utf-8",
    )
    store = SettingsStore(settings_file)

    loaded = store.load()

    assert loaded.settings_version == SettingsStore.CURRENT_VERSION
    assert loaded.split_branch_paths is True


def test_settings_history_row_alpha_defaults_to_half() -> None:
    defaults = AppSettings()
    assert defaults.history_row_alpha == 0.5


def test_settings_round_trip_preserves_history_row_alpha(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    original = AppSettings(history_row_alpha=0.2)

    store.save(original)
    loaded = store.load()

    assert loaded.history_row_alpha == 0.2


def test_settings_history_row_alpha_clamps_invalid(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        """
{
  "settings_version": 3,
  "history_row_alpha": 99
}
""".strip() + "\n",
        encoding="utf-8",
    )
    store = SettingsStore(settings_file)

    loaded = store.load()

    assert loaded.history_row_alpha == 0.5


def test_settings_ui_font_family_defaults_to_inter() -> None:
    defaults = AppSettings()
    assert defaults.ui_font_family == "Inter"


def test_settings_round_trip_preserves_ui_font_family(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    original = AppSettings(ui_font_family="Fira Sans")

    store.save(original)
    loaded = store.load()

    assert loaded.ui_font_family == "Fira Sans"


def test_settings_v3_payload_migrates_to_v4_with_font_family(
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        """
{
  "settings_version": 3,
  "recent_repositories": ["/tmp/repo-a"],
  "git_executable": "/usr/bin/git"
}
""".strip() + "\n",
        encoding="utf-8",
    )
    store = SettingsStore(settings_file)

    loaded = store.load()

    assert loaded.settings_version == SettingsStore.CURRENT_VERSION
    assert loaded.ui_font_family == "Inter"


def test_settings_ui_font_family_falls_back_when_blank(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        """
{
  "settings_version": 4,
  "ui_font_family": "   "
}
""".strip() + "\n",
        encoding="utf-8",
    )
    store = SettingsStore(settings_file)

    loaded = store.load()

    assert loaded.ui_font_family == "Inter"


def test_settings_recovers_non_object_payload(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[]\n")
    store = SettingsStore(path)
    assert store.load() == AppSettings()
    assert store.warning
    assert next(tmp_path.glob("settings.json.corrupt-*")).read_text() == "[]\n"
