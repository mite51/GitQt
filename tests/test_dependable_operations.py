"""Real-repository recovery, transfers, refresh, and asynchronous UI regressions."""

import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QThread, QTimer
from test_visible_workflows import git

from gitqt.app.bootstrap import build_services
from gitqt.services.git import (
    GitCommandCancelledError,
    GitCommandFailure,
    GitCommandTimeoutError,
    GitExecutable,
    GitRunner,
)
from gitqt.services.observer import repository_fingerprint
from gitqt.services.process_context import ProcessContext, process_context
from gitqt.services.recovery import RecoveryService
from gitqt.services.remote import RemoteService
from gitqt.services.worker_pool import ImmediateWorkerPool, WorkerPool
from gitqt.ui.dialogs import CloneDialog
from gitqt.ui.repository_view import RepositoryView

pytest_plugins = ["test_visible_workflows"]


def test_dropped_stash_survives_restart_gc_and_other_changes(repo, runner):
    (repo / "file.txt").write_text("saved work\n")
    git(repo, "stash", "push", "-qm", "Saved")
    recovery = RecoveryService(runner)
    ref = recovery.retain(repo, "stash@{0}", "drop-stash", kind="stash")
    git(repo, "stash", "drop")
    (repo / "other.txt").write_text("external work")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "External")
    git(repo, "reflog", "expire", "--expire=now", "--all")
    git(repo, "gc", "--prune=now")
    restarted = RecoveryService(runner)
    entry = next(item for item in restarted.entries(repo) if item.reference == ref)
    restarted.recover(repo, entry)
    assert (repo / "file.txt").read_text() == "saved work\n"
    assert (repo / "other.txt").read_text() == "external work"
    assert git(repo, "rev-parse", ref).returncode == 0


def test_reset_retains_distinct_staged_and_unstaged_work(repo, runner):
    (repo / "file.txt").write_text("staged\n")
    git(repo, "add", ".")
    (repo / "file.txt").write_text("unstaged\n")
    recovery = RecoveryService(runner)
    recovery.before_reset(repo, hard=True)
    git(repo, "reset", "--hard", "HEAD")
    entry = next(item for item in recovery.entries(repo) if item.kind == "stash")
    recovery.recover(repo, entry)
    assert (repo / "file.txt").read_text() == "unstaged\n"
    assert git(repo, "show", ":file.txt").stdout == b"staged\n"


def test_hard_reset_refuses_untracked_and_ignored_data(repo, runner):
    (repo / "scratch").write_text("untracked")
    with pytest.raises(ValueError, match="untracked"):
        RecoveryService(runner).before_reset(repo, hard=True)
    (repo / "scratch").unlink()
    (repo / ".git" / "info" / "exclude").write_text("ignored\n")
    (repo / "ignored").write_text("ignored")
    with pytest.raises(ValueError, match="ignored"):
        RecoveryService(runner).before_reset(repo, hard=True)


def test_file_recovery_retains_intervening_edits(repo, runner, service):
    (repo / "file.txt").write_text("original unsaved")
    backup = service._backup_worktree_file(repo, "file.txt")
    (repo / "file.txt").write_text("newer external edits")
    recovery = RecoveryService(runner)
    entry = next(item for item in recovery.entries(repo) if item.reference == backup)
    recovery.recover(repo, entry)
    assert (repo / "file.txt").read_text() == "original unsaved"
    backups = [
        Path(item.reference, "content").read_text()
        for item in recovery.entries(repo)
        if item.kind == "file"
    ]
    assert "newer external edits" in backups
    assert git(repo, "diff", "--cached").stdout == b""


def test_recovery_creates_branch_without_moving_current_head(repo, runner):
    recovery = RecoveryService(runner)
    ref = recovery.retain(repo, "HEAD", "deleted-branch")
    entry = next(item for item in recovery.entries(repo) if item.reference == ref)
    recovery.recover(repo, entry, branch_name="recovered")
    assert git(repo, "branch", "--show-current").stdout == b"main\n"
    assert git(repo, "rev-parse", "recovered").stdout == git(repo, "rev-parse", "HEAD").stdout
    with pytest.raises(GitCommandFailure):
        recovery.recover(repo, entry, branch_name="main")


def test_publish_remote_crud_and_recovery(repo, runner, tmp_path):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    service = RemoteService(runner)
    service.configure(repo, "fork", str(bare))
    result = service.push(repo, remote="fork", branch="HEAD:feature", set_upstream=True)
    assert result.success, result.message
    assert git(repo, "rev-parse", "--abbrev-ref", "@{upstream}").stdout == b"fork/feature\n"
    service.configure(repo, "origin", str(bare), previous="fork")
    service.remove(repo, "origin")
    recovery = RecoveryService(runner)
    entry = next(item for item in recovery.entries(repo) if item.kind == "remote")
    recovery.recover(repo, entry)
    assert service.list_remotes(repo, force_refresh=True).remotes[0].fetch_url == str(bare)
    assert any(
        item.kind == "commit" and "removed-remote" in item.description
        for item in recovery.entries(repo)
    )


