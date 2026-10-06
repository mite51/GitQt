from __future__ import annotations

from dataclasses import dataclass, field

from gitqt.services.stash import StashEntry, StashService
from gitqt.services.tag import TagInfo, TagService


@dataclass(slots=True)
class ReferencesActionResult:
    success: bool
    message: str


@dataclass(slots=True)
class ConfirmationState:
    action: str
    target_ref: str
    title: str
    body: str
    confirm_label: str
    recovery_hint: str


@dataclass(slots=True)
class ReferencesState:
    repository_path: str | None = None
    stashes: list[StashEntry] = field(default_factory=list)
    tags: list[TagInfo] = field(default_factory=list)
    selected_stash: str | None = None
    pending_confirmation: ConfirmationState | None = None
    recovery_hint: str | None = None
    message: str = "Open a repository to inspect stashes and tags."


class ReferencesViewModel:
    """State and workflows for stash and tag operations."""

    def __init__(self, stash_service: StashService, tag_service: TagService) -> None:
        self._stash_service = stash_service
        self._tag_service = tag_service
        self.state = ReferencesState()

    def load_repository(self, repository_path: str) -> ReferencesActionResult:
        self.state.repository_path = repository_path
        self._refresh_references()
        return ReferencesActionResult(success=True, message="References loaded.")

    def create_stash(
        self,
        *,
        message: str | None = None,
        include_untracked: bool = False,
    ) -> ReferencesActionResult:
        if self.state.repository_path is None:
            return ReferencesActionResult(success=False, message="No repository is loaded.")

        self._stash_service.create_stash(
            self.state.repository_path,
            message=message,
            include_untracked=include_untracked,
        )
        self._refresh_references()
        self.state.message = "Stash created."
        self.state.recovery_hint = (
            "Use stash apply to restore changes without removing the stash entry."
        )
        return ReferencesActionResult(success=True, message="Stash created.")

    def apply_stash(self, stash_ref: str) -> ReferencesActionResult:
        if self.state.repository_path is None:
            return ReferencesActionResult(success=False, message="No repository is loaded.")

        self._stash_service.apply_stash(self.state.repository_path, stash_ref)
        self._refresh_references()
        self.state.message = f"Applied {stash_ref}."
        self.state.recovery_hint = (
            "If conflicts appear, resolve them and keep the stash for another attempt."
        )
        return ReferencesActionResult(success=True, message=f"Applied {stash_ref}.")

    def request_pop_stash(self, stash_ref: str) -> ReferencesActionResult:
        if not self._stash_ref_exists(stash_ref):
            return ReferencesActionResult(success=False, message="Selected stash is not available.")
        self.state.pending_confirmation = ConfirmationState(
            action="stash_pop",
            target_ref=stash_ref,
            title="Pop stash",
            body=f"Pop {stash_ref}? Git will apply changes and remove this stash entry.",
            confirm_label="Pop Stash",
            recovery_hint=(
                "If pop fails or conflicts, resolve conflicts and recover prior state with "
                "`git reset --merge` or by applying another stash."
            ),
        )
        self.state.message = f"Confirmation required before popping {stash_ref}."
        self.state.recovery_hint = self.state.pending_confirmation.recovery_hint
        return ReferencesActionResult(success=True, message="Stash pop confirmation requested.")

    def request_drop_stash(self, stash_ref: str) -> ReferencesActionResult:
        if not self._stash_ref_exists(stash_ref):
            return ReferencesActionResult(success=False, message="Selected stash is not available.")
        self.state.pending_confirmation = ConfirmationState(
            action="stash_drop",
            target_ref=stash_ref,
            title="Drop stash",
            body=f"Drop {stash_ref}? This removes it from the stash list.",
            confirm_label="Drop Stash",
            recovery_hint=(
                "Dropped stashes are harder to restore. If needed, inspect `git reflog` "
                "to recover recently dropped stash commits."
            ),
        )
        self.state.message = f"Confirmation required before dropping {stash_ref}."
        self.state.recovery_hint = self.state.pending_confirmation.recovery_hint
        return ReferencesActionResult(success=True, message="Stash drop confirmation requested.")

    def resolve_confirmation(self, *, confirm: bool) -> ReferencesActionResult:
        pending = self.state.pending_confirmation
        if pending is None:
            return ReferencesActionResult(success=False, message="No confirmation is pending.")
        if self.state.repository_path is None:
            self.state.pending_confirmation = None
            return ReferencesActionResult(success=False, message="No repository is loaded.")
        if not confirm:
            self.state.pending_confirmation = None
            self.state.message = "Action cancelled."
            return ReferencesActionResult(success=True, message="Action cancelled.")

        if pending.action == "stash_pop":
            self._stash_service.pop_stash(self.state.repository_path, pending.target_ref)
            success_message = f"Popped {pending.target_ref}."
        elif pending.action == "stash_drop":
            self._stash_service.drop_stash(self.state.repository_path, pending.target_ref)
            success_message = f"Dropped {pending.target_ref}."
        else:
            self.state.pending_confirmation = None
            return ReferencesActionResult(success=False, message="Unsupported confirmation action.")

        self.state.pending_confirmation = None
        self._refresh_references()
        self.state.message = success_message
        self.state.recovery_hint = pending.recovery_hint
        return ReferencesActionResult(success=True, message=success_message)

    def create_tag(
        self,
        name: str,
        *,
        target: str | None = None,
        annotation: str | None = None,
    ) -> ReferencesActionResult:
        if self.state.repository_path is None:
            return ReferencesActionResult(success=False, message="No repository is loaded.")

        self._tag_service.create_tag(
            self.state.repository_path,
            name,
            target=target,
            annotation=annotation,
        )
        self._refresh_references()
        self.state.message = f"Tag {name} created."
        self.state.recovery_hint = "If this tag is incorrect, delete it with `git tag -d <name>`."
        return ReferencesActionResult(success=True, message=f"Tag {name} created.")

    def _refresh_references(self) -> None:
        if self.state.repository_path is None:
            self.state.stashes = []
            self.state.tags = []
            self.state.selected_stash = None
            self.state.pending_confirmation = None
            return

        stash_snapshot = self._stash_service.list_stashes(self.state.repository_path)
        tag_snapshot = self._tag_service.list_tags(self.state.repository_path)
        self.state.stashes = list(stash_snapshot.entries)
        self.state.tags = list(tag_snapshot.tags)
        available_stashes = {entry.ref for entry in self.state.stashes}
        if self.state.selected_stash not in available_stashes:
            self.state.selected_stash = self.state.stashes[0].ref if self.state.stashes else None
        if self.state.pending_confirmation and self.state.pending_confirmation.target_ref not in (
            available_stashes
        ):
            self.state.pending_confirmation = None

        stash_count = len(self.state.stashes)
        tag_count = len(self.state.tags)
        self.state.message = f"{stash_count} stashes and {tag_count} tags loaded."

    def _stash_ref_exists(self, stash_ref: str) -> bool:
        return stash_ref in {entry.ref for entry in self.state.stashes}
