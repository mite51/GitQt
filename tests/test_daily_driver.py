"""Daily-driver acceptance through real Git repositories and Qt controls."""

import os

import pytest
from PySide6.QtWidgets import QMessageBox
from test_visible_workflows import git

from gitqt.app.bootstrap import build_services
from gitqt.services.commit import CommitService
from gitqt.services.history import HistoryService
from gitqt.services.recovery import RecoveryService
from gitqt.services.ref_management import RefManagementService
from gitqt.services.repositories import RepositoryValidationCode, RepositoryValidator
from gitqt.services.settings import AppSettings, SettingsStore
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.repository_view import RepositoryView

pytest_plugins = ["test_visible_workflows"]


def services_at(tmp_path):
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    return services


def test_discovery_normalizes_subdirectory_and_linked_worktree(repo, runner, tmp_path):
    sub = repo / "nested" / "deep"
    sub.mkdir(parents=True)
    validator = RepositoryValidator(runner)
    assert validator.validate(sub).path == str(repo)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-b", "linked", str(linked))
    assert validator.validate(linked).path == str(linked)
    assert validator.validate(linked).is_valid


def test_discovery_rejects_bare_and_broken_git_files(tmp_path, runner):
    bare = tmp_path / "bare"
    git(tmp_path, "init", "--bare", str(bare))
    assert (
        RepositoryValidator(runner).validate(bare).code == RepositoryValidationCode.BARE_UNSUPPORTED
    )
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text("gitdir: /missing/gitqt-test\n")
    assert not RepositoryValidator(runner).validate(broken).is_valid


def test_init_dialog_creates_empty_branch_with_reviewable_starter_files(app, tmp_path):
    from gitqt.ui.init_dialog import InitDialog

    services = services_at(tmp_path)
    dialog = InitDialog(services)
    target = tmp_path / "new app"
    dialog.destination.setText(str(target))
    dialog.branch.setText("trunk")
    dialog.readme.setChecked(True)
    dialog.ignore.setChecked(True)
    dialog.initialize()
    assert dialog.path == str(target)
    assert git(target, "symbolic-ref", "--short", "HEAD").stdout == b"trunk\n"
    assert git(target, "status", "--porcelain").stdout.count(b"??") == 2
    view = RepositoryView(services)
    view.load_repository(str(target))
    assert view._branch_snapshot.tracking.branch_name == "trunk"
    assert not view._branch_snapshot.tracking.is_detached_head
    assert services.history.get_graph(target) == []
    view._observe_timer.stop()
    view.close()


def test_init_refuses_to_overwrite_existing_content(tmp_path, runner):
    (tmp_path / "keep").write_text("valuable")
    with pytest.raises(ValueError, match="empty"):
        RepositoryValidator(runner).initialize(tmp_path)
    assert (tmp_path / "keep").read_text() == "valuable"
    assert not (tmp_path / ".git").exists()


def test_dashboard_open_emits_normalized_repository(app, repo, tmp_path):
    from gitqt.ui.dashboard_view import DashboardView

    sub = repo / "nested"
    sub.mkdir()
    services = services_at(tmp_path)
    view = DashboardView(services)
    opened = []
    view.repository_opened.connect(opened.append)
    view._open_path(str(sub))
    assert opened == [str(repo)]
    assert services.settings_store.load().last_repository == str(repo)
    view.close()


def test_message_only_amend_preserves_unstaged_content_and_retains_original(repo, runner):
    original = git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "file.txt").write_text("unstaged content")
    service = CommitService(runner)
    preview = service.prepare_amend(repo)
    result = service.commit(repo, summary="Reworded", amend=True, expected_head=preview.oid)
    assert result.success, result.message
    assert (repo / "file.txt").read_text() == "unstaged content"
    assert git(repo, "show", "HEAD:file.txt").stdout == b"one\ntwo\nthree\n"
    assert any(
        git(repo, "rev-parse", entry.reference).stdout.strip() == original
        for entry in RecoveryService(runner).entries(repo)
        if "before-amend" in entry.description
    )


