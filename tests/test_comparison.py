"""Real repositories and visible comparison controls, including patch-fidelity edges."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from gitqt.parsers.diff import DiffLineType
from gitqt.services.comparison import ComparisonRequest, ComparisonService, DiffOptions
from gitqt.services.git import GitLocator, GitRunner
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.comparison_dialog import ComparisonDialog, ComparisonRenderer, _word_ranges


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        git(tmp_path, "config", key, value)
    (tmp_path / "file.py").write_text('value = "old word"\nsecond = 2\n', encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Initial\n\nFull body paragraph.\n\nFinal paragraph.")
    return tmp_path


@pytest.fixture
def service():
    return ComparisonService(GitRunner(GitLocator().discover()))


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_root_commit_and_full_message(repo, service):
    comparison = service.compare(repo, ComparisonRequest(target="HEAD", parent=0))
    assert comparison.commit.parents == ()
    assert comparison.commit.message == "Initial\n\nFull body paragraph.\n\nFinal paragraph."
    assert [(change.status, change.path) for change in comparison.files] == [("A", "file.py")]
    preview = service.file_diff(repo, comparison, comparison.files[0])
    assert [line.text for line in preview.hunks[0].lines] == ['value = "old word"', "second = 2"]
    assert "empty tree" in comparison.description


def make_merge(repo):
    git(repo, "checkout", "-qb", "feature")
    (repo / "feature.txt").write_text("feature\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Feature")
    git(repo, "checkout", "-q", "main")
    (repo / "main.txt").write_text("main\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Main")


def test_direct_and_directional_merge_base(repo, service):
    make_merge(repo)
    direct = service.compare(repo, ComparisonRequest("main", "feature"))
    merge_base = service.compare(repo, ComparisonRequest("main", "feature", merge_base=True))
    reverse = service.compare(repo, ComparisonRequest("feature", "main", merge_base=True))
    assert {file.path for file in direct.files} == {"main.txt", "feature.txt"}
    assert [file.path for file in merge_base.files] == ["feature.txt"]
    assert [file.path for file in reverse.files] == ["main.txt"]
    assert "merge base" in merge_base.description
    git(repo, "merge", "--no-ff", "feature", "-m", "Merge\n\nDetailed merge message")
    first = service.compare(repo, ComparisonRequest(parent=1))
    second = service.compare(repo, ComparisonRequest(parent=2))
    assert len(first.commit.parents) == 2
    assert [file.path for file in first.files] == ["feature.txt"]
    assert [file.path for file in second.files] == ["main.txt"]
    with pytest.raises(ValueError, match="parent"):
        service.compare(repo, ComparisonRequest(parent=3))


def test_complete_refs_and_pinned_commit_endpoints(repo, service):
    for index in range(125):
        git(repo, "branch", f"branch-{index}")
    git(repo, "tag", "tag-name")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    refs = service.references(repo)
    assert len(refs) == 129
    assert {"HEAD", "refs/heads/main", "refs/tags/tag-name", "refs/remotes/origin/main"} <= set(
        refs
    )
    comparison = service.compare(repo, ComparisonRequest(parent=0))
    (repo / "file.py").write_text("new commit\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "New")
    diff = service.file_diff(repo, comparison, comparison.files[0])
    assert "old word" in diff.raw
    assert "new commit" not in diff.raw


def test_staged_worktree_and_index_base(repo, service):
    file = repo / "file.py"
    file.write_text('value = "staged"\nsecond = 2\n')
    git(repo, "add", ".")
    file.write_text('value = "unstaged"\nsecond = 2\n')
    (repo / "untracked.txt").write_text("untracked")
    staged = service.compare(repo, ComparisonRequest(target_kind="index"))
    worktree = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    unstaged = service.compare(repo, ComparisonRequest(base="INDEX", target_kind="worktree"))
    stage_diff = service.file_diff(repo, staged, staged.files[0]).raw
    tree_diff = service.file_diff(repo, worktree, worktree.files[0]).raw
    unstaged_diff = service.file_diff(repo, unstaged, unstaged.files[0]).raw
    assert '+value = "staged"' in stage_diff and "unstaged" not in stage_diff
    assert '-value = "old word"' in tree_diff and '+value = "unstaged"' in tree_diff
    assert '-value = "staged"' in unstaged_diff and '+value = "unstaged"' in unstaged_diff
    assert [change.path for change in worktree.files] == ["file.py"]
    assert "untracked files are excluded" in worktree.description


@pytest.mark.parametrize("kind", ["worktree", "index"])
def test_mutable_snapshot_requires_refresh_after_changes(repo, service, kind):
    (repo / "file.py").write_text("first\n")
    git(repo, "add", ".")
    comparison = service.compare(repo, ComparisonRequest(target_kind=kind))
    (repo / "file.py").write_text("later contents\n")
    if kind == "index":
        git(repo, "add", ".")
    with pytest.raises(ValueError, match="Press Compare"):
        service.file_diff(repo, comparison, comparison.files[0])


def test_literal_rename_non_ascii_crlf_no_eol(repo, service):
    name = ":(glob)雪[1].txt"
    old = repo / name
    old.write_bytes(b"one\r\ntwo\r\nlast")
    git(repo, "--literal-pathspecs", "add", "--", name)
    git(repo, "commit", "-qm", "Add literal filename")
    renamed = "renamed-雪[1].txt"
    old.rename(repo / renamed)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Rename")
    comparison = service.compare(repo, ComparisonRequest("HEAD~1", "HEAD"))
    assert comparison.files[0].old_path == name
    assert comparison.files[0].path == renamed
    rename_diff = service.file_diff(repo, comparison, comparison.files[0])
    assert "rename from" in rename_diff.raw
    (repo / renamed).write_bytes(b"one\r\nTWO\r\nlast updated")
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    diff = service.file_diff(repo, comparison, comparison.files[0])
    assert any(line.text == "TWO\r" for line in diff.hunks[0].lines)
    assert any("No newline" in line.text for line in diff.hunks[0].lines)
    assert "\r\n" in diff.raw


def test_literal_path_does_not_expand_globs(repo, service):
    for name in ("a[1].txt", "a1.txt"):
        (repo / name).write_text("original\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Add")
    (repo / "a[1].txt").write_text("literal only\n")
    (repo / "a1.txt").write_text("must not appear\n")
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    change = next(change for change in comparison.files if change.path == "a[1].txt")
    diff = service.file_diff(repo, comparison, change)
    assert "literal only" in diff.raw and "must not appear" not in diff.raw


def test_whitespace_context_and_large_binary_fallback(repo, service):
    (repo / "file.py").write_text('value  = "old word"\nsecond = 2\n')
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    exact = service.file_diff(repo, comparison, comparison.files[0], DiffOptions(0))
    ignored = service.file_diff(repo, comparison, comparison.files[0], DiffOptions(0, "all-space"))
    assert len(exact.hunks) == 1 and exact.hunks[0].old_count == 1
    assert not ignored.hunks
    (repo / "large.png").write_bytes(b"\x00" + b"x" * 3_000_000)
    git(repo, "add", "large.png")
    comparison = service.compare(repo, ComparisonRequest(target_kind="index"))
    change = next(change for change in comparison.files if change.path == "large.png")
    diff = service.file_diff(repo, comparison, change)
    assert not diff.hunks
    assert "Binary/image preview unavailable" in diff.notice
    assert "Binary files" in diff.raw
    (repo / "file.py").write_text("x" * 2_100_000)
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    change = next(change for change in comparison.files if change.path == "file.py")
    diff = service.file_diff(repo, comparison, change)
    assert not diff.hunks and "2 MB preview limit" in diff.notice


def test_dialog_controls_commit_parent_and_find(app, repo, service):
    make_merge(repo)
    git(repo, "merge", "--no-ff", "feature", "-m", "Merge\n\nLong body")
    dialog = ComparisonDialog(service, str(repo), commit="HEAD", pool=ImmediateWorkerPool())
    assert dialog.parent_picker.currentData() == 1
    assert "Long body" in dialog.message.toPlainText()
    assert dialog.files.count() == 1
    assert "feature.txt" in dialog.files.item(0).text()
    dialog.parent_picker.setCurrentIndex(dialog.parent_picker.findData(2))
    assert "main.txt" in dialog.files.item(0).text()
    dialog.side_by_side.setChecked(True)
    assert "main" in dialog.renderer.right.toPlainText()
    dialog.search.setText("main")
    dialog.find_button.click()
    assert dialog.renderer.right.textCursor().hasSelection()
    dialog.next_button.click()
    dialog.context.setValue(0)
    dialog.word_diff.setChecked(False)
    assert dialog._diff is not None
    dialog.target.setEditText("HEAD~1")
    assert dialog._comparison is None and dialog.files.count() == 0
    assert dialog.mode.isEnabled()
    dialog.compare_button.click()
    assert dialog._comparison.commit.oid == git(repo, "rev-parse", "HEAD~1").decode().strip()
    dialog.close()


@pytest.mark.parametrize("old,new", [(b"old\n", b"new"), (b"old", b"new\n"), (b"old", b"new")])
def test_side_by_side_no_eol_attributed_to_correct_side(app, repo, service, old, new):
    (repo / "file.py").write_bytes(old)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Old")
    (repo / "file.py").write_bytes(new)
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    diff = service.file_diff(repo, comparison, comparison.files[0])
    renderer = ComparisonRenderer()
    renderer.set_diff(diff, side_by_side=True)
    assert ("No newline" in renderer.left.toPlainText()) == (not old.endswith(b"\n"))
    assert ("No newline" in renderer.right.toPlainText()) == (not new.endswith(b"\n"))
    assert renderer.left.blockCount() == renderer.right.blockCount()
    assert any(line.type == DiffLineType.META for line in diff.hunks[0].lines)
    assert "<b>" not in renderer.left.toPlainText()


def test_display_transforms_preserve_patch_bytes_and_escape_html(app, repo, service):
    (repo / "file.py").write_bytes(b'value = "<b>new word</b>"\r\nsecond = 2\r\n')
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    diff = service.file_diff(repo, comparison, comparison.files[0])
    original = diff.raw
    renderer = ComparisonRenderer()
    renderer.set_diff(diff, side_by_side=True)
    assert "<b>new word</b>" in renderer.right.toPlainText()
    assert "␍" in renderer.right.toPlainText()
    renderer.set_diff(diff, side_by_side=False, word_diff=False)
    assert diff.raw == original
    assert _word_ranges("old word", "new word") == ([(0, 3)], [(0, 3)])
    assert _word_ranges("x" * 5000, "y" * 5000) == ([], [])


class QueuedPool:
    def __init__(self):
        self.pending = []

    def submit(self, *, key, run, on_result):
        self.pending.append((run, on_result))

    def finish(self, index):
        run, on_result = self.pending[index]
        on_result(SimpleNamespace(value=run()))


def test_stale_async_comparison_and_file_selection_are_ignored(app, repo, service):
    (repo / "file.py").write_text("modified\n")
    pool = QueuedPool()
    dialog = ComparisonDialog(service, str(repo), pool=pool)
    pool.finish(0)  # reference list
    dialog.base.setEditText("HEAD~100")  # invalidates the pending comparison before it runs
    pool.finish(1)
    assert dialog._comparison is None
    dialog.base.setEditText("HEAD")
    dialog.compare_button.click()
    pool.finish(2)
    assert dialog.files.count() == 1
    dialog.context.setValue(0)  # supersede the first per-file request
    pool.finish(3)
    assert dialog._diff is None
    pool.finish(4)
    assert dialog._diff is not None
    dialog.close()


def test_large_single_line_preview_is_bounded_and_search_visits_both_sides(app, repo, service):
    (repo / "file.py").write_text('value = "new word"\nsecond = 2\n')
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    diff = service.file_diff(repo, comparison, comparison.files[0])
    renderer = ComparisonRenderer()
    renderer.set_diff(diff, side_by_side=True)
    assert renderer.find("word")
    assert renderer.left.textCursor().selectedText() == "word"
    assert renderer.find("word")
    assert renderer.right.textCursor().selectedText() == "word"
    (repo / "file.py").write_text("x = 1; " * 100_000)
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    diff = service.file_diff(repo, comparison, comparison.files[0])
    renderer.set_diff(diff)
    assert "highlighting are disabled" in diff.notice
    assert renderer._syntax is False and renderer._word_diff is False
    assert len(renderer.left.toPlainText()) > 700_000
    assert renderer.left.blockCount() < 10


@pytest.mark.parametrize("kind", ["index", "worktree"])
def test_changed_file_list_race_is_rejected(repo, service, kind, monkeypatch):
    (repo / "other.txt").write_text("original\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Other")
    (repo / "file.py").write_text("changed\n")
    if kind == "index":
        git(repo, "add", ".")
    original_run = service._runner.run
    edited = False

    def racing_run(args, **kwargs):
        nonlocal edited
        result = original_run(args, **kwargs)
        if "--name-status" in args and not edited:
            edited = True
            git(repo, "restore", "--source=HEAD", "--staged", "--worktree", "--", "file.py")
            (repo / "other.txt").write_text("changed instead\n")
            if kind == "index":
                git(repo, "add", ".")
        return result

    monkeypatch.setattr(service._runner, "run", racing_run)
    with pytest.raises(ValueError, match="Press Compare"):
        service.compare(repo, ComparisonRequest(target_kind=kind))


def test_visible_endpoint_mode_and_snapshot_controls(app, repo, service):
    make_merge(repo)
    dialog = ComparisonDialog(service, str(repo), pool=ImmediateWorkerPool())
    dialog.kind.setCurrentIndex(dialog.kind.findData("commit"))
    dialog.base.setEditText("main")
    dialog.target.setEditText("feature")
    dialog.compare_button.click()
    assert dialog.files.count() == 2
    dialog.mode.setCurrentIndex(1)
    dialog.compare_button.click()
    assert dialog.files.count() == 1
    assert "feature.txt" in dialog.files.item(0).text()
    assert "merge base" in dialog.direction.text()
    (repo / "file.py").write_text("staged\n")
    git(repo, "add", ".")
    (repo / "file.py").write_text("unstaged\n")
    dialog.kind.setCurrentIndex(dialog.kind.findData("index"))
    dialog.base.setEditText("HEAD")
    dialog.compare_button.click()
    assert "+staged" in dialog._diff.raw
    assert not dialog.mode.isEnabled() and not dialog.target.isEnabled()
    dialog.kind.setCurrentIndex(dialog.kind.findData("worktree"))
    dialog.base.setEditText("INDEX")
    dialog.compare_button.click()
    assert "-staged" in dialog._diff.raw and "+unstaged" in dialog._diff.raw
    dialog.whitespace.setCurrentIndex(1)
    assert dialog._diff is not None
    dialog.close()


def test_many_line_preview_limit(repo, service):
    (repo / "file.py").write_text("small line\n" * 10_001)
    comparison = service.compare(repo, ComparisonRequest(target_kind="worktree"))
    diff = service.file_diff(repo, comparison, comparison.files[0])
    assert not diff.hunks and "10,000-line preview limit" in diff.notice
