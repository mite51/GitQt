"""Previewed rebases with durable interactive plans and retained original tips."""

from __future__ import annotations

import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from gitqt.services.commit_operations import CommitOperationResult
from gitqt.services.conflict import ConflictService
from gitqt.services.git import GitCommandFailure, GitRunner
from gitqt.services.recovery import RecoveryService


@dataclass(frozen=True)
class RebaseCommit:
    oid: str
    message: str


@dataclass(frozen=True)
class RebaseStep:
    oid: str
    action: str = "pick"
    message: str = ""


@dataclass(frozen=True)
class RebasePreview:
    source: str
    target: str
    source_oid: str
    target_oid: str
    head_ref: str
    head_oid: str
    commits: tuple[RebaseCommit, ...]
    has_merges: bool


class RebaseService:
    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

    def _oid(self, repository, ref):
        return self.runner.run(
            ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
            cwd=repository,
        ).stdout.strip()

    def _plan_root(self, repository):
        git_dir = Path(
            self.runner.run(["rev-parse", "--absolute-git-dir"], cwd=repository).stdout.strip()
        )
        return git_dir / "gitqt-rebase-plans"

    def editor_environment(self, repository):
        root = self._plan_root(repository)
        active = root / "active"
        if active.is_file():
            name = active.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[0-9a-f]{32}", name):
                editor = root / name / "editor.sh"
                if editor.is_file():
                    return {"GIT_EDITOR": "sh " + shlex.quote(editor.as_posix())}
        return {"GIT_EDITOR": "true"}

    def cleanup_plan(self, repository):
        root = self._plan_root(repository)
        active = root / "active"
        if active.is_file():
            name = active.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[0-9a-f]{32}", name):
                shutil.rmtree(root / name, ignore_errors=True)
            active.unlink(missing_ok=True)

    def _ready(self, repository):
        conflict = ConflictService(self.runner).detect_conflicts(repository)
        if conflict.operation or conflict.has_conflicts:
            raise ValueError("Finish or abort the current repository operation before rebasing.")
        if self.runner.run(
            ["status", "--porcelain", "--untracked-files=all"], cwd=repository
        ).stdout:
            raise ValueError("Commit or stash your changes, including untracked files, first.")

    def preview(self, repository, source: str, target: str) -> RebasePreview:
        self._ready(repository)
        self.runner.run(["check-ref-format", f"refs/heads/{source}"], cwd=repository)
        if source.startswith("-") or not target or target.startswith("-"):
            raise ValueError("Choose a local source branch and a valid destination.")
        try:
            head_ref = self.runner.run(
                ["symbolic-ref", "--quiet", "HEAD"], cwd=repository
            ).stdout.strip()
        except GitCommandFailure as exc:
            if exc.result.exit_code == 1:
                raise ValueError("Check out a local branch before rebasing.") from exc
            raise
        source_ref = f"refs/heads/{source}"
        if source_ref == target:
            raise ValueError("Choose a destination different from the source branch.")
        worktrees = self.runner.run(["worktree", "list", "--porcelain"], cwd=repository).stdout
        checked_out = worktrees.splitlines().count(f"branch {source_ref}")
        if checked_out > (1 if head_ref == source_ref else 0):
            raise ValueError("The source branch is checked out in another worktree.")
        source_oid = self._oid(repository, source_ref)
        target_oid = self._oid(repository, target)
        head_oid = self._oid(repository, "HEAD")
        raw = self.runner.run(
            [
                "log",
                "--reverse",
                "--topo-order",
                "--max-count=501",
                "--format=%H%x00%P%x00%B%x00",
                f"{target_oid}..{source_oid}",
                "--",
            ],
            cwd=repository,
        ).stdout.split("\0")
        commits = []
        has_merges = False
        for index in range(0, len(raw) - 2, 3):
            oid, parents, message = raw[index : index + 3]
            oid = oid.strip()
            if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
                raise ValueError("This history contains a commit message that cannot be previewed.")
            if len(parents.split()) > 1:
                has_merges = True
            else:
                commits.append(RebaseCommit(oid, message.rstrip("\n")))
        if (len(raw) - 1) // 3 > 500:
            raise ValueError(
                "This rebase exceeds the 500-commit preview limit. Choose a nearer base."
            )
        return RebasePreview(
            source, target, source_oid, target_oid, head_ref, head_oid, tuple(commits), has_merges
        )

    @staticmethod
    def validate_steps(preview: RebasePreview, steps: tuple[RebaseStep, ...]) -> None:
        if preview.has_merges:
            raise ValueError(
                "Interactive rebase currently supports linear history. "
                "Use a regular rebase to flatten merge commits."
            )
        if not preview.commits:
            raise ValueError("There are no commits to edit for this destination.")
        expected = {commit.oid for commit in preview.commits}
        if len(steps) != len(expected) or {step.oid for step in steps} != expected:
            raise ValueError("The commit list changed. Reload the rebase preview.")
        kept = False
        for step in steps:
            if step.action not in {"pick", "reword", "squash", "fixup", "drop"}:
                raise ValueError("Unsupported rebase action.")
            if step.action in {"squash", "fixup"} and not kept:
                raise ValueError("Squash and fixup need a preceding commit that is not dropped.")
            if step.action == "reword" and (not step.message.strip() or "\0" in step.message):
                raise ValueError("Enter a non-empty commit message without NUL characters.")
            if step.action != "drop":
                kept = True

    def start(
        self, repository, preview: RebasePreview, steps: tuple[RebaseStep, ...] | None = None
    ) -> CommitOperationResult:
        # Recheck the checkout, refs, index, worktree, and plan inside the write job.
        fresh = self.preview(repository, preview.source, preview.target)
        if fresh != preview:
            raise ValueError("The repository changed since this preview. Reopen Rebase and retry.")
        if steps is not None:
            self.validate_steps(preview, steps)
        self.cleanup_plan(repository)
        recovery = RecoveryService(self.runner).retain(
            repository, preview.source_oid, "before-rebase"
        )
        plan_dir = None
        env = {"GIT_EDITOR": "true"}
        args = [
            "-c",
            "rebase.updateRefs=false",
            "-c",
            "rebase.autoStash=false",
            "-c",
            "rebase.autoSquash=false",
            "-c",
            "rebase.rebaseMerges=false",
            "-c",
            "rebase.abbreviateCommands=false",
            "rebase",
            "--merge",
            "--no-fork-point",
            "--reapply-cherry-picks",
            "--empty=keep",
        ]
        if steps is not None:
            git_dir = Path(
                self.runner.run(["rev-parse", "--absolute-git-dir"], cwd=repository).stdout.strip()
            )
            # These files must survive conflicts, app restarts, and Continue jobs.
            plan_dir = git_dir / "gitqt-rebase-plans" / uuid4().hex
            plan_dir.mkdir(parents=True)
            todo = []
            done = shlex.quote((git_dir / "rebase-merge" / "done").as_posix())
            editor = [f'case "$(tail -n 1 {done})" in']
            for index, step in enumerate(steps):
                todo.append(f"{step.action} {step.oid}")
                if step.action == "reword":
                    message_file = plan_dir / f"message-{index}.txt"
                    message_file.write_text(step.message + "\n", encoding="utf-8", newline="\n")
                    editor.append(
                        f'{shlex.quote("reword " + step.oid)}*) '
                        f'cp {shlex.quote(message_file.as_posix())} "$1" ;;'
                    )
            editor.append("esac")
            (plan_dir / "editor.sh").write_text(
                "\n".join(editor) + "\n", encoding="utf-8", newline="\n"
            )
            (plan_dir.parent / "active").write_text(plan_dir.name, encoding="utf-8")
            todo_file = plan_dir / "todo"
            todo_file.write_text("\n".join(todo) + "\n", encoding="utf-8", newline="\n")
            # Git invokes editors through its shell, including Git for Windows.
            env["GIT_SEQUENCE_EDITOR"] = "cp " + shlex.quote(todo_file.as_posix())
            env.update(self.editor_environment(repository))
            args.append("--interactive")
        args.extend([preview.target_oid, preview.source])
        try:
            self.runner.run(args, cwd=repository, env_overrides=env)
        except GitCommandFailure as exc:
            active = ConflictService(self.runner).detect_conflicts(repository).operation == "rebase"
            if active:
                return CommitOperationResult(
                    False,
                    "Rebase paused. Resolve and stage any conflicts, then use Continue, "
                    "Skip Commit, or Abort in Working Tree.\n\n" + exc.result.stderr.strip(),
                )
            raise
        finally:
            if plan_dir is not None:
                # Do not delete message files while a paused rebase still needs them.
                active = ConflictService(self.runner).detect_conflicts(repository).operation
                if active != "rebase":
                    self.cleanup_plan(repository)
        return CommitOperationResult(
            True,
            f"Rebased {preview.source} onto {preview.target}. "
            f"Original tip retained in Recovery ({recovery}).",
        )
