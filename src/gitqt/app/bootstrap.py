from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from gitqt.services.branch import BranchService
from gitqt.services.commit import CommitService
from gitqt.services.commit_operations import CommitOperationsService
from gitqt.services.conflict import ConflictService
from gitqt.services.crash_reporting import CrashReporter
from gitqt.services.drafts import DraftStore
from gitqt.services.external_tools import ExternalToolService
from gitqt.services.git import GitExecutable, GitLocator, GitNotFoundError, GitRunner
from gitqt.services.history import HistoryService
from gitqt.services.logging import configure_logging
from gitqt.services.remote import RemoteService
from gitqt.services.repositories import RecentRepositoriesService, RepositoryValidator
from gitqt.services.settings import AppSettings, SettingsStore
from gitqt.services.stash import StashService
from gitqt.services.status import StatusService
from gitqt.services.tag import TagService
from gitqt.services.worker_pool import WorkerPool


@dataclass(slots=True)
class AppPaths:
    config_dir: Path
    settings_file: Path
    log_dir: Path
    crash_dir: Path


@dataclass(slots=True)
class AppServices:
    paths: AppPaths
    settings_store: SettingsStore
    settings: AppSettings
    git_locator: GitLocator
    git: GitExecutable | None
    git_runner: GitRunner | None
    repositories: RecentRepositoriesService
    status: StatusService | None
    commit: CommitService | None
    commit_operations: CommitOperationsService | None
    branch: BranchService | None
    history: HistoryService | None
    remote: RemoteService | None
    stash: StashService | None
    tag: TagService | None
    conflict: ConflictService | None
    external_tools: ExternalToolService | None
    worker_pool: WorkerPool
    crash_reporter: CrashReporter
    credential_broker: object | None = None
    drafts: DraftStore | None = None


def default_config_dir(app_name: str = "GitQt") -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / app_name


def create_app_paths(base_config_dir: Path | None = None) -> AppPaths:
    config_dir = Path(base_config_dir) if base_config_dir is not None else default_config_dir()
    return AppPaths(
        config_dir=config_dir,
        settings_file=config_dir / "settings.json",
        log_dir=config_dir / "logs",
        crash_dir=config_dir / "crash-reports",
    )


def build_services(
    *,
    base_config_dir: Path | None = None,
    explicit_git_path: str | None = None,
    which_func=None,
) -> AppServices:
    paths = create_app_paths(base_config_dir)
    logger = configure_logging(paths.log_dir)

    settings_store = SettingsStore(paths.settings_file)
    settings = settings_store.load()
    crash_reporter = CrashReporter(paths.crash_dir)
    if settings.crash_reports_enabled:
        crash_reporter.install()
    git_locator = GitLocator(which_func=which_func)

    configured_git = explicit_git_path or settings.git_executable
    try:
        git = git_locator.discover(configured_git)
    except GitNotFoundError:
        git = None
        logger.warning("Git executable is not currently available.")
    git_runner = GitRunner(git) if git is not None else None
    status = StatusService(git_runner) if git_runner is not None else None
    commit = CommitService(git_runner) if git_runner is not None else None
    commit_operations = CommitOperationsService(git_runner) if git_runner is not None else None
    branch = BranchService(git_runner) if git_runner is not None else None
    history = HistoryService(git_runner) if git_runner is not None else None
    remote = RemoteService(git_runner) if git_runner is not None else None
    stash = StashService(git_runner) if git_runner is not None else None
    tag = TagService(git_runner) if git_runner is not None else None
    conflict = ConflictService(git_runner) if git_runner is not None else None
    external_tools = ExternalToolService(git_runner) if git_runner is not None else None
    worker_pool = WorkerPool()

    return AppServices(
        paths=paths,
        settings_store=settings_store,
        settings=settings,
        git_locator=git_locator,
        git=git,
        git_runner=git_runner,
        repositories=RecentRepositoriesService(
            settings_store=settings_store, validator=RepositoryValidator(git_runner)
        ),
        status=status,
        commit=commit,
        commit_operations=commit_operations,
        branch=branch,
        history=history,
        remote=remote,
        stash=stash,
        tag=tag,
        conflict=conflict,
        external_tools=external_tools,
        worker_pool=worker_pool,
        crash_reporter=crash_reporter,
        drafts=DraftStore(paths.config_dir / "drafts.json"),
    )
