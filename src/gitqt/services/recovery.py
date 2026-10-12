"""Persistent, bounded recovery: retained commits/stashes and file backups."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from gitqt.services.git import GitCommandFailure
from gitqt.services.status import StatusService


@dataclass(frozen=True)
class RecoveryEntry:
    kind: str
    reference: str
    description: str


class RecoveryService:
    def __init__(self, runner):
        self.runner = runner

    def retain(self, repository, revision, label, *, kind="commit"):
        oid = self.runner.run(
            ["rev-parse", "--verify", f"{revision}^{{{'object' if kind == 'tag' else 'commit'}}}"],
            cwd=repository,
        ).stdout.strip()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        ref = f"refs/gitqt/recovery/{kind}/{stamp}-{label}-{uuid4().hex[:8]}"
        self.runner.run(["update-ref", ref, oid], cwd=repository)
        return ref

    def before_reset(self, repository, *, hard=False):
        if hard:
            index = self.runner.run(["ls-files", "--stage", "-z"], cwd=repository).stdout
            tree = self.runner.run(["ls-tree", "-r", "-z", "HEAD"], cwd=repository).stdout
            if any(record.startswith("160000 ") for record in (index + "\0" + tree).split("\0")):
                raise ValueError(
                    "Hard reset is unavailable in repositories containing submodules. GitQt "
                    "cannot yet back up submodule worktrees. Use a soft or mixed reset, or "
                    "manage this repository with an external Git tool."
                )
            untracked = self.runner.run(
                ["ls-files", "--others", "--exclude-standard", "-z"], cwd=repository
            ).stdout
            ignored = self.runner.run(
                ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], cwd=repository
            ).stdout
            if untracked or ignored:
                raise ValueError(
                    "Hard reset is unavailable while untracked or ignored files exist. Move them"
                    " out of the repository first; they cannot be recovered by a tracked-work"
                    " backup."
                )
        self.retain(repository, "HEAD", "before-reset")
        # stash create records the index and tracked worktree without changing either.
        oid = self.runner.run(
            ["stash", "create", "GitQt before reset"], cwd=repository
        ).stdout.strip()
        if oid:
            self.retain(repository, oid, "before-reset-work", kind="stash")

    def entries(self, repository):
        raw = self.runner.run(
            [
                "for-each-ref",
                "--sort=-refname",
                "--format=%(refname)%09%(subject)",
                "refs/gitqt/recovery/",
            ],
            cwd=repository,
        ).stdout
        entries = []
        for line in raw.splitlines():
            ref, _, subject = line.partition("\t")
            entries.append(
                RecoveryEntry(ref.split("/")[3], ref, f"{ref.rsplit('/', 1)[1]} — {subject}")
            )
        git_dir = Path(
            self.runner.run(["rev-parse", "--absolute-git-dir"], cwd=repository).stdout.strip()
        )
        for info in sorted((git_dir / "gitqt-discard-backups").glob("*/info.json"), reverse=True):
            try:
                meta = json.loads(info.read_text())
                entries.append(
                    RecoveryEntry("file", str(info.parent), f"{meta['path']} — {info.parent.name}")
                )
            except (OSError, ValueError, KeyError):
                continue
        for backup in (git_dir / "gitqt-remote-backups").glob("*.json"):
            try:
                data = json.loads(backup.read_text())
                entries.append(
                    RecoveryEntry("remote", str(backup), f"Removed remote: {data['name']}")
                )
            except (OSError, ValueError, KeyError):
                continue
        try:
            self.runner.run(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repository)
        except GitCommandFailure as exc:
            if exc.result.exit_code == 1:
                # File backups are useful even before the first commit creates HEAD.
                return entries
            raise
        log = self.runner.run(["reflog", "--format=%H%x09%gs", "-n", "100"], cwd=repository).stdout
        entries.extend(
            RecoveryEntry("commit", oid, f"Reflog: {message}")
            for line in log.splitlines()
            if (oid := line.partition("\t")[0]) and (message := line.partition("\t")[2])
        )
        return entries

    def recover(self, repository, entry, *, branch_name=None):
        if entry.kind == "remote":
            from gitqt.services.remote import RemoteService

            git_dir = Path(
                self.runner.run(["rev-parse", "--absolute-git-dir"], cwd=repository).stdout.strip()
            )
            backup = Path(entry.reference).resolve()
            if not backup.is_relative_to((git_dir / "gitqt-remote-backups").resolve()):
                raise ValueError("Invalid remote backup.")
            data = json.loads(backup.read_text())
            RemoteService(self.runner).configure(
                repository, data["name"], data["fetch"], data["push"]
            )
            return (
                "Remote URLs restored. Fetch to rebuild tracking references and set upstream when"
                " publishing."
            )
        if entry.kind == "tag":
            if not branch_name:
                raise ValueError("Enter a tag name.")
            self.runner.run(["check-ref-format", f"refs/tags/{branch_name}"], cwd=repository)
            oid = self.runner.run(["rev-parse", entry.reference], cwd=repository).stdout.strip()
            self.runner.run(
                ["update-ref", f"refs/tags/{branch_name}", oid, "0" * len(oid)], cwd=repository
            )
            return "Tag restored, including its original annotation/signature."
        if entry.kind == "commit":
            if not branch_name:
                raise ValueError("Enter a new branch name.")
            self.runner.run(["check-ref-format", "--branch", branch_name], cwd=repository)
            self.runner.run(["branch", "--", branch_name, entry.reference], cwd=repository)
            return f"Created {branch_name}; your current checkout is unchanged."
        if entry.kind == "stash":
            if self.runner.run(
                ["status", "--porcelain", "--untracked-files=all"], cwd=repository
            ).stdout:
                raise ValueError("Commit or stash current changes before recovering saved work.")
            self.runner.run(["stash", "apply", "--index", entry.reference], cwd=repository)
            return "Saved work restored. The recovery reference is retained."
        if entry.kind != "file":
            raise ValueError("Unsupported recovery entry.")
        root = Path(repository).resolve()
        git_dir = Path(
            self.runner.run(["rev-parse", "--absolute-git-dir"], cwd=root).stdout.strip()
        )
        backup = Path(entry.reference).resolve()
        if not backup.is_relative_to((git_dir / "gitqt-discard-backups").resolve()):
            raise ValueError("Invalid backup location.")
        meta = json.loads((backup / "info.json").read_text())
        target = root / meta["path"]
        if (
            target.is_symlink()
            or not target.resolve().is_relative_to(root)
            or target.resolve() == root
        ):
            raise ValueError("Cannot restore outside the working tree or through a symbolic link.")
        if target.resolve().is_relative_to(git_dir.resolve()) or ".git" in Path(meta["path"]).parts:
            raise ValueError("Cannot restore repository metadata.")
        # Keep newer edits recoverable before replacing this file.
        StatusService(self.runner)._backup_worktree_file(root, meta["path"])
        if meta["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((backup / "content").read_bytes())
            if meta.get("mode") is not None:
                os.chmod(target, meta["mode"])
        else:
            target.unlink(missing_ok=True)
        return "File restored; its previous contents were backed up. The index is unchanged."
