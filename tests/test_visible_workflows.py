"""Regression coverage from visible controls through real Git repositories."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QMessageBox

from gitqt.app.bootstrap import build_services
from gitqt.app.working_tree import WorkingTreeViewModel
from gitqt.parsers.diff import DiffLineType
from gitqt.services.commit_operations import CommitOperationsService
from gitqt.services.conflict import ConflictService
from gitqt.services.git import GitCommandLaunchError, GitCommandTimeoutError, GitLocator, GitRunner
from gitqt.services.status import StatusService
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.repository_view import RepositoryView
from gitqt.ui.working_tree_view import WorkingTreeView


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=check)


def blob(repo: Path, revision: str) -> bytes:
    return git(repo, "show", revision).stdout


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    for name, value in (
        ("user.name", "Test User"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        git(path, "config", name, value)
    (path / "file.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", "Initial")
    return path


@pytest.fixture
def runner():
    return GitRunner(GitLocator().discover())


@pytest.fixture
def service(runner):
    return StatusService(runner, cache_ttl_seconds=0)


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def warnings(monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))
    return messages


def working_view(repo, service):
    vm = WorkingTreeViewModel(service)
    vm.load_repository(str(repo))
    view = WorkingTreeView()
    view._jobs.pool = ImmediateWorkerPool()
    view.set_view_model(vm)
    return view, vm


def select_file(view, *, staged=False, path="file.txt"):
    widget = view._staged_list if staged else view._unstaged_list
    for index in range(widget.count()):
        item = widget.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == path:
            widget.setCurrentItem(item)
            return
    raise AssertionError(f"Missing file {path}")


def select_changed_line(view, text):
    for block, (hunk, index) in view._diff_view._blocks.items():
        if index < 0:
            continue
        line = view._view_model.state.selected_diff[hunk].lines[index]
        if line.text == text and line.type == DiffLineType.ADDITION:
            document = view._diff_view._text.document()
            block = document.findBlockByNumber(block)
            cursor = QTextCursor(document)
            cursor.setPosition(block.position())
            cursor.setPosition(
                block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor
            )
            view._diff_view._text.setTextCursor(cursor)
            return
    raise AssertionError("Changed line was not rendered")


def test_buttons_stage_and_unstage_selected_lines(app, repo, service, warnings):
    (repo / "file.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
    view, vm = working_view(repo, service)
    select_file(view)
    select_changed_line(view, "four")
    assert view._diff_toolbar._stage_btn.text() == "Stage Selected Lines"
    view._diff_toolbar._stage_btn.click()
    assert blob(repo, ":file.txt") == b"one\ntwo\nthree\nfour\n"
    assert (repo / "file.txt").read_bytes().endswith(b"four\nfive\n")
    select_file(view, staged=True)
    assert vm.state.selected_staged
    assert "Staged" in view._diff_view._header.text()
    assert not view._diff_toolbar._discard_btn.isEnabled()
    select_changed_line(view, "four")
    assert view._diff_toolbar._stage_btn.text() == "Unstage Selected Lines"
    view._diff_toolbar._stage_btn.click()
    assert blob(repo, ":file.txt") == blob(repo, "HEAD:file.txt")
    assert warnings == []
    view.close()


def test_hunk_picker_stages_only_second_hunk(app, repo, service, warnings):
    original = [f"line {i}" for i in range(30)]
    (repo / "file.txt").write_text("\n".join(original) + "\n")
    git(repo, "commit", "-qam", "More lines")
    original[1], original[27] = "first change", "second change"
    (repo / "file.txt").write_text("\n".join(original) + "\n")
    view, vm = working_view(repo, service)
    select_file(view)
    assert len(vm.state.selected_diff) == 2
    view._diff_toolbar._hunk_picker.setCurrentIndex(1)
    view._diff_toolbar._stage_btn.click()
    staged = blob(repo, ":file.txt")
    assert b"second change" in staged and b"first change" not in staged
    assert warnings == []
    view.close()


def test_staged_and_unstaged_selection_and_new_file_preview(app, repo, service):
    (repo / "file.txt").write_text("staged text\n")
    git(repo, "add", "file.txt")
    (repo / "file.txt").write_text("unstaged text\n")
    (repo / "new.txt").write_text("new file text\n")
    view, vm = working_view(repo, service)
    select_file(view, staged=True)
    assert "staged text" in view._diff_view._text.toPlainText()
    assert "unstaged text" not in view._diff_view._text.toPlainText()
    select_file(view)
    assert "unstaged text" in view._diff_view._text.toPlainText()
    select_file(view, path="new.txt")
    assert "new file text" in view._diff_view._text.toPlainText()
    assert vm.state.selected_preview.new_file
    view.close()


@pytest.mark.parametrize("accept", [False, True])
def test_discard_button_confirms_and_preserves_index(
    app, repo, service, warnings, monkeypatch, accept
):
    (repo / "file.txt").write_text("staged\n")
    git(repo, "add", "file.txt")
    content = b"staged\nextra\n"
    (repo / "file.txt").write_bytes(content)
    view, vm = working_view(repo, service)
    select_file(view)
    confirmations = []
    monkeypatch.setattr(
        view, "_confirm_discard", lambda *args: confirmations.append(args) or accept
    )
    view._diff_toolbar._discard_btn.click()
    assert len(confirmations) == 1
    assert blob(repo, ":file.txt") == b"staged\n"
    assert (repo / "file.txt").read_bytes() == (b"staged\n" if accept else content)
    backups = list((repo / ".git" / "gitqt-discard-backups").glob("*/content"))
    assert len(backups) == int(accept)
    if accept:
        assert backups[0].read_bytes() == content
        assert str(backups[0].parent) in vm.state.message
        info = json.loads(backups[0].with_name("info.json").read_text())
        assert info["path"] == "file.txt"
    assert warnings == []
    view.close()


@pytest.mark.parametrize(
    "filename", ["space name.txt", "日本語.txt", 'quote"name.txt', "tab\tname.txt"]
)
@pytest.mark.parametrize("ending", [b"\n", b"", b"\r\n"])
def test_partial_round_trip_preserves_paths_and_newlines(repo, service, filename, ending):
    if os.name == "nt" and ('"' in filename or "\t" in filename):
        pytest.skip("Filename is not permitted on Windows")
    path = repo / filename
    before = b"first" + ending
    after = b"changed" + ending
    path.write_bytes(before)
    git(repo, "add", "--", filename)
    git(repo, "commit", "-qm", "File")
    path.write_bytes(after)
    assert filename in [e.path for e in service.get_working_tree(repo).entries]
    preview = service.get_preview(repo, filename)
    service.apply_hunk(repo, filename, preview, 0, action="stage")
    assert blob(repo, f":{filename}") == after
    preview = service.get_preview(repo, filename, staged=True)
    service.apply_hunk(repo, filename, preview, 0, action="unstage")
    assert blob(repo, f":{filename}") == before
    preview = service.get_preview(repo, filename)
    service.apply_hunk(repo, filename, preview, 0, action="discard")
    assert path.read_bytes() == before


def test_new_file_partial_stage_then_whole_unstage(repo, service):
    path = repo / "new.txt"
    path.write_text("first\nsecond\n")
    preview = service.get_preview(repo, "new.txt")
    service.apply_hunk(repo, "new.txt", preview, 0, action="stage", selected_lines={0})
    assert blob(repo, ":new.txt") == b"first\n"
    preview = service.get_preview(repo, "new.txt", staged=True)
    service.apply_hunk(repo, "new.txt", preview, 0, action="unstage")
    assert git(repo, "ls-files", "new.txt").stdout == b""
    assert path.read_bytes() == b"first\nsecond\n"


def test_deleted_file_partial_and_full_actions(repo, service):
    (repo / "file.txt").unlink()
    preview = service.get_preview(repo, "file.txt")
    service.apply_hunk(repo, "file.txt", preview, 0, action="stage", selected_lines={0})
    assert blob(repo, ":file.txt") == b"two\nthree\n"
    preview = service.get_preview(repo, "file.txt")
    service.apply_hunk(repo, "file.txt", preview, 0, action="discard")
    assert (repo / "file.txt").read_bytes() == b"two\nthree\n"
    (repo / "file.txt").unlink()
    preview = service.get_preview(repo, "file.txt")
    service.apply_hunk(repo, "file.txt", preview, 0, action="stage")
    assert git(repo, "ls-files", "file.txt").stdout == b""


def test_stale_discard_is_rejected_without_mutation(repo, service):
    path = repo / "file.txt"
    path.write_text("first edit\n")
    preview = service.get_preview(repo, "file.txt")
    path.write_text("later edit\n")
    with pytest.raises(ValueError, match="File changed"):
        service.apply_hunk(repo, "file.txt", preview, 0, action="discard")
    assert path.read_text() == "later edit\n"
    assert blob(repo, ":file.txt") == blob(repo, "HEAD:file.txt")


@pytest.mark.parametrize("content, phrase", [(b"", "empty"), (b"\x00\x01", "Binary")])
def test_non_text_hunk_states_are_explained_and_disabled(app, repo, service, content, phrase):
    (repo / "new.txt").write_bytes(content)
    view, vm = working_view(repo, service)
    select_file(view, path="new.txt")
    assert phrase in view._diff_view._empty_label.text()
    assert not view._diff_toolbar._stage_btn.isEnabled()
    assert not view._diff_toolbar._discard_btn.isEnabled()
    assert view._stage_btn.isEnabled()
    view.close()


def test_stage_all_and_multi_file_unstage_buttons(app, repo, service, warnings):
    (repo / "file.txt").write_text("changed\n")
    (repo / "new.txt").write_text("new\n")
    view, vm = working_view(repo, service)
    view._stage_all_btn.click()
    assert len(service.get_working_tree(repo).entries) == 2
    assert all(e.staged and not e.unstaged for e in service.get_working_tree(repo).entries)
    view._staged_list.selectAll()
    view._unstage_btn.click()
    assert all(not e.staged for e in service.get_working_tree(repo).entries)
    assert warnings == []
    view.close()


def conflict(repo, operation):
    git(repo, "checkout", "-qb", "feature")
    (repo / "file.txt").write_text("feature\n")
    git(repo, "commit", "-qam", "Feature")
    feature = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    git(repo, "checkout", "-q", "main")
    (repo / "file.txt").write_text("main\n")
    git(repo, "commit", "-qam", "Main")
    before = git(repo, "rev-parse", "HEAD").stdout
    if operation == "merge":
        result = git(repo, "merge", "feature", check=False)
    elif operation == "rebase":
        git(repo, "checkout", "-q", "feature")
        before = git(repo, "rev-parse", "HEAD").stdout
        result = git(repo, "rebase", "main", check=False)
    elif operation == "cherry-pick":
        result = git(repo, "cherry-pick", feature, check=False)
    else:
        result = git(repo, "revert", "--no-edit", feature, check=False)
    assert result.returncode != 0
    return before


@pytest.mark.parametrize("operation", ["merge", "rebase", "cherry-pick", "revert"])
def test_operations_remain_detected_after_staging_and_continue(repo, runner, service, operation):
    conflict(repo, operation)
    detector = ConflictService(runner)
    vm = WorkingTreeViewModel(service, conflict_service=detector)
    vm.load_repository(str(repo))
    assert vm.state.conflict_operation == operation
    assert vm.state.has_conflicts and vm.state.stage_actions_enabled
    commands = CommitOperationsService(runner)
    assert not commands.resolve_operation(repo, operation, "continue").success
    (repo / "file.txt").write_text("resolved\n")
    vm.select_file("file.txt", staged=False)
    assert vm.stage_selected_file().success
    assert not vm.state.has_conflicts
    assert vm.state.conflict_operation == operation
    assert vm.state.conflict_banner
    assert not vm.state.commit_enabled
    # A fresh service simulates reopening the app; state must not depend on memory.
    assert ConflictService(runner).detect_conflicts(repo).operation == operation
    result = commands.resolve_operation(repo, operation, "continue")
    assert result.success, result.message
    assert detector.detect_conflicts(repo).operation is None
    assert blob(repo, "HEAD:file.txt") == b"resolved\n"


@pytest.mark.parametrize("operation", ["merge", "rebase", "cherry-pick", "revert"])
def test_operation_abort_restores_starting_commit(repo, runner, operation):
    before = conflict(repo, operation)
    result = CommitOperationsService(runner).resolve_operation(repo, operation, "abort")
    assert result.success, result.message
    assert git(repo, "rev-parse", "HEAD").stdout == before
    assert ConflictService(runner).detect_conflicts(repo).operation is None
    assert git(repo, "status", "--porcelain").stdout == b""


@pytest.mark.parametrize("operation", ["rebase", "cherry-pick", "revert"])
def test_operation_skip_leaves_clean_repository(repo, runner, operation):
    conflict(repo, operation)
    result = CommitOperationsService(runner).resolve_operation(repo, operation, "skip")
    assert result.success, result.message
    assert ConflictService(runner).detect_conflicts(repo).operation is None
    assert git(repo, "status", "--porcelain").stdout == b""


def repository_view(repo, tmp_path):
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    view = RepositoryView(services)
    view.load_repository(str(repo))
    return view


def test_conflict_continue_button_finishes_merge(app, repo, runner, tmp_path, warnings):
    conflict(repo, "merge")
    view = repository_view(repo, tmp_path)
    panel = view._working_tree
    assert not panel._continue_btn.isEnabled()
    (repo / "file.txt").write_text("resolved\n")
    select_file(panel)
    panel._stage_btn.click()
    assert panel._continue_btn.isEnabled()
    assert not panel._conflict_banner.isHidden()
    panel._continue_btn.click()
    assert ConflictService(runner).detect_conflicts(repo).operation is None
    assert panel._conflict_banner.isHidden()
    assert panel.isEnabled() and view._back_btn.isEnabled()
    assert warnings == []
    view.close()


@pytest.mark.parametrize(
    "error_type", [GitCommandTimeoutError, GitCommandLaunchError, RuntimeError]
)
def test_remote_worker_exception_restores_buttons(app, repo, tmp_path, warnings, error_type):
    view = repository_view(repo, tmp_path)
    refreshes = []
    view._refresh_all = lambda: refreshes.append(True)

    def fail(vm):
        assert not view._fetch_btn.isEnabled()
        raise error_type("remote failure")

    view._run_remote_op_async(op_name="Fetch", failure_title="Fetch Failed", runner=fail)
    assert all(button.isEnabled() for button in (view._fetch_btn, view._pull_btn, view._push_btn))
    assert not view._remote_operation_busy
    assert refreshes and any("remote failure" in message for message in warnings)
    # A retry must remain possible.
    view._run_remote_op_async(
        op_name="Fetch",
        failure_title="Fetch Failed",
        runner=lambda vm: SimpleNamespace(success=True),
    )
    assert not view._remote_operation_busy
    view.close()


def test_remote_submission_failure_restores_buttons(app, repo, tmp_path, warnings):
    view = repository_view(repo, tmp_path)
    view._refresh_all = lambda: None

    class BrokenPool:
        def submit(self, **kwargs):
            raise RuntimeError("could not submit")

    view._services.worker_pool = BrokenPool()
    view._do_fetch()
    assert view._fetch_btn.isEnabled() and view._push_btn.isEnabled()
    assert any("could not submit" in message for message in warnings)
    view.close()


def test_symlink_preview_cannot_partially_change_target(app, repo, service):
    link = repo / "link.txt"
    try:
        link.symlink_to("file.txt")
    except OSError:
        pytest.skip("Symlinks are unavailable")
    git(repo, "add", "link.txt")
    git(repo, "commit", "-qm", "Link")
    link.unlink()
    link.symlink_to("missing.txt")
    view, vm = working_view(repo, service)
    select_file(view, path="link.txt")
    assert not vm.state.selected_preview.partial_supported
    assert not view._diff_toolbar._stage_btn.isEnabled()
    assert not view._diff_toolbar._discard_btn.isEnabled()
    view.close()


def test_file_with_pathspec_characters_is_staged_literally(repo, service):
    (repo / "[ab].txt").write_text("literal\n")
    (repo / "a.txt").write_text("unrelated\n")
    service.stage_file(repo, "[ab].txt")
    assert git(repo, "diff", "--cached", "--name-only").stdout == b"[ab].txt\n"
    service.unstage_file(repo, "[ab].txt")
    assert git(repo, "diff", "--cached", "--name-only").stdout == b""


def test_linked_worktree_operation_state_survives_staging(repo, runner, tmp_path):
    git(repo, "checkout", "-qb", "feature")
    (repo / "file.txt").write_text("feature\n")
    git(repo, "commit", "-qam", "Feature")
    git(repo, "checkout", "-q", "main")
    (repo / "file.txt").write_text("main\n")
    git(repo, "commit", "-qam", "Main")
    worktree = tmp_path / "linked"
    git(repo, "worktree", "add", str(worktree), "feature")
    assert git(worktree, "merge", "main", check=False).returncode != 0
    (worktree / "file.txt").write_text("resolved\n")
    git(worktree, "add", "file.txt")
    state = ConflictService(runner).detect_conflicts(worktree)
    assert state.operation == "merge" and not state.has_conflicts
    assert ConflictService(runner).detect_conflicts(repo).operation is None
    assert CommitOperationsService(runner).resolve_operation(worktree, "merge", "abort").success


def test_remote_timeout_restores_buttons_through_real_worker(app, repo, tmp_path, warnings):
    from gitqt.services.worker_pool import WorkerPool

    view = repository_view(repo, tmp_path)
    pool = WorkerPool()
    view._services.worker_pool = pool
    view._refresh_all = lambda: None

    def fail(vm):
        raise GitCommandTimeoutError("timed out on worker")

    view._run_remote_op_async(op_name="Fetch", failure_title="Fetch Failed", runner=fail)
    assert pool.wait_for_done(2000)
    app.processEvents()
    assert view._fetch_btn.isEnabled() and view._push_btn.isEnabled()
    assert not view._remote_operation_busy
    assert any("timed out on worker" in message for message in warnings)
    view.close()


def test_partial_new_file_can_be_unstaged_before_first_commit(tmp_path, service):
    path = tmp_path / "empty-repo"
    path.mkdir()
    git(path, "init")
    (path / "new.txt").write_text("first\nsecond\n")
    preview = service.get_preview(path, "new.txt")
    service.apply_hunk(path, "new.txt", preview, 0, action="stage", selected_lines={0})
    preview = service.get_preview(path, "new.txt", staged=True)
    service.apply_hunk(path, "new.txt", preview, 0, action="unstage")
    assert git(path, "ls-files").stdout == b""
    assert (path / "new.txt").read_bytes() == b"first\nsecond\n"


def test_refresh_button_updates_selected_diff_after_external_resolution(
    app, repo, tmp_path, warnings
):
    (repo / "file.txt").write_text("first edit\n")
    view = repository_view(repo, tmp_path)
    panel = view._working_tree
    select_file(panel)
    assert "first edit" in panel._diff_view._text.toPlainText()
    (repo / "file.txt").write_text("later edit\n")
    panel._refresh_btn.click()
    assert "later edit" in panel._diff_view._text.toPlainText()
    assert "first edit" not in panel._diff_view._text.toPlainText()
    assert warnings == []
    view.close()
