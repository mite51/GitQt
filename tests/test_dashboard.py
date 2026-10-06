from __future__ import annotations

import subprocess
from pathlib import Path

from gitqt.app.dashboard import DashboardViewModel
from gitqt.services.repositories import RecentRepositoriesService
from gitqt.services.settings import AppSettings, SettingsStore


def _init_git_repository(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True, text=True)


def test_dashboard_state_is_empty_with_no_repositories(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    service = RecentRepositoriesService(settings_store=store)

    dashboard = DashboardViewModel(service)

    assert dashboard.state.status == "empty"
    assert dashboard.state.repositories == []


def test_dashboard_state_is_ready_with_valid_repositories(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repository(repo)
    normalized = str(repo.resolve())
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings(recent_repositories=[normalized], last_repository=normalized))
    service = RecentRepositoriesService(settings_store=store)

    dashboard = DashboardViewModel(service)

    assert dashboard.state.status == "ready"
    assert dashboard.state.message == "1 repository available."
    assert len(dashboard.state.repositories) == 1
    assert dashboard.state.repositories[0].is_last_opened
    assert dashboard.state.repositories[0].validation.is_valid


def test_dashboard_ready_message_handles_plural_repository_count(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _init_git_repository(repo_a)
    _init_git_repository(repo_b)
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings(recent_repositories=[str(repo_a.resolve()), str(repo_b.resolve())]))
    service = RecentRepositoriesService(settings_store=store)

    dashboard = DashboardViewModel(service)

    assert dashboard.state.status == "ready"
    assert dashboard.state.message == "2 repositories available."


def test_dashboard_state_is_invalid_when_saved_path_is_not_repository(tmp_path: Path) -> None:
    invalid_repo = tmp_path / "not-a-repo"
    invalid_repo.mkdir()
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings(recent_repositories=[str(invalid_repo.resolve())]))
    service = RecentRepositoriesService(settings_store=store)

    dashboard = DashboardViewModel(service)

    assert dashboard.state.status == "invalid"
    assert len(dashboard.state.repositories) == 1
    assert not dashboard.state.repositories[0].validation.is_valid
