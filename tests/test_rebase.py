"""Rebase workflows against real repositories, including paused interactive plans."""

from dataclasses import replace

import pytest
from test_visible_workflows import git

from gitqt.services.branch import BranchService
from gitqt.services.commit_operations import CommitOperationsService
from gitqt.services.conflict import ConflictService
from gitqt.services.rebase import RebaseService, RebaseStep
from gitqt.services.recovery import RecoveryService
from gitqt.services.worker_pool import ImmediateWorkerPool

pytest_plugins = ["test_visible_workflows"]


def commit(repo, name, content=None):
    (repo / name).write_text(content or name, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-qm", name)
    return git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def branch_history(repo):
    git(repo, "checkout", "-qb", "feature")
    commits = [commit(repo, name) for name in ("first", "second", "third")]
    git(repo, "checkout", "main")
    commit(repo, "upstream")
    git(repo, "checkout", "feature")
    return commits


def test_branch_rebase_remote_destination_and_retained_tip(repo, runner):
    branch_history(repo)
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    # A similarly named tag must not affect destination selection.
    git(repo, "tag", "main", "feature")
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/remotes/origin/main")
    assert [c.message for c in preview.commits] == ["first", "second", "third"]
    result = service.start(repo, preview)
    assert result.success
    assert git(repo, "merge-base", "--is-ancestor", preview.target_oid, "feature").returncode == 0
    assert (repo / "upstream").exists()
    retained = [e for e in RecoveryService(runner).entries(repo) if "before-rebase" in e.reference]
    assert len(retained) == 1
    assert (
        git(repo, "rev-parse", retained[0].reference).stdout.decode().strip() == preview.source_oid
    )


def test_rebase_noncurrent_branch_checks_it_out_without_moving_current_branch(repo, runner):
    branch_history(repo)
    git(repo, "checkout", "main")
    old_main = git(repo, "rev-parse", "main").stdout
    service = RebaseService(runner)
    assert service.start(repo, service.preview(repo, "feature", "refs/heads/main")).success
    assert git(repo, "branch", "--show-current").stdout.strip() == b"feature"
    assert git(repo, "rev-parse", "main").stdout == old_main


def test_interactive_reorders_rewords_and_drops_without_interpreting_message(repo, runner):
    branch_history(repo)
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    a, b, c = preview.commits
    message = "Renamed café $(touch unexpected)\n\nBody with `literal` and 'quotes'."
    steps = (RebaseStep(c.oid), RebaseStep(a.oid, "reword", message), RebaseStep(b.oid, "drop"))
    assert service.start(repo, preview, steps).success
    subjects = (
        git(repo, "log", "--reverse", "--format=%s", "main..feature").stdout.decode().splitlines()
    )
    assert subjects == ["third", message.splitlines()[0]]
    assert git(repo, "show", "-s", "--format=%B", "HEAD").stdout.decode().strip() == message
    assert not (repo / "second").exists()
    assert not (repo / "unexpected").exists()
    assert not list((repo / ".git" / "gitqt-rebase-plans").glob("*/todo"))


@pytest.mark.parametrize("action", ["squash", "fixup"])
def test_interactive_combines_commits(repo, runner, action):
    branch_history(repo)
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    a, b, c = preview.commits
    assert service.start(
        repo, preview, (RebaseStep(a.oid), RebaseStep(b.oid, action), RebaseStep(c.oid, "drop"))
    ).success
    assert git(repo, "rev-list", "--count", "main..feature").stdout.strip() == b"1"
    message = git(repo, "show", "-s", "--format=%B", "HEAD").stdout.decode()
    assert ("second" in message) == (action == "squash")
    assert (repo / "first").exists() and (repo / "second").exists()


def conflicting_history(repo):
    git(repo, "checkout", "-qb", "feature")
    commit(repo, "file.txt", "feature change\n")
    commit(repo, "later")
    git(repo, "checkout", "main")
    commit(repo, "file.txt", "upstream change\n")
    git(repo, "checkout", "feature")


def test_conflict_continue_after_service_restart_keeps_reword_plan(repo, runner):
    conflicting_history(repo)
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    steps = tuple(RebaseStep(c.oid, "reword", f"Edited {i}") for i, c in enumerate(preview.commits))
    result = service.start(repo, preview, steps)
    assert not result.success and "Rebase paused" in result.message
    assert ConflictService(runner).detect_conflicts(repo).operation == "rebase"
    assert list((repo / ".git" / "gitqt-rebase-plans").glob("*/message-0.txt"))
    (repo / "file.txt").write_text("resolved\n")
    git(repo, "add", "file.txt")
    result = CommitOperationsService(runner).resolve_operation(repo, "rebase", "continue")
    assert result.success, result.message
    assert git(repo, "log", "--reverse", "--format=%s", "main..feature").stdout.splitlines() == [
        b"Edited 0",
        b"Edited 1",
    ]
    assert not list((repo / ".git" / "gitqt-rebase-plans").glob("*/todo"))


@pytest.mark.parametrize("action", ["abort", "skip"])
def test_conflict_abort_and_skip(repo, runner, action):
    conflicting_history(repo)
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    steps = tuple(RebaseStep(c.oid) for c in preview.commits)
    assert not service.start(repo, preview, steps).success
    result = CommitOperationsService(runner).resolve_operation(repo, "rebase", action)
    assert result.success, result.message
    if action == "abort":
        assert git(repo, "rev-parse", "HEAD").stdout.decode().strip() == preview.source_oid
    else:
        assert (repo / "file.txt").read_text() == "upstream change\n"
        assert (repo / "later").exists()
    assert ConflictService(runner).detect_conflicts(repo).operation is None


@pytest.mark.parametrize("change", ["source", "target", "checkout", "dirty", "untracked"])
def test_rebase_rejects_stale_preview_or_unsaved_work(repo, runner, change):
    branch_history(repo)
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    if change == "source":
        commit(repo, "new")
    elif change == "target":
        git(repo, "update-ref", "refs/heads/main", "HEAD")
    elif change == "checkout":
        git(repo, "checkout", "main")
    elif change == "dirty":
        (repo / "file.txt").write_text("unsaved")
    else:
        (repo / "untracked").write_text("unsaved")
    old_head = git(repo, "rev-parse", "HEAD").stdout
    with pytest.raises(ValueError):
        service.start(repo, preview)
    assert git(repo, "rev-parse", "HEAD").stdout == old_head
    assert ConflictService(runner).detect_conflicts(repo).operation is None


def test_plan_validation_rejects_missing_duplicate_and_leading_squash(repo, runner):
    branch_history(repo)
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    steps = tuple(RebaseStep(c.oid) for c in preview.commits)
    for invalid in (
        steps[:-1],
        (steps[0], steps[0], steps[2]),
        (replace(steps[0], action="squash"), *steps[1:]),
        (replace(steps[0], action="reword", message=""), *steps[1:]),
    ):
        with pytest.raises(ValueError):
            service.start(repo, preview, invalid)
    assert git(repo, "rev-parse", "HEAD").stdout.decode().strip() == preview.source_oid


def test_all_dropped_commits_move_branch_to_destination(repo, runner):
    branch_history(repo)
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    assert service.start(
        repo, preview, tuple(RebaseStep(c.oid, "drop") for c in preview.commits)
    ).success
    assert git(repo, "rev-parse", "feature").stdout == git(repo, "rev-parse", "main").stdout


def test_source_checked_out_in_another_worktree_is_rejected(repo, runner, tmp_path):
    branch_history(repo)
    git(repo, "checkout", "main")
    git(repo, "worktree", "add", str(tmp_path / "linked"), "feature")
    with pytest.raises(ValueError, match="another worktree"):
        RebaseService(runner).preview(repo, "feature", "refs/heads/main")


def test_dialog_edits_reorders_and_validates_before_accepting(repo, runner, app):
    from gitqt.ui.rebase_dialog import RebaseDialog

    branch_history(repo)
    dialog = RebaseDialog(
        runner,
        ImmediateWorkerPool(),
        str(repo),
        BranchService(runner).get_snapshot(repo),
        source="feature",
        target="refs/heads/main",
        interactive=True,
    )
    assert dialog.start.isEnabled()
    original = dialog.steps()
    dialog.down.click()
    assert dialog.steps()[1].oid == original[0].oid
    dialog.action.setCurrentIndex(dialog.action.findData("reword"))
    dialog.message.setPlainText("New subject\n\nBody")
    assert dialog.steps()[1].message == "New subject\n\nBody"
    dialog.commits.setCurrentRow(0)
    dialog.action.setCurrentIndex(dialog.action.findData("squash"))
    assert not dialog.start.isEnabled()
    assert "preceding" in dialog.error.text()
    dialog.interactive.setChecked(False)
    assert dialog.start.isEnabled() and dialog.steps() is None
    dialog.reject()


def test_dialog_cancel_leaves_repository_unchanged(repo, runner, app):
    from gitqt.ui.rebase_dialog import RebaseDialog

    branch_history(repo)
    old_head = git(repo, "rev-parse", "HEAD").stdout
    dialog = RebaseDialog(
        runner,
        ImmediateWorkerPool(),
        str(repo),
        BranchService(runner).get_snapshot(repo),
        source="feature",
        target="refs/heads/main",
        interactive=True,
    )
    dialog.reject()
    assert git(repo, "rev-parse", "HEAD").stdout == old_head
    assert not list((repo / ".git" / "gitqt-rebase-plans").glob("*"))


def test_skipping_reword_conflict_does_not_rename_destination(repo, runner):
    conflicting_history(repo)
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    main_message = git(repo, "show", "-s", "--format=%B", "main").stdout
    first, later = preview.commits
    assert not service.start(
        repo,
        preview,
        (
            RebaseStep(first.oid, "reword", "Should be skipped"),
            RebaseStep(later.oid, "reword", "Later edited"),
        ),
    ).success
    assert CommitOperationsService(runner).resolve_operation(repo, "rebase", "skip").success
    assert git(repo, "show", "-s", "--format=%B", "HEAD^").stdout == main_message
    assert git(repo, "log", "--format=%s", "main..feature").stdout.strip() == b"Later edited"


def test_linked_worktree_and_quoted_unicode_paths(repo, runner, tmp_path):
    branch_history(repo)
    git(repo, "checkout", "main")
    linked = tmp_path / "linked café's $work"
    git(repo, "worktree", "add", str(linked), "feature")
    service = RebaseService(runner)
    preview = service.preview(linked, "feature", "refs/heads/main")
    steps = tuple(
        RebaseStep(c.oid, "reword", f"Changed {i}") for i, c in enumerate(preview.commits)
    )
    assert service.start(linked, preview, steps).success
    assert git(linked, "log", "--reverse", "--format=%s", "main..feature").stdout.splitlines() == [
        b"Changed 0",
        b"Changed 1",
        b"Changed 2",
    ]


def test_rebase_configuration_cannot_rewrite_other_branches_or_abbreviate_editor_steps(
    repo, runner
):
    branch_history(repo)
    git(repo, "branch", "other", "HEAD~1")
    other = git(repo, "rev-parse", "other").stdout
    for name in ("rebase.updateRefs", "rebase.abbreviateCommands", "rebase.autoSquash"):
        git(repo, "config", name, "true")
    git(repo, "config", "rebase.backend", "apply")
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    assert service.start(
        repo,
        preview,
        tuple(RebaseStep(c.oid, "reword", f"Updated {i}") for i, c in enumerate(preview.commits)),
    ).success
    assert git(repo, "rev-parse", "other").stdout == other
    assert git(repo, "log", "--format=%s", "-1").stdout.strip() == b"Updated 2"


def test_merge_history_requires_regular_rebase(repo, runner):
    branch_history(repo)
    git(repo, "checkout", "-qb", "side", "feature~2")
    commit(repo, "side-file")
    git(repo, "checkout", "feature")
    git(repo, "merge", "--no-ff", "-m", "Merge side", "side")
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    assert preview.has_merges
    with pytest.raises(ValueError, match="linear history"):
        service.start(repo, preview, tuple(RebaseStep(c.oid) for c in preview.commits))
    assert service.start(repo, preview).success
    assert (repo / "side-file").exists()
    assert not git(repo, "rev-list", "--merges", "main..feature").stdout


def test_empty_replay_fast_forwards_and_interactive_explains_no_commits(repo, runner):
    git(repo, "branch", "feature")
    commit(repo, "upstream")
    service = RebaseService(runner)
    preview = service.preview(repo, "feature", "refs/heads/main")
    assert not preview.commits
    with pytest.raises(ValueError, match="no commits"):
        service.start(repo, preview, ())
    assert service.start(repo, preview).success
    assert git(repo, "rev-parse", "feature").stdout == git(repo, "rev-parse", "main").stdout


def test_sidebar_menus_offer_both_modes_with_unambiguous_branch_refs(
    repo, runner, app, monkeypatch
):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMenu

    from gitqt.ui.sidebar import Sidebar

    branch_history(repo)
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    sidebar = Sidebar(split_branch_paths=False)
    sidebar.set_branch_snapshot(BranchService(runner).get_snapshot(repo))
    sidebar.show()
    app.processEvents()
    requests = []
    sidebar.rebase_requested.connect(lambda *args: requests.append(args))
    menus = []

    class TestMenu(QMenu):
        def exec(self, *_):
            actions = [a for a in self.actions() if "onto" in a.text()]
            menus.extend(a.text() for a in actions)
            actions[-1].trigger()

    monkeypatch.setattr("gitqt.ui.sidebar.QMenu", TestMenu)
    main = next(
        sidebar._local_root.child(i)
        for i in range(sidebar._local_root.childCount())
        if sidebar._local_root.child(i).data(0, Qt.ItemDataRole.UserRole)[1] == "main"
    )
    sidebar._show_context_menu(sidebar._tree.visualItemRect(main).center())
    assert requests[-1] == ("feature", "refs/heads/main", True)
    assert any(text.startswith("Rebase") for text in menus)
    sidebar._branch_drop_menu("feature", "refs/remotes/origin/main")
    assert requests[-1] == ("feature", "refs/remotes/origin/main", True)
    sidebar.close()


def test_branch_drop_rejects_invalid_targets_and_does_not_move_items(repo, runner, app):
    from types import SimpleNamespace

    from PySide6.QtCore import QMimeData, QPointF, Qt

    from gitqt.ui.sidebar import Sidebar

    branch_history(repo)
    sidebar = Sidebar(split_branch_paths=False)
    sidebar.set_branch_snapshot(BranchService(runner).get_snapshot(repo))
    sidebar.show()
    app.processEvents()
    tree = sidebar._tree
    mime = QMimeData()
    mime.setData(tree.MIME, b"feature")
    source = tree
    target = next(
        sidebar._local_root.child(i)
        for i in range(sidebar._local_root.childCount())
        if sidebar._local_root.child(i).data(0, Qt.ItemDataRole.UserRole)[1] == "main"
    )
    event = SimpleNamespace(
        source=lambda: source,
        mimeData=lambda: mime,
        position=lambda: QPointF(tree.visualItemRect(target).center()),
        acceptProposedAction=lambda: None,
        ignore=lambda: None,
    )
    requests = []
    tree.branch_dropped.disconnect()
    tree.branch_dropped.connect(lambda *args: requests.append(args))
    assert tree._drop_pair(event) == ("feature", "refs/heads/main")
    tree.dropEvent(event)
    assert requests == [("feature", "refs/heads/main")]
    assert target.parent() is sidebar._local_root
    mime.setData(tree.MIME, b"main")
    assert tree._drop_pair(event) is None
    mime.setData(tree.MIME, b"feature")
    source = None
    assert tree._drop_pair(event) is None
    source = tree
    target = sidebar._local_root
    assert tree._drop_pair(event) is None
    sidebar.close()


def test_repository_rebase_entry_point_runs_accepted_interactive_plan(
    repo, tmp_path, app, monkeypatch, warnings
):
    from gitqt.app.bootstrap import build_services
    from gitqt.ui.rebase_dialog import RebaseDialog
    from gitqt.ui.repository_view import RepositoryView

    branch_history(repo)
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    view = RepositoryView(services)
    view.load_repository(str(repo))

    def accept_plan(dialog):
        assert dialog.preview is not None
        dialog.action.setCurrentIndex(dialog.action.findData("reword"))
        dialog.message.setPlainText("Changed through UI")
        return True

    monkeypatch.setattr(RebaseDialog, "exec", accept_plan)
    view._sidebar.rebase_requested.emit("feature", "refs/heads/main", True)
    assert not warnings
    assert git(repo, "log", "--reverse", "--format=%s", "main..feature").stdout.splitlines()[0] == (
        b"Changed through UI"
    )
    assert view._branch_snapshot.tracking.branch_name == "feature"
    view.close()
