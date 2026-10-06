"""Branch/tag lifecycle operations with retained, explicitly leased remote deletion."""

from dataclasses import dataclass

from gitqt.services.recovery import RecoveryService


@dataclass(frozen=True)
class RemoteRefPreview:
    url: str
    ref: str
    oid: str
    recovery_ref: str


class RefManagementService:
    def __init__(self, runner):
        self.runner = runner

    def validate(self, repo, kind, name):
        self.runner.run(["check-ref-format", f"refs/{kind}/{name}"], cwd=repo)

    def rename_branch(self, repo, old, new):
        self.validate(repo, "heads", new)
        self.runner.run(["branch", "-m", "--", old, new], cwd=repo)

    def track_branch(self, repo, remote_ref, local_name):
        self.validate(repo, "heads", local_name)
        self.validate(repo, "remotes", remote_ref)
        self.runner.run(
            ["switch", "--create", local_name, "--track", f"refs/remotes/{remote_ref}"], cwd=repo
        )

    def set_upstream(self, repo, local_name, remote_ref):
        self.validate(repo, "heads", local_name)
        self.validate(repo, "remotes", remote_ref)
        self.runner.run(
            ["branch", f"--set-upstream-to=refs/remotes/{remote_ref}", "--", local_name], cwd=repo
        )

    def inspect_tag(self, repo, name):
        self.validate(repo, "tags", name)
        oid = self.runner.run(
            ["rev-parse", "--verify", f"refs/tags/{name}"], cwd=repo
        ).stdout.strip()
        kind = self.runner.run(["cat-file", "-t", oid], cwd=repo).stdout.strip()
        details = self.runner.run(["cat-file", "-p", oid], cwd=repo).stdout
        return f"Tag: {name}\nObject: {oid}\nType: {kind}\n\n{details}"

    def checkout_tag(self, repo, name):
        self.validate(repo, "tags", name)
        self.runner.run(["switch", "--detach", f"refs/tags/{name}"], cwd=repo)

    def delete_tag(self, repo, name):
        self.validate(repo, "tags", name)
        RecoveryService(self.runner).retain(repo, f"refs/tags/{name}", "deleted-tag", kind="tag")
        self.runner.run(["tag", "--delete", "--", name], cwd=repo)

    def prepare_remote(self, repo, remote, kind, name):
        self.validate(repo, kind, name)
        url = self.runner.run(["remote", "get-url", "--push", remote], cwd=repo).stdout.strip()
        ref = f"refs/{kind}/{name}"
        raw = self.runner.run(["ls-remote", "--refs", "--", url, ref], cwd=repo).stdout
        exact = [row.split("\t", 1)[0] for row in raw.splitlines() if row.partition("\t")[2] == ref]
        if len(exact) != 1:
            raise ValueError(
                "The selected reference is missing from that destination. Refresh and select it"
                " again."
            )
        oid = exact[0]
        self.runner.run(["fetch", "--progress", "--", url, ref], cwd=repo)
        # Retaining the exact previewed object fails safely if the remote changed during fetch.
        saved = RecoveryService(self.runner).retain(
            repo, oid, "deleted-remote-ref", kind="tag" if kind == "tags" else "commit"
        )
        return RemoteRefPreview(url, ref, oid, saved)

    def delete_remote(self, repo, preview):
        self.runner.run(
            [
                "push",
                "--progress",
                f"--force-with-lease={preview.ref}:{preview.oid}",
                "--",
                preview.url,
                f":{preview.ref}",
            ],
            cwd=repo,
        )

    def prepare_tag_push(self, repo, remote, name):
        self.validate(repo, "tags", name)
        url = self.runner.run(["remote", "get-url", "--push", remote], cwd=repo).stdout.strip()
        oid = self.runner.run(
            ["rev-parse", "--verify", f"refs/tags/{name}"], cwd=repo
        ).stdout.strip()
        return RemoteRefPreview(url, f"refs/tags/{name}", oid, "")

    def push_tag(self, repo, preview):
        self.runner.run(
            ["push", "--progress", "--", preview.url, f"{preview.oid}:{preview.ref}"], cwd=repo
        )
