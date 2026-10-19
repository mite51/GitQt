"""Durable composer acceptance tests using real repositories and visible controls."""

import json
from threading import Event

import pytest
from PySide6.QtWidgets import QMessageBox
from test_session_jobs import DeferredPool
from test_visible_workflows import git

from gitqt.app.bootstrap import build_services
from gitqt.app.working_tree import WorkingTreeViewModel
from gitqt.services.commit import CommitService
from gitqt.services.drafts import DraftStore
from gitqt.services.settings import SettingsStore
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.repository_view import RepositoryView
from gitqt.ui.settings_dialog import SettingsDialog
from gitqt.ui.working_tree_view import WorkingTreeView

pytest_plugins = ["test_visible_workflows"]


def composer(repo, service, runner, path):
    vm = WorkingTreeViewModel(
        service, CommitService(runner), draft_store=DraftStore(path, debounce_seconds=60)
    )
    vm.load_repository(str(repo))
    view = WorkingTreeView()
    view._jobs.pool = ImmediateWorkerPool()
    view.set_view_model(vm)
    return view, vm


def test_gui_restart_preserves_independent_repository_and_worktree_drafts(
    app, repo, service, runner, tmp_path
):
    linked = tmp_path / "linked"
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-b", "linked", str(linked))
    git(tmp_path, "clone", str(repo), str(other))
    path = tmp_path / "drafts.json"
    expected = {
        repo: ("Main draft", "Main body\nwith trailing space  "),
        linked: ("Linked draft", "Linked body"),
        other: ("Other draft", "Other body"),
    }
    for root, (summary, body) in expected.items():
        view, _ = composer(root, service, runner, path)
        view._summary_input.setText(summary)
        view._body_input.setPlainText(body)
        assert view.close()
    for root, (summary, body) in expected.items():
        view, _ = composer(root, service, runner, path)
        assert view._summary_input.text() == summary
        assert view._body_input.toPlainText() == body
        assert view.close()
    assert len(json.loads(path.read_text())) == 3