def test_amend_refuses_changed_head_and_empty_repository(repo, runner, tmp_path):
    service = CommitService(runner)
    preview = service.prepare_amend(repo)
    git(repo, "commit", "--allow-empty", "-qm", "Intervening commit")
    assert not service.commit(
        repo, summary="Wrong commit", amend=True, expected_head=preview.oid
    ).success
    empty = tmp_path / "empty"
    git(tmp_path, "init", str(empty))
    assert not service.commit(empty, summary="No previous commit", amend=True).success


def test_amend_ui_preloads_and_warns_for_published_commit(app, repo, runner, tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-u", "origin", "main")
    services = services_at(tmp_path)
    view = RepositoryView(services)
    view.load_repository(str(repo))
    composer = view._working_tree
    composer._summary_input.setText("Preserved draft")
    composer._amend_btn.setChecked(True)
    assert composer._summary_input.text() == "Initial"
    composer._amend_btn.setChecked(False)
    assert composer._summary_input.text() == "Preserved draft"
    composer._amend_btn.setChecked(True)
    composer._summary_input.setText("Updated initial")
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: (warnings.append(args[2]), QMessageBox.StandardButton.Yes)[1],
    )
    composer._do_commit()
    assert "origin/main" in warnings[0]
    assert git(repo, "log", "-1", "--format=%s").stdout == b"Updated initial\n"
    # Amending consumed the amended message, not the unrelated ordinary draft.
    assert composer._summary_input.text() == "Preserved draft"
    assert not composer._amend_btn.isChecked()
    view._observe_timer.stop()
    view.close()


def test_branch_tracking_rename_and_upstream(repo, runner, tmp_path):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "origin", "main:topic")
    git(repo, "fetch")
    service = RefManagementService(runner)
    service.track_branch(repo, "origin/topic", "local-topic")
    assert git(repo, "branch", "--show-current").stdout == b"local-topic\n"
    assert git(repo, "rev-parse", "--abbrev-ref", "@{upstream}").stdout == b"origin/topic\n"
    service.rename_branch(repo, "local-topic", "renamed")
    service.set_upstream(repo, "renamed", "origin/topic")
    assert git(repo, "branch", "--show-current").stdout == b"renamed\n"


def test_tag_inspection_delete_and_recovery_preserve_annotation(repo, runner):
    git(repo, "tag", "-a", "v1", "-m", "Release annotation")
    oid = git(repo, "rev-parse", "v1").stdout.strip()
    service = RefManagementService(runner)
    assert "Release annotation" in service.inspect_tag(repo, "v1")
    service.delete_tag(repo, "v1")
    recovery = RecoveryService(runner)
    entry = next(entry for entry in recovery.entries(repo) if entry.kind == "tag")
    recovery.recover(repo, entry, branch_name="restored-v1")
    assert git(repo, "rev-parse", "restored-v1").stdout.strip() == oid
    service.checkout_tag(repo, "restored-v1")
    assert git(repo, "symbolic-ref", "HEAD", check=False).returncode != 0


def test_remote_tag_push_delete_and_lease(repo, runner, tmp_path):
    from gitqt.services.git import GitCommandFailure

    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "tag", "-a", "v1", "-m", "Annotation")
    service = RefManagementService(runner)
    service.push_tag(repo, service.prepare_tag_push(repo, "origin", "v1"))
    stale = service.prepare_remote(repo, "origin", "tags", "v1")
    git(repo, "tag", "-af", "v1", "-m", "Replacement")
    git(repo, "push", "--force", "origin", "refs/tags/v1")
    with pytest.raises(GitCommandFailure):
        service.delete_remote(repo, stale)
    current = service.prepare_remote(repo, "origin", "tags", "v1")
    service.delete_remote(repo, current)
    assert git(bare, "show-ref", "--verify", "refs/tags/v1", check=False).returncode != 0
    assert git(repo, "cat-file", "-t", current.recovery_ref).stdout == b"tag\n"


