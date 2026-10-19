from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gitqt.services.git import GitCommandFailure, GitLocator, GitNotFoundError, GitRunner
from gitqt.services.settings import AppSettings, SettingsStore


class RepositoryValidationCode(StrEnum):
    VALID = "valid"
    BARE_UNSUPPORTED = "bare_unsupported"
    GIT_UNAVAILABLE = "git_unavailable"
    DOES_NOT_EXIST = "does_not_exist"
    NOT_DIRECTORY = "not_directory"
    NOT_GIT_REPOSITORY = "not_git_repository"


@dataclass(slots=True)
class RepositoryValidationResult:
    path: str
    code: RepositoryValidationCode
    message: str

    @property
    def is_valid(self) -> bool:
        return self.code == RepositoryValidationCode.VALID


@dataclass(slots=True)
class RepositoryEntry:
    path: str
    name: str
    validation: RepositoryValidationResult
    is_last_opened: bool


_AUTO = object()


class RepositoryValidator:
    """Validate whether a path points to a Git repository."""

    def __init__(self, runner=_AUTO):
        self.runner = runner

    def _runner(self):
        if self.runner is _AUTO:
            return GitRunner(GitLocator().discover())
        if self.runner is None:
            raise GitNotFoundError(
                "Configure Git in Application → Preferences before opening a repository."
            )
        return self.runner

    def validate(self, path: str | Path) -> RepositoryValidationResult:
        candidate = Path(path).expanduser().resolve()
        path_str = str(candidate)
        if not candidate.exists():
            return RepositoryValidationResult(
                path=path_str,
                code=RepositoryValidationCode.DOES_NOT_EXIST,
                message="Directory does not exist.",
            )
        if not candidate.is_dir():
            return RepositoryValidationResult(
                path=path_str,
                code=RepositoryValidationCode.NOT_DIRECTORY,
                message="Path is not a directory.",
            )
        try:
            runner = self._runner()
            bare = runner.run(["rev-parse", "--is-bare-repository"], cwd=candidate).stdout.strip()
            if bare == "true":
                return RepositoryValidationResult(
                    path_str,
                    RepositoryValidationCode.BARE_UNSUPPORTED,
                    "This is a bare repository. Clone it to a working directory before opening it"
                    " in GitQt.",
                )
            root = runner.run(
                ["rev-parse", "--show-toplevel"], cwd=candidate, preserve_newlines=True
            ).stdout.removesuffix("\n")
            if not root:
                raise ValueError("No working directory was found.")
            return RepositoryValidationResult(
                str(Path(root).resolve()), RepositoryValidationCode.VALID, "Repository is valid."
            )
        except GitCommandFailure as error:
            return RepositoryValidationResult(
                path_str,
                RepositoryValidationCode.NOT_GIT_REPOSITORY,
                f"Git could not open this repository: {error.result.stderr.strip()}",
            )
        except (RuntimeError, OSError, ValueError) as error:
            return RepositoryValidationResult(
                path_str, RepositoryValidationCode.GIT_UNAVAILABLE, str(error)
            )

    def initialize(self, path, *, branch="main", readme=False, gitignore=False):
        target = Path(path).expanduser().resolve()
        runner = self._runner()
        runner.run(["check-ref-format", f"refs/heads/{branch}"])
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError(
                "Choose a new or empty directory. Existing files will not be overwritten."
            )
        target.mkdir(parents=True, exist_ok=True)
        runner.run(["init", f"--initial-branch={branch}", "--", str(target)])
        if readme:
            (target / "README.md").write_text(f"# {target.name}\n", encoding="utf-8")
        if gitignore:
            (target / ".gitignore").write_text(
                "# Add patterns for generated or private files here.\n", encoding="utf-8"
            )
        return self.validate(target)


class RecentRepositoriesService:
    """Persist and validate recently used repositories."""

    def __init__(
        self,
        settings_store: SettingsStore,
        validator: RepositoryValidator | None = None,
        max_recent: int = 20,
    ) -> None:
        self._settings_store = settings_store
        self._validator = validator or RepositoryValidator()
        self._max_recent = max_recent

    def list_recent(self) -> list[RepositoryEntry]:
        settings = self._settings_store.load()
        entries: list[RepositoryEntry] = []
        for repo_path in settings.recent_repositories:
            normalized = str(Path(repo_path).expanduser().resolve())
            validation = self._validator.validate(normalized)
            entries.append(
                RepositoryEntry(
                    path=validation.path,
                    name=Path(validation.path).name or validation.path,
                    validation=validation,
                    is_last_opened=normalized == settings.last_repository,
                )
            )
        return entries

    def add_repository(self, path: str | Path) -> RepositoryValidationResult:
        normalized = str(Path(path).expanduser().resolve())
        validation = self._validator.validate(normalized)
        if not validation.is_valid:
            return validation

        self._record_recent(validation.path, opened=False)
        return validation

    def _record_recent(self, normalized: str, *, opened: bool) -> None:
        def add(settings: AppSettings) -> None:
            repositories = [repo for repo in settings.recent_repositories if repo != normalized]
            repositories.insert(0, normalized)
            settings.recent_repositories = repositories[: self._max_recent]
            if opened:
                settings.last_repository = normalized

        self._settings_store.mutate(add)

    def open_repository(self, path: str | Path) -> RepositoryValidationResult:
        validation = self._validator.validate(str(Path(path).expanduser().resolve()))
        if validation.is_valid:
            self._record_recent(validation.path, opened=True)
        return validation

    def remove_repository(self, path: str | Path) -> None:
        normalized = str(Path(path).expanduser().resolve())

        def remove(settings: AppSettings) -> None:
            settings.recent_repositories = [
                repo for repo in settings.recent_repositories if repo != normalized
            ]
            if settings.last_repository == normalized:
                settings.last_repository = None

        self._settings_store.mutate(remove)
