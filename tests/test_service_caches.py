from __future__ import annotations

from pathlib import Path

from gitqt.services.branch import BranchService
from gitqt.services.commit import CommitAuthor, CommitService
from gitqt.services.git import GitCommandFailure, GitCommandResult
from gitqt.services.history import HistoryService
from gitqt.services.remote import RemoteService
from gitqt.services.stash import StashService
from gitqt.services.tag import TagService


class _Clock:
    """Monotonic test clock that can be advanced manually."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def _result(stdout: str = "") -> GitCommandResult:
    return GitCommandResult(
        args=("git",),
        stdout=stdout,
        stderr="",
        exit_code=0,
        elapsed_ms=1,
    )


class _ScriptedRunner:
    """Test runner that returns canned responses keyed by command prefix."""

    def __init__(self, responses: dict[tuple[str, ...], str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path,  # noqa: ARG002
        input_text: str | None = None,  # noqa: ARG002
    ) -> GitCommandResult:
        key = tuple(args)
        self.calls.append(key)
        for prefix, stdout in self._responses.items():
            if key[: len(prefix)] == prefix:
                return _result(stdout)
        return _result("")


# ---------------------------------------------------------------------------
# BranchService
# ---------------------------------------------------------------------------


def test_branch_service_caches_snapshot_until_ttl_expires() -> None:
    runner = _ScriptedRunner(
        {
            (
                "branch",
                "--format=%(refname:short)|%(upstream:short)|%(HEAD)|%(objectname:short)",
            ): "main|origin/main|*|abcdef0\n",
            ("branch", "-r"): "origin/main|abcdef0\n",
            ("rev-parse",): "abcdef0\n",
            ("rev-list",): "0\t0\n",
        }
    )
    clock = _Clock(start=10.0)
    service = BranchService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=1.0,
        monotonic_func=clock,
    )

    service.get_snapshot("/tmp/repo")
    service.get_snapshot("/tmp/repo")
    clock.advance(2.0)
    service.get_snapshot("/tmp/repo")

    branch_list_calls = [c for c in runner.calls if c[0] == "branch" and "-r" not in c]
    assert len(branch_list_calls) == 2


def test_branch_service_invalidate_drops_cached_snapshot() -> None:
    runner = _ScriptedRunner(
        {
            (
                "branch",
                "--format=%(refname:short)|%(upstream:short)|%(HEAD)|%(objectname:short)",
            ): "main|origin/main|*|abcdef0\n",
            ("branch", "-r"): "",
            ("rev-parse",): "abcdef0\n",
            ("rev-list",): "0\t0\n",
        }
    )
    service = BranchService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.get_snapshot("/tmp/repo")
    service.invalidate_repository("/tmp/repo")
    service.get_snapshot("/tmp/repo")

    branch_list_calls = [c for c in runner.calls if c[0] == "branch" and "-r" not in c]
    assert len(branch_list_calls) == 2


def test_branch_service_force_refresh_bypasses_cache() -> None:
    runner = _ScriptedRunner(
        {
            (
                "branch",
                "--format=%(refname:short)|%(upstream:short)|%(HEAD)|%(objectname:short)",
            ): "main|origin/main|*|abcdef0\n",
            ("branch", "-r"): "",
            ("rev-parse",): "abcdef0\n",
            ("rev-list",): "0\t0\n",
        }
    )
    service = BranchService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.get_snapshot("/tmp/repo")
    service.get_snapshot("/tmp/repo", force_refresh=True)

    branch_list_calls = [c for c in runner.calls if c[0] == "branch" and "-r" not in c]
    assert len(branch_list_calls) == 2


# ---------------------------------------------------------------------------
# HistoryService
# ---------------------------------------------------------------------------


def test_history_service_caches_per_repo_limit_and_all_refs() -> None:
    runner = _ScriptedRunner({("log",): ""})
    service = HistoryService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.get_graph("/tmp/repo", limit=200)
    service.get_graph("/tmp/repo", limit=200)
    service.get_graph("/tmp/repo", limit=500)
    service.get_graph("/tmp/repo", limit=500, all_refs=False)

    log_calls = [c for c in runner.calls if c[0] == "log"]
    assert len(log_calls) == 3


def test_history_service_invalidate_clears_all_entries_for_repo() -> None:
    runner = _ScriptedRunner({("log",): ""})
    service = HistoryService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.get_graph("/tmp/repo", limit=200)
    service.get_graph("/tmp/repo", limit=500)
    service.invalidate_repository("/tmp/repo")
    service.get_graph("/tmp/repo", limit=200)

    log_calls = [c for c in runner.calls if c[0] == "log"]
    assert len(log_calls) == 3


def test_history_service_ref_filter_drops_all_flag_and_appends_ref() -> None:
    runner = _ScriptedRunner({("log",): ""})
    service = HistoryService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.get_graph("/tmp/repo", limit=200, ref="feature/x")

    assert len(runner.calls) == 1
    log_args = runner.calls[0]
    assert "--all" not in log_args
    assert log_args[-1] == "feature/x"


def test_history_service_ref_filter_cache_key_is_ref_specific() -> None:
    runner = _ScriptedRunner({("log",): ""})
    service = HistoryService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.get_graph("/tmp/repo", limit=200)  # all refs
    service.get_graph("/tmp/repo", limit=200, ref="main")
    service.get_graph("/tmp/repo", limit=200, ref="main")  # cached
    service.get_graph("/tmp/repo", limit=200, ref="feature/x")

    log_calls = [c for c in runner.calls if c[0] == "log"]
    assert len(log_calls) == 3


# ---------------------------------------------------------------------------
# RemoteService
# ---------------------------------------------------------------------------


def test_remote_service_caches_list_until_invalidated() -> None:
    runner = _ScriptedRunner({("remote",): "origin\tgit@example.com (fetch)\n"})
    service = RemoteService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.list_remotes("/tmp/repo")
    service.list_remotes("/tmp/repo")
    service.invalidate_repository("/tmp/repo")
    service.list_remotes("/tmp/repo")

    remote_calls = [c for c in runner.calls if c[0] == "remote"]
    assert len(remote_calls) == 2


# ---------------------------------------------------------------------------
# StashService
# ---------------------------------------------------------------------------


def test_stash_service_caches_list_and_invalidates_on_writes() -> None:
    runner = _ScriptedRunner(
        {
            ("stash", "list"): "stash@{0}|WIP on main\n",
            ("stash", "push"): "",
            ("stash", "apply"): "",
            ("stash", "pop"): "",
            ("stash", "drop"): "",
        }
    )
    service = StashService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.list_stashes("/tmp/repo")
    service.list_stashes("/tmp/repo")
    service.create_stash("/tmp/repo", message="wip")
    service.list_stashes("/tmp/repo")
    service.apply_stash("/tmp/repo")
    service.list_stashes("/tmp/repo")

    list_calls = [c for c in runner.calls if c[0] == "stash" and c[1] == "list"]
    assert len(list_calls) == 3


# ---------------------------------------------------------------------------
# TagService
# ---------------------------------------------------------------------------


def test_tag_service_caches_list_and_invalidates_on_create() -> None:
    runner = _ScriptedRunner(
        {
            ("tag", "--list"): "v1.0.0\nv1.1.0\n",
            ("tag",): "",
        }
    )
    service = TagService(
        runner,  # type: ignore[arg-type]
        cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    service.list_tags("/tmp/repo")
    service.list_tags("/tmp/repo")
    service.create_tag("/tmp/repo", "v2.0.0")
    service.list_tags("/tmp/repo")

    list_calls = [c for c in runner.calls if c[0] == "tag" and len(c) > 1 and c[1] == "--list"]
    assert len(list_calls) == 2


# ---------------------------------------------------------------------------
# CommitService.get_author
# ---------------------------------------------------------------------------


def test_commit_author_uses_single_get_regexp_subprocess() -> None:
    runner = _ScriptedRunner(
        {
            ("config", "--get-regexp"): "user.name Test User\nuser.email test@example.com\n",
        }
    )
    service = CommitService(
        runner,  # type: ignore[arg-type]
        author_cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    author = service.get_author("/tmp/repo")
    author_again = service.get_author("/tmp/repo")

    assert author == CommitAuthor(name="Test User", email="test@example.com")
    assert author_again == author
    config_calls = [c for c in runner.calls if c[0] == "config"]
    assert len(config_calls) == 1


def test_commit_author_handles_git_failure_gracefully() -> None:
    class _FailingRunner:
        calls: list[tuple[str, ...]] = []

        def run(
            self,
            args: list[str],
            *,
            cwd: str | Path,  # noqa: ARG002
            input_text: str | None = None,  # noqa: ARG002
        ):
            self.calls.append(tuple(args))
            raise GitCommandFailure(
                GitCommandResult(
                    args=("git", *args),
                    stdout="",
                    stderr="not in a repo",
                    exit_code=128,
                    elapsed_ms=1,
                )
            )

    runner = _FailingRunner()
    service = CommitService(
        runner,  # type: ignore[arg-type]
        author_cache_ttl_seconds=60.0,
        monotonic_func=_Clock(),
    )

    author = service.get_author("/tmp/repo")

    assert author == CommitAuthor(name="", email="")
