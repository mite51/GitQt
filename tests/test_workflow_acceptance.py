"""Acceptance regressions for staged renames, recovery, and repository drafts."""

import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from test_visible_workflows import conflict, git, repository_view, select_file, working_view

from gitqt.app.working_tree import WorkingTreeViewModel
from gitqt.services.commit import CommitService
from gitqt.services.conflict import ConflictService
from gitqt.services.recovery import RecoveryService
from gitqt.ui.recovery_dialog import RecoveryDialog
from gitqt.ui.working_tree_view import WorkingTreeView

pytest_plugins = ["test_visible_workflows"]


@pytest.mark.parametrize("destination", ["renamed.txt", "[literal]\tname.txt"])
def test_staged_rename_preview_and_unstage_preserve_both_paths(repo, service, destination):
    if os.name == "nt" and "\t" in destination:
        pytest.skip("Tabs are not permitted in Windows filenames")
    git(repo, "mv", "--", "file.txt", destination)
    preview = service.get_preview(repo, destination, staged=True)
    assert not preview.partial_supported
    assert "Renamed" in preview.message
    service.unstage_file(repo, destination)
    assert git(repo, "diff", "--cached").stdout == b""
    assert (repo / destination).read_text() == "one\ntwo\nthree\n"
    assert not (repo / "file.txt").exists()
    assert git(repo, "show", ":file.txt").stdout == b"one\ntwo\nthree\n"


def test_rename_unstage_keeps_other_staged_work_and_destination_edits(repo, service):
    git(repo, "mv", "file.txt", "renamed.txt")
    (repo / "renamed.txt").write_text("later unstaged edit\n")
    (repo / "other.txt").write_text("independent staged change\n")
    git(repo, "add", "other.txt")
    service.unstage_file(repo, "renamed.txt")
    assert git(repo, "diff", "--cached", "--name-only").stdout == b"other.txt\n"
    assert (repo / "renamed.txt").read_text() == "later unstaged edit\n"


def test_recovery_before_initial_commit_restores_file_backup(tmp_path, runner, service):
    repo = tmp_path / "unborn"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    recovery = RecoveryService(runner)
    assert recovery.entries(repo) == []
    (repo / "draft.txt").write_text("saved draft\n")
    backup = service._backup_worktree_file(repo, "draft.txt")
    (repo / "draft.txt").write_text("new draft\n")
    entry = next(entry for entry in recovery.entries(repo) if entry.reference == backup)
    recovery.recover(repo, entry)
    assert (repo / "draft.txt").read_text() == "saved draft\n"
    assert len(recovery.entries(repo)) == 2
    assert git(repo, "ls-files").stdout == b""


def test_switching_working_tree_models_does_not_edit_either_draft(app, service, runner):
    first = WorkingTreeViewModel(service, CommitService(runner))
    second = WorkingTreeViewModel(service, CommitService(runner))
    first.set_commit_message("First draft", "First body")
    second.set_commit_message("Second draft", "Second body")
    view = WorkingTreeView()
    view.set_view_model(first)
    view.set_view_model(second)
    assert (first.state.commit_summary, first.state.commit_body) == (
        "First draft",
        "First body",
    )
    assert (second.state.commit_summary, second.state.commit_body) == (
        "Second draft",
        "Second body",
    )
    assert view._summary_input.text() == "Second draft"
    assert view._body_input.toPlainText() == "Second body"
    view.set_view_model(first)
    assert view._summary_input.text() == "First draft"
    assert view._body_input.toPlainText() == "First body"
    view.close()


@pytest.mark.parametrize("operation", ["rebase", "cherry-pick", "revert"])
def test_operation_continue_control_survives_reopening(
    app, repo, runner, tmp_path, warnings, operation
):
    conflict(repo, operation)
    first = repository_view(repo, tmp_path)
    panel = first._working_tree
    assert not panel._continue_btn.isEnabled()
    (repo / "file.txt").write_text("resolved in desktop\n")
    select_file(panel)
    panel._stage_btn.click()
    assert panel._continue_btn.isEnabled()
    first.close()
    reopened = repository_view(repo, tmp_path)
    panel = reopened._working_tree
    assert reopened._working_tree_vm.state.conflict_operation == operation
    assert not panel._conflict_banner.isHidden()
    assert panel._continue_btn.isEnabled()
    assert not panel._commit_btn.isEnabled()
    panel._continue_btn.click()
    assert ConflictService(runner).detect_conflicts(repo).operation is None
    assert git(repo, "show", "HEAD:file.txt").stdout == b"resolved in desktop\n"
    assert panel._conflict_banner.isHidden()
    assert reopened._back_btn.isEnabled()
    assert warnings == []
    reopened.close()


