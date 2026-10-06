from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

from gitqt.services.caching import TtlCache, repository_cache_key
from gitqt.services.git import GitCommandFailure, GitRunner
from gitqt.services.recovery import RecoveryService


@dataclass(slots=True)
class RemoteInfo:
    name: str
    fetch_url: str
    push_url: str


@dataclass(slots=True)
class RemoteSnapshot:
    remotes: list[RemoteInfo]


@dataclass(frozen=True, slots=True)
class ForcePushPreview:
    expected_oid: str
    source_oid: str
    push_url: str
    message: str


@dataclass(slots=True)
class RemoteProgressEvent:
    level: str
    message: str


@dataclass(slots=True)
class RemoteCommandResult:
    success: bool
    operation: str
    remote: str
    message: str
    elapsed_ms: int
    stderr: str = ""
    error_category: str | None = None
    progress: list[RemoteProgressEvent] | None = None


class RemoteService:
    """Run remote Git operations with typed results and error mapping."""

    def __init__(
        self,
        runner: GitRunner,
        *,
        cache_ttl_seconds: float = 30.0,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._remote_cache: TtlCache[str, RemoteSnapshot] = TtlCache(
            ttl_seconds=cache_ttl_seconds,
            monotonic_func=monotonic_func,
        )

    def list_remotes(
        self,
        repository_path: str | Path,
        *,
        force_refresh: bool = False,
    ) -> RemoteSnapshot:
        cache_key = repository_cache_key(repository_path)
        if not force_refresh:
            cached = self._remote_cache.get(cache_key)
            if cached is not None:
                return cached
        result = self._runner.run(["remote", "-v"], cwd=repository_path)
        remotes = _parse_remote_verbose_output(result.stdout)
        snapshot = RemoteSnapshot(remotes=remotes)
        self._remote_cache.set(cache_key, snapshot)
        return snapshot

    def invalidate_repository(self, repository_path: str | Path) -> None:
        self._remote_cache.invalidate(repository_cache_key(repository_path))

    def fetch(
        self,
        repository_path: str | Path,
        *,
        remote: str = "origin",
        prune: bool = False,
    ) -> RemoteCommandResult:
        args = ["fetch", "--progress", remote]
        if prune:
            args.append("--prune")
        return self._run_remote_operation(
            repository_path,
            operation="fetch",
            remote=remote,
            args=args,
        )

    def pull(
        self,
        repository_path: str | Path,
        *,
        remote: str = "origin",
        branch: str | None = None,
        rebase: bool = False,
        ff_only: bool = False,
    ) -> RemoteCommandResult:
        args = ["pull", "--progress", remote]
        if branch:
            args.append(branch)
        if rebase:
            args.append("--rebase")
        elif ff_only:
            args.append("--ff-only")
        else:
            args.append("--no-rebase")
        return self._run_remote_operation(
            repository_path,
            operation="pull",
            remote=remote,
            args=args,
        )

    def push(
        self,
        repository_path: str | Path,
        *,
        remote: str = "origin",
        branch: str | None = None,
        set_upstream: bool = False,
        force_with_lease: bool = False,
        expected_oid: str | None = None,
        target_url: str | None = None,
    ) -> RemoteCommandResult:
        args = ["push", "--progress", target_url or remote]
        if branch:
            args.append(branch)
        if set_upstream:
            args.append("--set-upstream")
        if force_with_lease:
            args.append(
                f"--force-with-lease=refs/heads/{branch.split(':')[-1]}:{expected_oid}"
                if expected_oid is not None and branch
                else "--force-with-lease"
            )
        return self._run_remote_operation(
            repository_path,
            operation="push",
            remote=remote,
            args=args,
        )

    @staticmethod
    def validate_url(url):
        parsed = urlsplit(url)
        if parsed.scheme in {"http", "https"} and (parsed.username or parsed.password):
            raise ValueError(
                "Remove credentials from the URL; enter them through the authentication prompt."
            )
        if not url or url.startswith("-") or "\n" in url:
            raise ValueError("Enter a repository URL or path.")

    def configure(self, repository, name, fetch_url, push_url=None, *, previous=None):
        if not name or name.startswith("-") or any(char.isspace() for char in name):
            raise ValueError("Enter a remote name without spaces or a leading dash.")
        self._runner.run(["check-ref-format", f"refs/remotes/{name}/branch"], cwd=repository)
        self.validate_url(fetch_url)
        if push_url:
            self.validate_url(push_url)
        if previous:
            if name != previous:
                self._runner.run(["remote", "rename", previous, name], cwd=repository)
            self._runner.run(["remote", "set-url", name, fetch_url], cwd=repository)
        else:
            self._runner.run(["remote", "add", name, fetch_url], cwd=repository)
        self._runner.run(
            ["remote", "set-url", "--push", name, push_url or fetch_url], cwd=repository
        )
        self.invalidate_repository(repository)

    def remove(self, repository, name):
        remote = next(
            item
            for item in self.list_remotes(repository, force_refresh=True).remotes
            if item.name == name
        )
        self.validate_url(remote.fetch_url)
        self.validate_url(remote.push_url)
        recovery = RecoveryService(self._runner)
        refs = self._runner.run(
            ["for-each-ref", "--format=%(objectname)", f"refs/remotes/{name}/"], cwd=repository
        ).stdout
        for oid in set(refs.splitlines()):
            recovery.retain(repository, oid, "removed-remote")
        directory = (
            Path(
                self._runner.run(["rev-parse", "--absolute-git-dir"], cwd=repository).stdout.strip()
            )
            / "gitqt-remote-backups"
        )
        directory.mkdir(exist_ok=True)
        (directory / f"{uuid4().hex}.json").write_text(
            json.dumps({"name": name, "fetch": remote.fetch_url, "push": remote.push_url})
        )
        self._runner.run(["remote", "remove", name], cwd=repository)
        self.invalidate_repository(repository)

    def force_preview(self, repository, remote, branch):
        self._runner.run(["check-ref-format", f"refs/heads/{branch}"], cwd=repository)
        url = self._runner.run(
            ["remote", "get-url", "--push", remote], cwd=repository
        ).stdout.strip()
        self._runner.run(["fetch", "--progress", "--", url, f"refs/heads/{branch}"], cwd=repository)
        oid = self._runner.run(["rev-parse", "FETCH_HEAD"], cwd=repository).stdout.strip()
        RecoveryService(self._runner).retain(repository, oid, "before-force-push")
        source = self._runner.run(["rev-parse", "HEAD"], cwd=repository).stdout.strip()
        removed = self._runner.run(
            ["log", "--format=%h %s", "-20", f"{source}..{oid}"], cwd=repository
        ).stdout
        counts = (
            self._runner.run(
                ["rev-list", "--left-right", "--count", f"{source}...{oid}"], cwd=repository
            )
            .stdout.strip()
            .split()
        )
        return ForcePushPreview(
            oid,
            source,
            url,
            f"{counts[0]} local commits to publish; {counts[1]} remote-only commits will leave"
            f" the branch.\nRemote tip {oid} is retained in Recovery.\n\n{removed}",
        )

    def _run_remote_operation(
        self,
        repository_path: str | Path,
        *,
        operation: str,
        remote: str,
        args: list[str],
    ) -> RemoteCommandResult:
        progress = [
            RemoteProgressEvent(level="info", message=f"{operation.title()} {remote} started."),
        ]
        try:
            result = self._runner.run(args, cwd=repository_path)
        except GitCommandFailure as exc:
            stderr = exc.result.stderr.strip()
            error_category = _classify_remote_error(operation=operation, stderr=stderr)
            progress.append(
                RemoteProgressEvent(
                    level="error",
                    message=(
                        f"{operation.title()} {remote} failed: {error_category.replace('_', ' ')}."
                    ),
                )
            )
            return RemoteCommandResult(
                success=False,
                operation=operation,
                remote=remote,
                message=_build_error_message(
                    operation=operation,
                    error_category=error_category,
                    stderr=stderr,
                ),
                elapsed_ms=exc.result.elapsed_ms,
                stderr=stderr,
                error_category=error_category,
                progress=progress,
            )

        stderr = result.stderr.strip()
        elapsed_seconds = result.elapsed_ms / 1000
        progress.append(
            RemoteProgressEvent(
                level="info",
                message=f"{operation.title()} {remote} completed in {elapsed_seconds:.1f}s.",
            )
        )
        return RemoteCommandResult(
            success=True,
            operation=operation,
            remote=remote,
            message=f"{operation.title()} completed successfully.",
            elapsed_ms=result.elapsed_ms,
            stderr=stderr,
            progress=progress,
        )


def _parse_remote_verbose_output(raw: str) -> list[RemoteInfo]:
    rows: dict[str, dict[str, str]] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[0], parts[1], parts[2]
        kind = kind.removeprefix("(").removesuffix(")")
        if name not in rows:
            rows[name] = {"fetch": "", "push": ""}
        if kind in {"fetch", "push"}:
            rows[name][kind] = url

    remotes: list[RemoteInfo] = []
    for name in sorted(rows):
        fetch_url = rows[name]["fetch"]
        push_url = rows[name]["push"] or fetch_url
        remotes.append(RemoteInfo(name=name, fetch_url=fetch_url, push_url=push_url))
    return remotes


def _classify_remote_error(*, operation: str, stderr: str) -> str:
    normalized = stderr.lower()
    if any(
        token in normalized
        for token in (
            "authentication failed",
            "could not read username",
            "permission denied (publickey)",
            "fatal: could not read from remote repository",
        )
    ):
        return "authentication_failed"
    if operation == "push" and "non-fast-forward" in normalized:
        return "non_fast_forward"
    if operation == "pull" and any(token in normalized for token in ("conflict", "merge conflict")):
        return "merge_conflict"
    network_tokens = ("could not resolve host", "failed to connect", "network")
    if any(token in normalized for token in network_tokens):
        return "network_error"
    return "command_failed"


def _build_error_message(*, operation: str, error_category: str, stderr: str) -> str:
    if error_category == "authentication_failed":
        return (
            "Authentication failed for the selected remote. "
            "Check your credentials, SSH key, or credential helper configuration."
        )
    if error_category == "non_fast_forward":
        return "Push rejected as non-fast-forward. Pull or rebase before pushing again."
    if error_category == "merge_conflict":
        return "Pull stopped because Git detected merge conflicts."
    if error_category == "network_error":
        return "Remote operation failed due to a network or connectivity issue."
    return stderr or f"Git {operation} failed."
