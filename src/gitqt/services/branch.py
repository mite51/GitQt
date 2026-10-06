from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gitqt.parsers.refs import (
    LocalBranchRef,
    RemoteBranchRef,
    parse_ahead_behind_counts,
    parse_local_branch_refs,
    parse_remote_branch_refs,
)
from gitqt.services.caching import TtlCache, repository_cache_key
from gitqt.services.git import GitCommandFailure, GitRunner


@dataclass(slots=True)
class BranchTrackingStatus:
    branch_name: str | None
    upstream: str | None
    ahead: int
    behind: int
    is_detached_head: bool
    head_commit_short_hash: str | None


@dataclass(slots=True)
class BranchSnapshot:
    local_branches: list[LocalBranchRef]
    remote_branches: list[RemoteBranchRef]
    tracking: BranchTrackingStatus


class BranchService:
    """Manage branch listing and branch operations for a repository."""

    def __init__(
        self,
        runner: GitRunner,
        *,
        cache_ttl_seconds: float = 1.5,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._cache: TtlCache[str, BranchSnapshot] = TtlCache(
            ttl_seconds=cache_ttl_seconds,
            monotonic_func=monotonic_func,
        )

    def get_snapshot(
        self,
        repository_path: str | Path,
        *,
        force_refresh: bool = False,
    ) -> BranchSnapshot:
        cache_key = repository_cache_key(repository_path)
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        snapshot = self._load_snapshot(repository_path)
        self._cache.set(cache_key, snapshot)
        return snapshot

    def invalidate_repository(self, repository_path: str | Path) -> None:
        self._cache.invalidate(repository_cache_key(repository_path))

    def _load_snapshot(self, repository_path: str | Path) -> BranchSnapshot:
        local_result = self._runner.run(
            ["branch", "--format=%(refname:short)|%(upstream:short)|%(HEAD)|%(objectname:short)"],
            cwd=repository_path,
        )
        remote_result = self._runner.run(
            ["branch", "-r", "--format=%(refname:short)|%(objectname:short)"],
            cwd=repository_path,
        )
        local_branches = parse_local_branch_refs(local_result.stdout)
        remote_branches = parse_remote_branch_refs(remote_result.stdout)

        current_branch = next((branch for branch in local_branches if branch.is_head), None)
        is_detached_head = bool(
            current_branch and current_branch.name.startswith("(HEAD detached at")
        )
        head_commit_short_hash = self._resolve_head_commit(repository_path)
        branch_name = (
            None if is_detached_head else (current_branch.name if current_branch else None)
        )
        if current_branch is None and head_commit_short_hash is None:
            try:
                branch_name = self._runner.run(
                    ["symbolic-ref", "--short", "HEAD"], cwd=repository_path
                ).stdout.strip()
            except GitCommandFailure:
                pass
        upstream = (
            None if is_detached_head else (current_branch.upstream if current_branch else None)
        )
        tracking = BranchTrackingStatus(
            branch_name=branch_name,
            upstream=upstream,
            ahead=0,
            behind=0,
            is_detached_head=is_detached_head or branch_name is None,
            head_commit_short_hash=head_commit_short_hash,
        )

        if current_branch and current_branch.upstream and not tracking.is_detached_head:
            result = self._runner.run(
                [
                    "rev-list",
                    "--left-right",
                    "--count",
                    f"{current_branch.upstream}...{current_branch.name}",
                ],
                cwd=repository_path,
            )
            ahead, behind = parse_ahead_behind_counts(result.stdout)
            tracking.ahead = ahead
            tracking.behind = behind

        return BranchSnapshot(
            local_branches=local_branches,
            remote_branches=remote_branches,
            tracking=tracking,
        )

    def create_branch(
        self,
        repository_path: str | Path,
        name: str,
        *,
        start_point: str | None = None,
        checkout: bool = False,
    ) -> None:
        args = ["branch", name]
        if start_point:
            args.append(start_point)
        self._runner.run(args, cwd=repository_path)
        self.invalidate_repository(repository_path)
        if checkout:
            self.checkout_branch(repository_path, name)

    def checkout_branch(self, repository_path: str | Path, name: str) -> None:
        self._runner.run(["checkout", name], cwd=repository_path)
        self.invalidate_repository(repository_path)

    def delete_branch(self, repository_path: str | Path, name: str, *, force: bool = False) -> None:
        delete_flag = "-D" if force else "-d"
        self._runner.run(["branch", delete_flag, name], cwd=repository_path)
        self.invalidate_repository(repository_path)

    def _resolve_head_commit(self, repository_path: str | Path) -> str | None:
        try:
            result = self._runner.run(["rev-parse", "--short", "HEAD"], cwd=repository_path)
        except GitCommandFailure:
            return None
        return result.stdout.strip() or None