def test_force_lease_rejects_changes_after_preview_even_after_external_fetch(
    repo, runner, tmp_path
):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    service = RemoteService(runner)
    service.configure(repo, "origin", str(bare))
    assert service.push(repo, branch="HEAD:main", set_upstream=True).success
    preview = service.force_preview(repo, "origin", "main")
    expected = preview.expected_oid
    assert "remote-only" in preview.message
    other = tmp_path / "other"
    git(tmp_path, "clone", "-b", "main", str(bare), str(other))
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.invalid")
    git(other, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "Other change")
    git(other, "push")
    git(repo, "fetch")
    result = service.push(repo, branch="HEAD:main", force_with_lease=True, expected_oid=expected)
    assert not result.success
    assert git(bare, "rev-parse", "main").stdout == git(other, "rev-parse", "HEAD").stdout


def test_fast_forward_pull_does_not_merge_divergent_commits(repo, runner, tmp_path):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    service = RemoteService(runner)
    service.configure(repo, "origin", str(bare))
    assert service.push(repo, branch="HEAD:main", set_upstream=True).success
    other = tmp_path / "other"
    git(tmp_path, "clone", "-b", "main", str(bare), str(other))
    git(
        other,
        "-c",
        "user.name=Other",
        "-c",
        "user.email=other@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-qm",
        "Remote change",
    )
    git(other, "push")
    git(repo, "commit", "--allow-empty", "-qm", "Local change")
    before = git(repo, "rev-parse", "HEAD").stdout
    assert not service.pull(repo, branch="main", ff_only=True).success
    assert git(repo, "rev-parse", "HEAD").stdout == before
    assert service.pull(repo, branch="main", rebase=True).success


def test_observer_detects_repeated_edits_index_and_linked_worktree(repo, runner, tmp_path):
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-b", "linked", str(linked))
    before = repository_fingerprint(runner, str(linked))
    (linked / "file.txt").write_text("first edit")
    first = repository_fingerprint(runner, str(linked))
    (linked / "file.txt").write_text("second edit")
    second = repository_fingerprint(runner, str(linked))
    git(linked, "add", ".")
    third = repository_fingerprint(runner, str(linked))
    assert len({before, first, second, third}) == 4


def test_process_streams_before_completion_and_cancels_children(tmp_path):
    context = ProcessContext()
    seen = threading.Event()
    messages = []
    context.progress = lambda message: (messages.append(message), seen.set())
    runner = GitRunner(GitExecutable(sys.executable))
    errors = []
    marker = tmp_path / "child-finished"
    child = (
        "import time; from pathlib import Path; time.sleep(1.2);"
        f" Path({str(marker)!r}).write_text('bad')"
    )
    code = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]);"
        " print('progress https://user:secret@example.invalid/repo', file=sys.stderr, flush=True);"
        " time.sleep(30)"
    )

    def run():
        with process_context(context):
            try:
                runner.run(["-c", code, "fetch"])
            except GitCommandCancelledError as error:
                errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert seen.wait(3)
    assert thread.is_alive()
    context.cancel.set()
    thread.join(3)
    assert not thread.is_alive() and errors
    assert all("secret" not in message for message in messages)
    time.sleep(1.3)
    assert not marker.exists()


def test_timeout_restores_process_control():
    runner = GitRunner(GitExecutable(sys.executable))
    with pytest.raises(GitCommandTimeoutError):
        runner.run(["-c", "import time; time.sleep(30)"], timeout_seconds=0.05)
    assert runner.run(["-c", "print('retry works')"]).stdout.strip() == "retry works"