def test_repository_view_navigation_and_restart_restore_drafts(app, repo, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", str(repo), str(other))
    config = tmp_path / "settings"
    services = build_services(base_config_dir=config)
    services.worker_pool = ImmediateWorkerPool()
    view = RepositoryView(services)
    view.load_repository(str(repo))
    view._working_tree._summary_input.setText("First repository draft")
    view._working_tree._body_input.setPlainText("First body")
    view.load_repository(str(other))
    assert view._working_tree._summary_input.text() == ""
    view._working_tree._summary_input.setText("Second repository draft")
    view.load_repository(str(repo))
    assert view._working_tree._summary_input.text() == "First repository draft"
    assert view._working_tree._body_input.toPlainText() == "First body"
    view._observe_timer.stop()
    assert view.close()
    restarted_services = build_services(base_config_dir=config)
    restarted_services.worker_pool = ImmediateWorkerPool()
    restarted = RepositoryView(restarted_services)
    restarted.load_repository(str(other))
    assert restarted._working_tree._summary_input.text() == "Second repository draft"
    restarted._observe_timer.stop()
    assert restarted.close()


def test_edits_before_initial_async_restore_are_preserved(app, repo, tmp_path):
    services = build_services(base_config_dir=tmp_path / "settings")
    services.drafts.stage(DraftStore.identity(str(repo)), "Previously saved", "Old body")
    services.drafts.flush()
    pool = DeferredPool()
    services.worker_pool = pool
    view = RepositoryView(services)
    view.load_repository(str(repo))
    view._working_tree._summary_input.setText("Typed while opening")
    view._working_tree._body_input.setPlainText("New body")
    job = next(index for index, item in enumerate(pool.jobs) if "working_tree" in item[0])
    pool.finish(job)
    assert view._working_tree._summary_input.text() == "Typed while opening"
    assert view._working_tree._body_input.toPlainText() == "New body"
    assert view._working_tree.flush_draft()
    assert DraftStore(services.drafts.path).load(DraftStore.identity(str(repo))) == (
        "Typed while opening",
        "New body",
    )
    view._observe_timer.stop()
    view.close()


def test_amend_toggle_failure_success_and_restart_keep_normal_draft(
    app, repo, service, runner, tmp_path, warnings, monkeypatch
):
    path = tmp_path / "drafts.json"
    view, vm = composer(repo, service, runner, path)
    view._summary_input.setText("Future commit")
    view._body_input.setPlainText("Future body")
    view._amend_btn.setChecked(True)
    assert view._summary_input.text() == "Initial"
    view._summary_input.setText("Edited amend")
    view._amend_btn.setChecked(False)
    assert view._summary_input.text() == "Future commit"
    view._amend_btn.setChecked(True)
    git(repo, "commit", "--allow-empty", "-qm", "Moved HEAD")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    view._do_commit()
    assert warnings
    assert view._amend_btn.isChecked()
    assert DraftStore(path).load(DraftStore.identity(str(repo))) == ("Future commit", "Future body")
    view._amend_btn.setChecked(False)
    view._amend_btn.setChecked(True)
    view._summary_input.setText("Successful amend")
    view._do_commit()
    assert git(repo, "log", "-1", "--format=%s").stdout == b"Successful amend\n"
    assert not view._amend_btn.isChecked()
    assert view._summary_input.text() == "Future commit"
    assert view._body_input.toPlainText() == "Future body"
    view._amend_btn.setChecked(True)
    view._summary_input.setText("Unfinished amend")
    assert view.close()
    restarted, _ = composer(repo, service, runner, path)
    assert restarted._summary_input.text() == "Future commit"
    assert restarted._body_input.toPlainText() == "Future body"
    restarted.close()


def test_failed_normal_commit_preserves_draft_success_clears_only_this_worktree(
    app, repo, service, runner, tmp_path, warnings
):
    path = tmp_path / "drafts.json"
    view, vm = composer(repo, service, runner, path)
    other_id = DraftStore.identity(str(tmp_path / "other"))
    vm._draft_store.stage(other_id, "Other", "Other body")
    view._summary_input.setText("Commit draft")
    view._body_input.setPlainText("Commit body")
    view._do_commit()
    assert warnings  # No staged content: failed commit must keep the composer.
    assert view._summary_input.text() == "Commit draft"
    assert view.flush_draft()
    assert DraftStore(path).load(DraftStore.identity(str(repo))) == ("Commit draft", "Commit body")
    (repo / "file.txt").write_text("changed\n")
    git(repo, "add", ".")
    view._do_commit()
    assert view._summary_input.text() == ""
    assert DraftStore(path).load(DraftStore.identity(str(repo))) == ("", "")
    assert DraftStore(path).load(other_id) == ("Other", "Other body")
    view.close()


def test_draft_debounce_writes_once_off_ui_thread_and_unchanged_flush_is_free(
    tmp_path, monkeypatch
):
    from threading import get_ident

    store = DraftStore(tmp_path / "drafts.json", debounce_seconds=0.05)
    writes, finished = [], Event()
    original = SettingsStore._atomic_write

    def record(path, content):
        writes.append((path, get_ident()))
        original(path, content)
        finished.set()

    monkeypatch.setattr(SettingsStore, "_atomic_write", record)
    for index in range(25):
        store.stage("scope", f"Typed {index}", "Body")
    assert finished.wait(5)
    store.flush()
    assert writes == [(store.path, writes[0][1])]
    assert writes[0][1] != get_ident()
    store.stage("scope", "Typed 24", "Body")
    store.flush()
    assert len(writes) == 1


def test_stale_draft_clear_and_unchanged_flush_preserve_another_instance(tmp_path):
    path = tmp_path / "drafts.json"
    first = DraftStore(path, debounce_seconds=60)
    first.stage("scope", "Old", "Body")
    first.flush()
    second = DraftStore(path, debounce_seconds=60)
    assert second.load("scope") == ("Old", "Body")
    second.stage("scope", "Newer", "New body")
    second.flush()
    first.stage("scope", "Old", "Body")
    first.flush()
    assert DraftStore(path).load("scope") == ("Newer", "New body")
    first.stage("scope", "", "")
    first.flush()
    assert DraftStore(path).load("scope") == ("Newer", "New body")
    # The refused deletion did not observe the newer draft: subsequent local
    # editing must preserve that concurrent draft permanently before replacing it.
    first.stage("scope", "Next local draft", "")
    first.flush()
    preserved = next(tmp_path.glob("drafts.json.conflict-*"))
    first.stage("scope", "Still editing", "")
    first.flush()
    assert "Newer" in preserved.read_text()


def test_concurrent_draft_edit_preserves_permanent_conflict_copy(tmp_path):
    path = tmp_path / "drafts.json"
    first = DraftStore(path, debounce_seconds=60)
    second = DraftStore(path, debounce_seconds=60)
    first.load("scope")
    second.load("scope")
    first.stage("scope", "First writer", "First body")
    first.flush()
    second.stage("scope", "Second writer", "Second body")
    second.flush()
    preserved = next(tmp_path.glob("drafts.json.conflict-*"))
    assert "First writer" in preserved.read_text()
    assert str(preserved) in second.warning
    second.stage("scope", "More editing", "")
    second.flush()
    assert "First writer" in preserved.read_text()


def test_failed_draft_flush_retains_pending_text_for_retry(tmp_path, monkeypatch):
    store = DraftStore(tmp_path / "drafts.json", debounce_seconds=60)
    store.stage("scope", "Keep me", "Body")
    with monkeypatch.context() as patch:
        patch.setattr(
            SettingsStore,
            "_atomic_write",
            lambda *args: (_ for _ in ()).throw(OSError("Disk full")),
        )
        with pytest.raises(OSError, match="Disk full"):
            store.flush()
    assert store.load("scope") == ("Keep me", "Body")
    store.flush()
    assert DraftStore(store.path).load("scope") == ("Keep me", "Body")


def test_successful_commit_with_draft_write_failure_still_reports_success(
    app, repo, service, runner, tmp_path, monkeypatch, warnings
):
    view, vm = composer(repo, service, runner, tmp_path / "drafts.json")
    view._summary_input.setText("Successful commit")
    assert view.flush_draft()
    (repo / "file.txt").write_text("changed\n")
    git(repo, "add", ".")
    messages = []
    view.action_message.connect(messages.append)
    with monkeypatch.context() as patch:
        patch.setattr(vm._draft_store, "flush", lambda: (_ for _ in ()).throw(OSError("Disk full")))
        view._do_commit()
    assert warnings == []
    assert "draft could not be saved" in messages[-1]
    assert view._summary_input.text() == ""
    assert git(repo, "log", "-1", "--format=%s").stdout == b"Successful commit\n"
    assert view.close()


def test_stale_settings_dialog_only_changes_edited_fields(app, tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.update(history_row_alpha=0.123)
    dialog = SettingsDialog(store, store.load())
    dialog._jobs.pool = ImmediateWorkerPool()
    SettingsStore(path).update(external_diff_tool="Other instance tool", history_row_alpha=0.875)
    dialog._merge_input.setText("My merge tool")
    dialog._on_save()
    result = store.load()
    assert result.external_diff_tool == "Other instance tool"
    assert result.external_merge_tool == "My merge tool"
    assert result.history_row_alpha == 0.875


@pytest.mark.parametrize("leave", ["switch", "close"])
def test_initial_typing_is_saved_even_when_leaving_before_first_load(app, repo, tmp_path, leave):
    services = build_services(base_config_dir=tmp_path / "settings")
    pool = DeferredPool()
    services.worker_pool = pool
    view = RepositoryView(services)
    view.load_repository(str(repo))
    view._working_tree._summary_input.setText("Typed before load completed")
    view._working_tree._body_input.setPlainText("Still save this body")
    if leave == "switch":
        other = tmp_path / "other"
        git(tmp_path, "clone", str(repo), str(other))
        view.load_repository(str(other))
    else:
        assert view.close()
    assert DraftStore(services.drafts.path).load(DraftStore.identity(str(repo))) == (
        "Typed before load completed",
        "Still save this body",
    )
    view._observe_timer.stop()
    view.close()


def test_background_failure_and_recovered_conflict_surface_in_view(
    app, repo, service, runner, tmp_path
):
    view, vm = composer(repo, service, runner, tmp_path / "drafts.json")
    messages = []
    view.action_message.connect(messages.append)
    vm._draft_store.last_error = "Disk full"
    vm._draft_store.warning = "Concurrent commit draft preserved at /local/draft.conflict-123."
    view._report_draft_status()
    view._report_draft_status()
    assert messages == [vm._draft_store.warning, "Disk full"]
    assert "not saved" in view._status_label.text()
    vm._draft_store.last_error = None
    view.close()


def test_cross_process_draft_flushes_preserve_other_worktrees(tmp_path):
    from test_settings import _run_competing_settings_processes

    _run_competing_settings_processes(
        tmp_path,
        """
from gitqt.services.drafts import DraftStore
store = DraftStore(root / "drafts.json", debounce_seconds=60)
for iteration in range(10):
    store.stage(f"worktree-{index}", f"summary-{iteration}", f"body-{index}")
    store.flush()
""",
    )
    store = DraftStore(tmp_path / "drafts.json")
    for index in range(4):
        assert store.load(f"worktree-{index}") == ("summary-9", f"body-{index}")


def test_corrupt_drafts_restore_backup_and_preserve_damaged_content(tmp_path):
    path = tmp_path / "drafts.json"
    store = DraftStore(path, debounce_seconds=60)
    store.stage("scope", "Previous", "Previous body")
    store.flush()
    store.stage("scope", "Latest", "Latest body")
    store.flush()
    path.write_bytes(b"damaged draft data")
    restarted = DraftStore(path)
    assert restarted.load("scope") == ("Previous", "Previous body")
    assert next(tmp_path.glob("drafts.json.corrupt-*")).read_bytes() == b"damaged draft data"
    assert restarted.warning
