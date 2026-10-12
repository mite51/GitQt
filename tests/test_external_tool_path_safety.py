"""External tools must not interpret selected file names as multiple paths."""

import os
import shlex
from pathlib import Path

import pytest
from test_visible_workflows import git

from gitqt.services.external_tools import ExternalToolService

pytest_plugins = ["test_visible_workflows"]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell and wildcard filename fixture")


def prepare_files(repo, name):
    for path in (name, "victim.txt"):
        (repo / path).write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Two files")


def test_external_diff_opens_only_literal_selected_path(repo, runner, tmp_path):
    prepare_files(repo, "*.txt")
    for name in ("*.txt", "victim.txt"):
        (repo / name).write_text("modified\n")
    calls = tmp_path / "calls"
    helper = tmp_path / "diff-tool"
    helper.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$2\" >> {shlex.quote(str(calls))}\n")
    helper.chmod(0o755)
    result = ExternalToolService(runner).launch_diff_tool(
        repo, tool_path=str(helper), file_path="*.txt"
    )
    assert result.success, result.stderr
    assert [Path(line).name for line in calls.read_text().splitlines()] == ["*.txt"]


@pytest.mark.parametrize(
    "name",
    [
        "*.txt",
        "file?.txt",
        "file[1].txt",
        "line\nname.txt",
        "tab\tname.txt",
        'quote"name.txt',
        "back\\slash.txt",
    ],
)
def test_unsafe_merge_filename_leaves_all_conflicts_and_index_untouched(
    repo, runner, tmp_path, name
):
    prepare_files(repo, name)
    git(repo, "checkout", "-b", "topic")
    for path in (name, "victim.txt"):
        (repo / path).write_text("incoming\n")
    git(repo, "commit", "-am", "Incoming")
    git(repo, "checkout", "main")
    for path in (name, "victim.txt"):
        (repo / path).write_text("current\n")
    git(repo, "commit", "-am", "Current")
    assert git(repo, "merge", "topic", check=False).returncode != 0
    index = git(repo, "ls-files", "--stage", "-z").stdout
    contents = {path: (repo / path).read_bytes() for path in (name, "victim.txt")}
    helper = tmp_path / "merge-tool"
    helper.write_text('#!/bin/sh\ncp -- "$3" "$4"\n')
    helper.chmod(0o755)
    result = ExternalToolService(runner).launch_merge_tool(
        repo, tool_path=str(helper), file_path=name
    )
    assert not result.success and "Resolve it in your editor" in result.message
    assert git(repo, "ls-files", "--stage", "-z").stdout == index
    assert all((repo / path).read_bytes() == data for path, data in contents.items())


def test_unicode_and_space_merge_filename_resolves_only_selected_conflict(repo, runner, tmp_path):
    name = "unicodé conflict.txt"
    prepare_files(repo, name)
    git(repo, "checkout", "-b", "topic")
    for path in (name, "victim.txt"):
        (repo / path).write_text("incoming\n")
    git(repo, "commit", "-am", "Incoming")
    git(repo, "checkout", "main")
    for path in (name, "victim.txt"):
        (repo / path).write_text("current\n")
    git(repo, "commit", "-am", "Current")
    assert git(repo, "merge", "topic", check=False).returncode != 0
    other_index = git(repo, "ls-files", "--stage", "--", "victim.txt").stdout
    other_content = (repo / "victim.txt").read_bytes()
    helper = tmp_path / "merge-tool"
    helper.write_text('#!/bin/sh\ncp -- "$3" "$4"\n')
    helper.chmod(0o755)
    result = ExternalToolService(runner).launch_merge_tool(
        repo, tool_path=str(helper), file_path=name
    )
    assert result.success, result.stderr
    assert not git(repo, "ls-files", "--unmerged", "--", name).stdout
    assert git(repo, "show", f":{name}").stdout == b"incoming\n"
    assert git(repo, "ls-files", "--stage", "--", "victim.txt").stdout == other_index
    assert (repo / "victim.txt").read_bytes() == other_content