@pytest.mark.parametrize("action", ["clone", "checkout", "commit", "diff"])
def test_slow_git_work_keeps_qt_alive_and_completes_on_gui_thread(
    app, repo, tmp_path, action, monkeypatch
):
    services = build_services(base_config_dir=tmp_path / "settings")
    entered, release = threading.Event(), threading.Event()
    thread_ids = []

    def slow(*_args, **_kwargs):
        thread_ids.append(QThread.currentThread())
        entered.set()
        assert release.wait(5)
        return None

    if action == "clone":
        view = CloneDialog(services)
        view._url_input.setText(str(repo))
        view._dest_input.setText(str(tmp_path))
        view._name_input.setText("slow-clone")
        monkeypatch.setattr(services.git_runner, "run", slow)
        view._do_clone()
        jobs = view._jobs
    else:
        # Initial read is deterministic; the user operation uses the real Qt pool.
        services.worker_pool = ImmediateWorkerPool()
        view = RepositoryView(services)
        view.load_repository(str(repo))
        services.worker_pool = WorkerPool()
        view._jobs.pool = services.worker_pool
        jobs = view._jobs
        if action == "checkout":
            monkeypatch.setattr(services.branch, "checkout_branch", slow)
            view._checkout_branch("main")
        elif action == "commit":
            # Exercise the same view-model mutation layer with a slow commit hook.
            from gitqt.app.working_tree import WorkingTreeActionResult

            def commit(model):
                slow()
                return WorkingTreeActionResult(True, "Committed")

            view._working_tree._run_model_action("Commit", commit, commit=True)
        else:
            (repo / "file.txt").write_text("edit")
            services.worker_pool = ImmediateWorkerPool()
            view._refresh_working_tree()
            view._jobs.pool = WorkerPool()
            from gitqt.services.status import DiffPreview

            def diff(*args, **kwargs):
                slow()
                return DiffPreview([])

            monkeypatch.setattr(services.status, "get_preview", diff)
            view._working_tree._unstaged_list.setCurrentRow(0)
    beats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    deadline = time.monotonic() + 3
    while (not entered.is_set() or len(beats) < 3) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert entered.is_set() and len(beats) >= 3
    assert thread_ids[0] != app.thread()
    release.set()
    deadline = time.monotonic() + 5
    while (jobs.busy or not services.worker_pool.wait_for_done(1)) and time.monotonic() < deadline:
        app.processEvents()
    app.processEvents()
    assert not jobs.busy
    timer.stop()
    if isinstance(view, RepositoryView):
        view._observe_timer.stop()
        view._jobs.invalidate_reads()
    view.close()


def test_background_snapshot_does_not_mutate_ui_and_cannot_cross_repositories(app, repo, tmp_path):
    from test_session_jobs import DeferredPool

    from gitqt.services.worker import WorkerExecutionResult

    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    view = RepositoryView(services)
    view.load_repository(str(repo))
    original = view._working_tree_vm
    assert original.state.entries == []
    (repo / "file.txt").write_text("modified externally")
    pool = DeferredPool()
    services.worker_pool = pool
    view._refresh_working_tree()
    key, run, callback = pool.jobs[0]
    result = run()
    assert original.state.entries == []
    other = tmp_path / "other"
    git(tmp_path, "clone", str(repo), str(other))
    view.load_repository(str(other))
    callback(WorkerExecutionResult(1, key, True, False, True, result))
    assert view._repository_path == str(other)
    assert view._working_tree_vm is not original
    assert view._working_tree_vm.state.entries == []
    view._observe_timer.stop()
    view.close()


def test_pull_gate_rejects_branch_stash_commit_and_navigation(app, repo, tmp_path):
    from test_session_jobs import DeferredPool

    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    view = RepositoryView(services)
    view.load_repository(str(repo))
    pool = DeferredPool()
    services.worker_pool = pool
    view._run_remote_op_async(op_name="Pull", failure_title="Pull Failed", runner=lambda vm: None)
    view._checkout_branch("main")
    view._apply_stash("stash@{0}")
    view._working_tree._run_model_action("Commit", lambda model: None, commit=True)
    assert not view.load_repository(str(tmp_path / "other"))
    assert len(pool.jobs) == 1
    assert not view._sidebar.isEnabled()
    assert not view._working_tree._summary_input.isEnabled()
    services.worker_pool = ImmediateWorkerPool()
    pool.finish(0)
    assert not view._jobs.busy and view._sidebar.isEnabled()
    view._observe_timer.stop()
    view.close()


def test_external_refresh_preserves_draft_and_file_selection(app, repo, tmp_path):
    from PySide6.QtCore import Qt

    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    (repo / "file.txt").write_text("modified externally")
    view = RepositoryView(services)
    view.load_repository(str(repo))
    view.show()
    app.processEvents()
    view._working_tree._unstaged_list.setCurrentRow(0)
    view._working_tree._summary_input.setText("Draft survives refresh")
    view._observe_repository()
    (repo / "file.txt").write_text("edited again")
    view._observe_repository()
    assert view._working_tree._summary_input.text() == "Draft survives refresh"
    assert view._working_tree_vm.state.commit_summary == "Draft survives refresh"
    assert (
        view._working_tree._unstaged_list.currentItem().data(Qt.ItemDataRole.UserRole) == "file.txt"
    )
    assert any(
        line.text == "edited again"
        for hunk in view._working_tree_vm.state.selected_diff
        for line in hunk.lines
    )
    view._observe_timer.stop()
    view.close()