def test_remote_branch_delete_retains_tip(repo, runner, tmp_path):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "origin", "main:topic")
    service = RefManagementService(runner)
    preview = service.prepare_remote(repo, "origin", "heads", "topic")
    service.delete_remote(repo, preview)
    assert git(bare, "show-ref", "--verify", "refs/heads/topic", check=False).returncode != 0
    assert git(repo, "rev-parse", preview.recovery_ref).stdout.strip().decode() == preview.oid


def test_ref_manager_ui_renames_branch_and_inspects_tag(app, repo, tmp_path):
    from gitqt.ui.ref_manager import RefManagerDialog

    services = services_at(tmp_path)
    git(repo, "tag", "-a", "v1", "-m", "Visible annotation")
    dialog = RefManagerDialog(services, str(repo), selection=("local_branch", "main"))
    dialog.value.setText("renamed")
    dialog.execute()
    assert git(repo, "branch", "--show-current").stdout == b"renamed\n"
    dialog.kind.setCurrentIndex(2)
    dialog.refs.setCurrentRow(0)
    assert "Visible annotation" in dialog.details.toPlainText()
    dialog.close()


@pytest.mark.parametrize(
    "name", ["unicodé.txt", "tab\tfile.txt", "line\nfile.txt", "literal[1].txt"]
)
def test_root_and_rename_history_keep_literal_paths(repo, runner, name):
    if os.name == "nt" and any(ord(char) < 32 for char in name):
        pytest.skip("Windows does not permit control characters in file names")
    (repo / name).write_text("first\n")
    git(repo, "add", "--", name)
    git(repo, "commit", "-qm", "Add unusual path")
    service = HistoryService(runner)
    changes = service.get_commit_files(repo, "HEAD")
    assert changes[0].path == name
    assert service.get_commit_file_diff(repo, "HEAD", name)
    git(repo, "mv", "--", name, "renamed.txt")
    git(repo, "commit", "-qm", "Rename")
    changes = service.get_commit_files(repo, "HEAD", force_refresh=True)
    assert changes[0].old_path == name
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.decode().strip()
    assert service.get_commit_files(repo, root)[0].path == "file.txt"


def test_history_paging_uses_stable_tips_after_external_commits(repo, runner):
    for index in range(8):
        git(repo, "commit", "--allow-empty", "-qm", f"Commit {index}")
    service = HistoryService(runner)
    tips = service.get_tips(repo)
    first = service.get_graph(repo, limit=4, refs=tips)
    git(repo, "commit", "--allow-empty", "-qm", "Later commit")
    second = service.get_graph(repo, limit=4, skip=4, refs=tips)
    assert len(first + second) == len({row.commit_full_hash for row in first + second}) == 8
    assert "Later commit" not in [row.summary for row in first + second]
    assert [row.commit_full_hash for row in first + second] == [
        row.commit_full_hash for row in service.get_graph(repo, limit=8, refs=tips)
    ]


def test_settings_interrupted_replace_keeps_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save(AppSettings(ui_font_family="Original"))
    replace = os.replace

    def fail_replace(source, target):
        if target == path:
            raise OSError("Interrupted write")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        store.save(AppSettings(ui_font_family="New"))
    assert store.load().ui_font_family == "Original"
    assert not list(tmp_path.glob(".settings.json.*"))


