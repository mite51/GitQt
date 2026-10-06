from __future__ import annotations

import sys
from pathlib import Path

from gitqt.app.bootstrap import build_services
from gitqt.services.settings import AppSettings


def test_build_services_initializes_defaults_and_discovers_git(tmp_path: Path) -> None:
    services = build_services(base_config_dir=tmp_path, which_func=lambda _: sys.executable)

    assert services.paths.config_dir == tmp_path
    assert services.paths.settings_file == tmp_path / "settings.json"
    assert services.paths.log_dir == tmp_path / "logs"
    assert services.paths.crash_dir == tmp_path / "crash-reports"
    assert services.settings == AppSettings()
    assert services.git is not None
    assert services.git.path == sys.executable
    assert services.commit is not None
    assert services.branch is not None
    assert services.history is not None
    assert services.remote is not None
    assert services.stash is not None
    assert services.tag is not None
    assert services.conflict is not None
    assert services.external_tools is not None
    assert services.paths.log_dir.exists()
    assert services.paths.crash_dir.exists()


def test_build_services_uses_saved_git_setting(tmp_path: Path) -> None:
    settings = AppSettings(git_executable=sys.executable)
    services = build_services(base_config_dir=tmp_path, which_func=lambda _: None)
    services.settings_store.save(settings)

    reloaded = build_services(base_config_dir=tmp_path, which_func=lambda _: None)

    assert reloaded.git is not None
    assert reloaded.git.path == sys.executable