def test_force_preview_uses_push_url(repo, runner, tmp_path):
    fetch_bare, push_bare = tmp_path / "fetch.git", tmp_path / "push.git"
    for bare in (fetch_bare, push_bare):
        git(tmp_path, "init", "--bare", str(bare))
        git(repo, "push", str(bare), "HEAD:main")
    git(repo, "commit", "--allow-empty", "-qm", "Only on push destination")
    git(repo, "push", str(push_bare), "HEAD:main")
    expected = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    git(repo, "reset", "--hard", "HEAD~1")
    remote = RemoteService(runner)
    remote.configure(repo, "origin", str(fetch_bare), str(push_bare))
    preview = remote.force_preview(repo, "origin", "main")
    assert preview.expected_oid == expected
    assert "1 remote-only commits" in preview.message


def test_real_slow_commit_hook_keeps_window_responsive(app, repo, tmp_path):
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    (repo / "file.txt").write_text("committed change")
    git(repo, "add", ".")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'Checking commit' >&2\nsleep 0.3\n")
    hook.chmod(0o700)
    view = RepositoryView(services)
    view.load_repository(str(repo))
    services.worker_pool = WorkerPool()
    view._jobs.pool = services.worker_pool
    view._working_tree._summary_input.setText("With slow hook")
    view._working_tree._do_commit()
    assert view._jobs.busy
    beats = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    deadline = time.monotonic() + 5
    while view._jobs.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    timer.stop()
    assert not view._jobs.busy and len(beats) >= 5
    assert git(repo, "log", "-1", "--format=%s").stdout == b"With slow hook\n"
    assert "Checking commit" in view._op_log._body.toPlainText()
    assert view._working_tree._summary_input.text() == ""
    services.worker_pool.wait_for_done(5000)
    app.processEvents()
    view._observe_timer.stop()
    view.close()


def test_pending_selection_survives_observer_refresh(app, repo, tmp_path):
    from test_session_jobs import DeferredPool

    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    (repo / "file.txt").write_text("change one")
    (repo / "two.txt").write_text("change two")
    view = RepositoryView(services)
    view.load_repository(str(repo))
    view._working_tree._unstaged_list.setCurrentRow(0)
    pool = DeferredPool()
    services.worker_pool = pool
    view._jobs.pool = pool
    view._working_tree._unstaged_list.setCurrentRow(1)
    assert view._working_tree_vm.state.selected_file == "two.txt"
    view._refresh_working_tree()
    pool.finish(1)
    pool.finish(0)
    assert view._working_tree_vm.state.selected_file == "two.txt"
    assert any(
        line.text == "change two"
        for hunk in view._working_tree_vm.state.selected_diff
        for line in hunk.lines
    )
    view._observe_timer.stop()
    view.close()


def test_recovery_references_do_not_pollute_default_history(repo, runner):
    from gitqt.services.history import HistoryService

    (repo / "file.txt").write_text("work backup")
    RecoveryService(runner).before_reset(repo)
    rows = HistoryService(runner).get_graph(repo)
    assert len(rows) == 1


def test_force_push_ui_pins_previewed_source_and_destination(
    app, repo, runner, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from gitqt.ui import remote_dialogs
    from gitqt.ui.dialogs import ConfirmationDialog

    bare, alternate = tmp_path / "remote.git", tmp_path / "alternate.git"
    for destination in (bare, alternate):
        git(tmp_path, "init", "--bare", str(destination))
        git(repo, "push", str(destination), "HEAD:main")
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    services.remote.configure(repo, "origin", str(bare))
    git(repo, "commit", "--allow-empty", "-qm", "Previewed commit")
    previewed_head = git(repo, "rev-parse", "HEAD").stdout
    view = RepositoryView(services)
    view.load_repository(str(repo))
    fake = SimpleNamespace(
        exec=lambda: True,
        remote=SimpleNamespace(currentText=lambda: "origin"),
        branch=SimpleNamespace(text=lambda: "main"),
        option=SimpleNamespace(isChecked=lambda: False),
        strategy=SimpleNamespace(currentIndex=lambda: 0),
        force=SimpleNamespace(isChecked=lambda: True),
    )
    monkeypatch.setattr(remote_dialogs, "TransferDialog", lambda *args: fake)

    def confirm(dialog):
        # Simulate edits made by an external tool while the preview is open.
        git(repo, "commit", "--allow-empty", "-qm", "Later external commit")
        git(repo, "remote", "set-url", "--push", "origin", str(alternate))
        return True

    monkeypatch.setattr(ConfirmationDialog, "exec", confirm)
    view._configure_transfer("push")
    assert git(bare, "rev-parse", "main").stdout == previewed_head
    assert git(alternate, "rev-parse", "main").stdout != previewed_head
    assert not view._jobs.busy
    view._observe_timer.stop()
    view.close()
