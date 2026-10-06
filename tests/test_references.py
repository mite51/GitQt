from __future__ import annotations

from gitqt.app.references import ReferencesViewModel
from gitqt.services.stash import StashEntry, StashSnapshot
from gitqt.services.tag import TagInfo, TagSnapshot


class _FakeStashService:
    def __init__(self) -> None:
        self.entries = [StashEntry(ref="stash@{0}", description="WIP on main")]
        self.list_calls = 0
        self.apply_calls: list[tuple[str, str]] = []
        self.pop_calls: list[tuple[str, str]] = []
        self.drop_calls: list[tuple[str, str]] = []
        self.create_calls: list[tuple[str, str | None, bool]] = []

    def list_stashes(self, repository_path: str) -> StashSnapshot:  # noqa: ARG002
        self.list_calls += 1
        return StashSnapshot(entries=list(self.entries))

    def create_stash(
        self,
        repository_path: str,  # noqa: ARG002
        *,
        message: str | None,
        include_untracked: bool,
    ) -> None:
        self.create_calls.append((repository_path, message, include_untracked))
        next_index = len(self.entries)
        description = f"On main: {message or 'WIP'}"
        self.entries.insert(0, StashEntry(ref=f"stash@{{{next_index}}}", description=description))

    def apply_stash(self, repository_path: str, stash_ref: str) -> None:
        self.apply_calls.append((repository_path, stash_ref))

    def pop_stash(self, repository_path: str, stash_ref: str) -> None:
        self.pop_calls.append((repository_path, stash_ref))
        self.entries = [entry for entry in self.entries if entry.ref != stash_ref]

    def drop_stash(self, repository_path: str, stash_ref: str) -> None:
        self.drop_calls.append((repository_path, stash_ref))
        self.entries = [entry for entry in self.entries if entry.ref != stash_ref]


class _FakeTagService:
    def __init__(self) -> None:
        self.tags = [TagInfo(name="v0.1.0")]
        self.list_calls = 0
        self.create_calls: list[tuple[str, str, str | None, str | None]] = []

    def list_tags(self, repository_path: str) -> TagSnapshot:  # noqa: ARG002
        self.list_calls += 1
        return TagSnapshot(tags=list(self.tags))

    def create_tag(
        self,
        repository_path: str,
        name: str,
        *,
        target: str | None,
        annotation: str | None,
    ) -> None:
        self.create_calls.append((repository_path, name, target, annotation))
        self.tags.insert(0, TagInfo(name=name))


def test_references_view_model_tracks_confirmation_and_recovery_hints() -> None:
    stash_service = _FakeStashService()
    tag_service = _FakeTagService()
    view_model = ReferencesViewModel(stash_service, tag_service)  # type: ignore[arg-type]
    view_model.load_repository("/tmp/repo")

    request = view_model.request_drop_stash("stash@{0}")
    assert request.success
    assert view_model.state.pending_confirmation is not None
    assert view_model.state.pending_confirmation.action == "stash_drop"
    assert "reflog" in view_model.state.pending_confirmation.recovery_hint.lower()
    assert "reflog" in (view_model.state.recovery_hint or "").lower()

    cancel_result = view_model.resolve_confirmation(confirm=False)
    assert cancel_result.success
    assert view_model.state.pending_confirmation is None
    assert view_model.state.message == "Action cancelled."


def test_references_view_model_refreshes_after_stash_and_tag_workflows() -> None:
    stash_service = _FakeStashService()
    tag_service = _FakeTagService()
    view_model = ReferencesViewModel(stash_service, tag_service)  # type: ignore[arg-type]
    view_model.load_repository("/tmp/repo")
    initial_stash_list_calls = stash_service.list_calls
    initial_tag_list_calls = tag_service.list_calls

    stash_result = view_model.create_stash(message="session-7", include_untracked=True)
    assert stash_result.success
    assert stash_service.create_calls == [("/tmp/repo", "session-7", True)]
    assert stash_service.list_calls > initial_stash_list_calls
    assert any("session-7" in entry.description for entry in view_model.state.stashes)

    tag_result = view_model.create_tag("v0.2.0", annotation="beta release")
    assert tag_result.success
    assert tag_service.create_calls == [("/tmp/repo", "v0.2.0", None, "beta release")]
    assert tag_service.list_calls > initial_tag_list_calls
    assert any(tag.name == "v0.2.0" for tag in view_model.state.tags)

    pop_request = view_model.request_pop_stash(view_model.state.stashes[0].ref)
    assert pop_request.success
    pop_result = view_model.resolve_confirmation(confirm=True)
    assert pop_result.success
    assert stash_service.pop_calls
    assert view_model.state.pending_confirmation is None
    assert "git reset --merge" in (view_model.state.recovery_hint or "")
