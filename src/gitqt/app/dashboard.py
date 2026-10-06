from __future__ import annotations

from dataclasses import dataclass

from gitqt.services.repositories import RecentRepositoriesService, RepositoryEntry


@dataclass(slots=True)
class DashboardActionResult:
    success: bool
    message: str


@dataclass(slots=True)
class DashboardState:
    repositories: list[RepositoryEntry]
    status: str
    message: str


class DashboardViewModel:
    """Application-facing state and actions for the repository dashboard."""

    def __init__(self, repositories: RecentRepositoriesService, *, autoload=True) -> None:
        self._repositories = repositories
        self.state = DashboardState(repositories=[], status="empty", message="")
        if autoload:
            self.refresh()

    def refresh(self) -> DashboardState:
        repositories = self._repositories.list_recent()
        if not repositories:
            self.state = DashboardState(
                repositories=[],
                status="empty",
                message="No repositories yet. Add or open a repository to get started.",
            )
            return self.state

        invalid_count = sum(1 for repo in repositories if not repo.validation.is_valid)
        if invalid_count:
            self.state = DashboardState(
                repositories=repositories,
                status="invalid",
                message=f"{invalid_count} repository path(s) are no longer valid.",
            )
            return self.state

        self.state = DashboardState(
            repositories=repositories,
            status="ready",
            message=self._format_repository_count_message(len(repositories)),
        )
        return self.state

    @staticmethod
    def _format_repository_count_message(count: int) -> str:
        noun = "repository" if count == 1 else "repositories"
        return f"{count} {noun} available."

    def add_repository(self, path: str) -> DashboardActionResult:
        validation = self._repositories.add_repository(path)
        self.refresh()
        if validation.is_valid:
            return DashboardActionResult(success=True, message="Repository added to dashboard.")
        return DashboardActionResult(success=False, message=validation.message)

    def open_repository(self, path: str) -> DashboardActionResult:
        validation = self._repositories.open_repository(path)
        self.refresh()
        if validation.is_valid:
            return DashboardActionResult(success=True, message="Repository opened.")
        return DashboardActionResult(success=False, message=validation.message)

    def remove_repository(self, path: str) -> DashboardActionResult:
        self._repositories.remove_repository(path)
        self.refresh()
        return DashboardActionResult(success=True, message="Repository removed from dashboard.")
