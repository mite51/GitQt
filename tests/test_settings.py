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


def _run_competing_settings_processes(tmp_path, action, *, count=4):
    """Launch real interpreters, synchronize them after their initial read."""
    import os
    import subprocess
    import sys
    import time

    source = Path(__file__).resolve().parents[1] / "src"
    script = """
import sys, time
from pathlib import Path
from gitqt.services.settings import SettingsStore
root, index = Path(sys.argv[1]), int(sys.argv[2])
store = SettingsStore(root / "settings.json")
snapshot = store.load()
(root / f"ready-{index}").touch()
while not (root / "go").exists():
    time.sleep(0.005)
""" + action
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), str(index)],
            env={**os.environ, "PYTHONPATH": str(source)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(count)
    ]
    try:
        deadline = time.monotonic() + 15
        while len(list(tmp_path.glob("ready-*"))) < count:
            assert time.monotonic() < deadline, "Child process did not reach contention barrier"
            assert all(process.poll() is None for process in processes)
            time.sleep(0.01)
        (tmp_path / "go").touch()
        for process in processes:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, output + error
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_settings_cross_process_stale_saves_merge_disjoint_changes(tmp_path):
    _run_competing_settings_processes(
        tmp_path,
        """
fields = ["git_executable", "external_diff_tool", "external_merge_tool", "ui_font_family"]
setattr(snapshot, fields[index], f"value-{index}")
store.save(snapshot)
""",
    )
    loaded = SettingsStore(tmp_path / "settings.json").load()
    assert loaded.git_executable == "value-0"
    assert loaded.external_diff_tool == "value-1"
    assert loaded.external_merge_tool == "value-2"
    assert loaded.ui_font_family == "value-3"


def test_settings_cross_process_updates_and_atomic_list_transactions(tmp_path):
    _run_competing_settings_processes(
        tmp_path,
        """
fields = ["git_executable", "external_diff_tool", "external_merge_tool", "ui_font_family"]
for iteration in range(12):
    store.mutate(lambda settings: settings.recent_repositories.append(f"{index}-{iteration}"))
    store.update(**{fields[index]: str(iteration)})
""",
    )
    loaded = SettingsStore(tmp_path / "settings.json").load()
    assert set(loaded.recent_repositories) == {
        f"{index}-{iteration}" for index in range(4) for iteration in range(12)
    }
    assert len(loaded.recent_repositories) == 48
    assert loaded.git_executable == loaded.external_diff_tool == loaded.external_merge_tool == "11"
    assert loaded.ui_font_family == "11"


def test_settings_multiple_loaded_snapshots_keep_their_own_baseline(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    first, second = store.load(), store.load()
    first.external_diff_tool = "diff"
    store.save(first)
    second.external_merge_tool = "merge"
    store.save(second)
    assert store.load().external_diff_tool == "diff"
    assert store.load().external_merge_tool == "merge"


def test_settings_cross_process_recovery_has_one_preserved_corrupt_file(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update(external_diff_tool="previous")
    store.update(external_diff_tool="latest")
    store.path.write_bytes(b"damaged")
    _run_competing_settings_processes(tmp_path, 'store.update(ui_font_family="Recovered")\n')
    assert len(list(tmp_path.glob("settings.json.corrupt-*"))) == 1
    assert store.load().external_diff_tool == "previous"
    assert store.load().ui_font_family == "Recovered"


def test_recent_repository_additions_are_atomic_across_processes(tmp_path):
    _run_competing_settings_processes(
        tmp_path,
        """
from gitqt.services.repositories import RecentRepositoriesService
from gitqt.services.repositories import RepositoryValidationResult, RepositoryValidationCode
class Validator:
    def validate(self, path):
        return RepositoryValidationResult(str(path), RepositoryValidationCode.VALID, "Valid")
service = RecentRepositoriesService(store, Validator(), max_recent=100)
for iteration in range(8):
    service.open_repository(root / f"repo-{index}-{iteration}")
""",
    )
    settings = SettingsStore(tmp_path / "settings.json").load()
    assert len(settings.recent_repositories) == 32
    assert set(settings.recent_repositories) == {
        str(tmp_path / f"repo-{index}-{iteration}") for index in range(4) for iteration in range(8)
    }
    assert settings.last_repository == settings.recent_repositories[0]