def test_recovery_browser_restores_backup_and_retains_newer_edits(
    app, repo, service, tmp_path, monkeypatch
):
    (repo / "file.txt").write_text("saved file\n")
    backup = service._backup_worktree_file(repo, "file.txt")
    (repo / "file.txt").write_text("newer file\n")
    view = repository_view(repo, tmp_path)
    dialog = RecoveryDialog(view._services, str(repo), view)
    for row in range(dialog.entries.count()):
        entry = dialog.entries.item(row).data(Qt.ItemDataRole.UserRole)
        if entry.reference == backup:
            dialog.entries.setCurrentRow(row)
            break
    else:
        pytest.fail("File backup missing from Recovery browser")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.restore.click()
    assert (repo / "file.txt").read_text() == "saved file\n"
    assert "File restored" in dialog.message.text()
    assert dialog.restore.isEnabled()
    backups = [
        item
        for item in dialog.service.entries(repo)
        if item.kind == "file" and item.reference != backup
    ]
    assert any((Path(item.reference) / "content").read_text() == "newer file\n" for item in backups)
    assert git(repo, "diff", "--cached").stdout == b""
    dialog.close()
    view.close()


def test_rename_controls_disable_partial_actions_and_unstage_the_whole_rename(
    app, repo, service, warnings
):
    git(repo, "mv", "file.txt", "renamed.txt")
    view, _ = working_view(repo, service)
    select_file(view, staged=True, path="renamed.txt")
    assert "Renamed" in view._diff_view._empty_label.text()
    assert not view._diff_view._empty_label.isHidden()
    assert not view._diff_toolbar._stage_btn.isEnabled()
    assert not view._diff_toolbar._discard_btn.isEnabled()
    assert view._unstage_btn.isEnabled()
    view._unstage_btn.click()
    assert git(repo, "diff", "--cached").stdout == b""
    assert warnings == []
    view.close()


@pytest.mark.skipif(os.name == "nt", reason="Executable mode requires a POSIX filesystem")
def test_mode_only_change_has_explanation_and_whole_file_staging(app, repo, service, warnings):
    git(repo, "config", "core.filemode", "true")
    (repo / "file.txt").chmod(0o755)
    view, _ = working_view(repo, service)
    select_file(view)
    assert "metadata changed" in view._diff_view._empty_label.text()
    assert not view._diff_view._empty_label.isHidden()
    assert not view._diff_toolbar._stage_btn.isEnabled()
    assert not view._diff_toolbar._discard_btn.isEnabled()
    view._stage_btn.click()
    assert git(repo, "ls-files", "--stage", "file.txt").stdout.startswith(b"100755")
    assert warnings == []
    view.close()


def test_submodule_change_disables_partial_actions_but_stages_gitlink(app, repo, service, warnings):
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", str(repo), "module")
    git(repo, "commit", "-qm", "Local submodule")
    git(
        repo / "module",
        "-c",
        "user.name=Tester",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-qm",
        "Submodule change",
    )
    expected = git(repo / "module", "rev-parse", "HEAD").stdout.strip()
    view, _ = working_view(repo, service)
    select_file(view, path="module")
    assert "submodule" in view._diff_view._notice.text()
    assert not view._diff_view._notice.isHidden()
    assert not view._diff_toolbar._stage_btn.isEnabled()
    assert not view._diff_toolbar._discard_btn.isEnabled()
    view._stage_btn.click()
    assert git(repo, "ls-files", "--stage", "module").stdout.startswith(b"160000 " + expected)
    assert warnings == []
    view.close()


def test_application_activation_refresh_preserves_draft_and_selection_without_network(
    app, repo, tmp_path, monkeypatch
):
    (repo / "file.txt").write_text("first edit\n")
    view = repository_view(repo, tmp_path)
    view.show()
    view._observe_timer.stop()
    panel = view._working_tree
    panel._summary_input.setText("Keep my draft")
    panel._body_input.setPlainText("Keep my body")
    select_file(panel)
    calls = []
    runner = view._services.git_runner
    original = runner.run

    def record(args, **kwargs):
        calls.append(args)
        return original(args, **kwargs)

    monkeypatch.setattr(runner, "run", record)
    (repo / "file.txt").write_text("second external edit\n")
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    state = view._working_tree_vm.state
    assert state.selected_file == "file.txt" and not state.selected_staged
    assert "second external edit" in state.selected_preview.raw
    assert panel._summary_input.text() == "Keep my draft"
    assert panel._body_input.toPlainText() == "Keep my body"
    assert state.commit_summary == "Keep my draft"
    assert calls
    assert not any(args[0] in {"fetch", "pull", "push", "ls-remote"} for args in calls)
    view.close()