def test_settings_damage_recovers_last_good_copy_and_preserves_evidence(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save(AppSettings(crash_reports_enabled=False))
    store.save(AppSettings(crash_reports_enabled=True))
    path.write_bytes(b"{truncated")
    restored = store.load()
    assert not restored.crash_reports_enabled
    assert store.warning and "restored" in store.warning
    assert next(tmp_path.glob("*.corrupt-*")).read_bytes() == b"{truncated"


def test_preferences_update_preserves_recent_repository_changes(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings(recent_repositories=["/new/repository"]))
    store.update(ui_font_family="New Font")
    assert store.load().recent_repositories == ["/new/repository"]


def test_large_diff_is_explicitly_unavailable_and_cache_is_bounded(repo, runner):
    from gitqt.services.caching import TtlCache
    from gitqt.services.status import StatusService

    (repo / "file.txt").write_text("changed\n" * 10001)
    preview = StatusService(runner).get_preview(repo, "file.txt")
    assert not preview.hunks and "limit" in preview.message
    cache = TtlCache(ttl_seconds=100, max_entries=2)
    for value in range(10):
        cache.set(value, value)
    assert len(cache._entries) == 2
    assert cache.get(0) is None and cache.get(9) == 9


def test_history_errors_do_not_look_like_empty_results(repo, runner):
    from gitqt.services.git import GitCommandFailure

    with pytest.raises(GitCommandFailure):
        HistoryService(runner).get_commit_files(repo, "nonexistent-commit")


def test_large_git_output_is_rejected_before_rendering(repo, runner):
    from gitqt.services.git import GitOutputLimitError

    (repo / "file.txt").write_text("very long line" * 200_000)
    with pytest.raises(GitOutputLimitError, match="limit"):
        runner.run(["diff", "--", "file.txt"], cwd=repo)


def test_history_ignores_tags_that_do_not_point_to_commits(repo, runner):
    blob = git(repo, "rev-parse", "HEAD:file.txt").stdout.decode().strip()
    git(repo, "tag", "blob", blob)
    git(repo, "tag", "-a", "annotated-blob", blob, "-m", "Blob tag")
    service = HistoryService(runner)
    assert len(service.get_graph(repo, refs=service.get_tips(repo))) == 1


def test_merge_history_shows_first_parent_changes(repo, runner):
    git(repo, "checkout", "-b", "topic")
    (repo / "topic.txt").write_text("topic content\n")
    git(repo, "add", "topic.txt")
    git(repo, "commit", "-qm", "Topic")
    git(repo, "checkout", "main")
    git(repo, "commit", "--allow-empty", "-qm", "Main change")
    git(repo, "merge", "--no-ff", "topic", "-m", "Merge topic")
    history = HistoryService(runner)
    assert history.get_commit_files(repo, "HEAD")[0].path == "topic.txt"
    assert history.get_commit_file_diff(repo, "HEAD", "topic.txt")


def test_support_bundle_contains_bounded_sanitized_logs_only(tmp_path):
    import zipfile

    from gitqt.services.diagnostics import export_support_bundle

    services = services_at(tmp_path)
    services.settings_store.save(AppSettings(git_executable="private-path"))
    log = services.paths.log_dir / "gitqt.log.1"
    log.write_text("x" * 1_100_000 + "\nhttps://user:supersecret@example.invalid/repo\n")
    target = tmp_path / "support.zip"
    export_support_bundle(services, target)
    with zipfile.ZipFile(target) as bundle:
        assert "diagnostics.json" in bundle.namelist()
        assert not any("settings" in name for name in bundle.namelist())
        content = bundle.read("gitqt.log.1")
        assert len(content) <= 1_000_000
        assert b"supersecret" not in content


def test_remote_tag_ui_completes_preview_confirmation_and_delete(app, repo, tmp_path, monkeypatch):
    from gitqt.ui.ref_manager import RefManagerDialog

    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "tag", "v1")
    git(repo, "push", "origin", "refs/tags/v1")
    dialog = RefManagerDialog(services_at(tmp_path), str(repo))
    dialog.kind.setCurrentIndex(3)
    dialog.remote_tags()
    dialog.refs.setCurrentRow(0)
    confirmations = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: (confirmations.append(args[2]), QMessageBox.StandardButton.Yes)[1],
    )
    dialog.execute()
    assert confirmations and "refs/tags/v1" in confirmations[0]
    assert git(bare, "show-ref", "--verify", "refs/tags/v1", check=False).returncode != 0
    assert dialog.run_button.isEnabled() and not dialog.jobs.busy
    dialog.close()
