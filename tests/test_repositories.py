from __future__ import annotations

import subprocess
from pathlib import Path

from gitqt.services.repositories import (
    RecentRepositoriesService,
    RepositoryValidationCode,
    RepositoryValidator,
)
from gitqt.services.settings import AppSettings, SettingsStore


def _init_git_repository(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True, text=True)


def test_repository_validator_detects_missing_and_non_git_directories(tmp_path: Path) -> None:
    validator = RepositoryValidator()
    missing = tmp_path / "missing"
    not_repo = tmp_path / "plain-directory"
    not_repo.mkdir()

    missing_result = validator.validate(missing)
    plain_result = validator.validate(not_repo)

    assert missing_result.code == RepositoryValidationCode.DOES_NOT_EXIST
    assert plain_result.code == RepositoryValidationCode.NOT_GIT_REPOSITORY


def test_repository_validator_accepts_git_repository(tmp_path: Path) -> None:
    validator = RepositoryValidator()
    repository_path = tmp_path / "repo"
    _init_git_repository(repository_path)

    result = validator.validate(repository_path)

    assert result.code == RepositoryValidationCode.VALID
    assert result.is_valid


def test_recent_repositories_are_persisted_and_reordered(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    service = RecentRepositoriesService(settings_store=store)
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _init_git_repository(repo_a)
    _init_git_repository(repo_b)

    service.add_repository(repo_a)
    service.open_repository(repo_b)
    service.add_repository(repo_a)

    loaded = store.load()
    repo_a_path = str(repo_a.resolve())
    repo_b_path = str(repo_b.resolve())
    assert loaded.recent_repositories == [repo_a_path, repo_b_path]
    assert loaded.last_repository == repo_b_path


def test_remove_repository_clears_last_repository_when_necessary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repository(repo)
    normalized = str(repo.resolve())
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings(recent_repositories=[normalized], last_repository=normalized))
    service = RecentRepositoriesService(settings_store=store)

    service.remove_repository(repo)

    loaded = store.load()
    assert loaded.recent_repositories == []
    assert loaded.last_repository is None
