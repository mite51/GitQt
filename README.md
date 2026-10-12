# GitQt

`GitQt` is an open source Git desktop client built with Python and Qt.

Crash reports are local-only (no telemetry) and written to the app config `crash-reports` directory when enabled.

For contribution workflow and project expectations, see `CONTRIBUTING.md`.

## Installable builds

Standalone packages bundle Python and Qt; install Git 2.30+ separately. The Linux portable archive and Debian package are locally verified. Windows/macOS installer recipes and CI are present, with native acceptance and signing still pending. See [RELEASE.md](RELEASE.md) for build commands, platform requirements, installation, updates, removal, and release gates. Development builds are not published automatically.

## Creating repositories and managing references

Use **New Repository** on the dashboard to choose a new/empty directory, initial branch name, and optional README/.gitignore. Starter files remain uncommitted. Opening a subfolder resolves to the repository root; linked worktrees are supported. Bare repositories must be cloned into a working checkout before opening.

**Repository → Branches and Tags** offers branch rename, upstream setting, checkout of a remote branch as a local tracking branch, and remote branch deletion. Local tags can be inspected, checked out in detached HEAD, deleted, or pushed. Choose **Remote tags** and load the selected destination's tags to delete one. Remote deletions show the exact destination/object, retain recovery data, and refuse deletion if that object changed after the preview. Tag push does not overwrite an existing different tag. Remote tag lists require an explicit reload after changes.

Enable **Amend** to preload the latest commit message. You can change just its message without staging files; staged changes are included when present. Disabling Amend restores your previous draft. Confirmation identifies known remote refs containing the commit, based on the last fetch. The old commit is retained in Recovery, and a changed HEAD requires a fresh preview.

## Settings and diagnostics

Settings save atomically, with a previous-good backup. Damaged settings are preserved in a `.corrupt-…` file and recovered from the backup where possible; the dashboard explains the recovery. **Application → Export Support Bundle** writes version information and sanitized, bounded application logs to a local ZIP. It excludes settings and repository contents. Review before sharing: local paths can still appear. **Application → About GitQt** shows the version and update approach.

## Development

Create and activate the virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the project and development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest
```

Launch the dashboard application shell:

```bash
python -m gitqt.main
```

## Working with changes

- Select a file in **Staged** or **Unstaged** to review that specific side. New text files can be previewed before staging.
- Choose a hunk from the diff toolbar, or highlight added/deleted lines within one hunk. The action changes to **Stage** or **Unstage** according to the selected side.
- **Discard Hunk / Discard Selected Lines** operates on unstaged changes only. Review the confirmation details first. GitQt checks that the preview is current and saves a backup before changing the file.
- Use Ctrl/Cmd-click or Shift-click to select multiple files. **Stage All / Unstage All** includes files hidden by the current filters.
- Binary files, symlinks, submodules, and rename/copy changes use whole-file staging; unavailable partial actions are disabled with explanatory messages.

Discard backups are kept in `gitqt-discard-backups` inside the repository's actual Git directory (which can be separate for a linked worktree). The operation log records the backup folder. Its `content` file contains the original bytes; `info.json` records the original path, existence, and mode. Open **Repository → Recovery** to restore a backup. GitQt backs up the current file before replacing it and leaves the index unchanged. Backups are retained until manually removed.

## Finishing an interrupted operation

The Working Tree panel shows the current merge, rebase, cherry-pick, or revert even after its last conflict is staged. Resolve files in your editor or configured merge tool, refresh, and stage the resolutions. Use **Continue** to finish. **Skip Commit** is available for rebase/cherry-pick/revert; **Abort** asks Git to return to the operation's starting state. Skip and abort require confirmation because resolution edits may be lost. GitQt rechecks the operation before acting and reports any Git failure.


## Setup and private repositories

Git 2.30 or newer is required. **Application → Preferences** is available from the dashboard as well as an open repository. Save validates the Git executable in the background. A changed executable path requires a restart, as the confirmation explains. Appearance, external-tool paths, and local crash-report preferences apply immediately.

Use **Application → Commit Identity** to set a global name/email or an override for the current repository. **Application → Connection Setup** checks the Git version, configured credential helpers, and SSH-agent keys. It also offers a connection test, retry, cancellation, and loading an existing private key into a running SSH agent. Start your system SSH agent before launching GitQt so its environment is inherited.

GitQt uses Git's existing credential helpers and SSH agents. When credentials are missing, desktop prompts accept HTTPS usernames/tokens, SSH passphrases, and host-verification answers. Prompt responses stay in memory and are sent through a token-authenticated localhost channel; GitQt does not save them to settings or logs. A configured Git credential helper controls any subsequent credential storage. For expired access, update/remove the saved entry in that helper and retry. Provider OAuth/account management remains future scope. See the official [Git credential-helper documentation](https://git-scm.com/docs/gitcredentials) and [OpenSSH askpass behavior](https://man.openbsd.org/ssh.1).

## Remote operations

- **Repository → Remotes** adds, renames, edits, and removes remotes, with separate fetch/push URLs. Enter credentials through the prompt rather than embedding them in URLs.
- **Fetch** lets you choose a remote and prune stale tracking branches. **Pull** offers fast-forward-only (default), merge, and rebase.
- **Push** shows the destination URL and branch and can publish the current branch with an upstream. Force-with-lease first fetches and retains the destination tip, previews remote-only commits, and requires confirmation. The push pins the previewed source commit and destination URL. Its explicit expected remote commit prevents overwriting changes made after the preview, even if another tool fetches in between. Force push does not change upstream configuration; use a normal push afterward if tracking needs to be set.
- Clone and transfers show live progress and offer cancellation. Cancellation or timeout stops the process tree and refreshes repository state; completed changes are not rolled back. A cancelled clone may leave a partial destination directory. Inspect it before removing it or selecting a new destination.

All Git work runs in background jobs. A repository write disables conflicting actions and navigation until completion; read results from old repositories or filters are discarded. Reads and writes also share a lock across linked worktrees in this app. External Git tools still rely on Git's own locking.

GitQt checks the visible repository for external changes every two seconds and on application activation. Local refresh preserves drafts, file selection, and history position. It does not fetch remote data automatically.

External merge tools support filenames containing spaces and Unicode. GitQt refuses names containing shell wildcards, quotes, backslashes, or control characters because Git’s merge-tool launcher can misinterpret them. Resolve those files in your editor, then stage them in GitQt.

## Recovery

**Repository → Recovery** lists retained commit/stash references, discarded-file backups, removed-remote URLs, and the latest 100 HEAD reflog entries.

- Recover committed history by creating a new branch; the current checkout is unchanged.
- Before reset, GitQt retains HEAD and any tracked index/worktree changes. Before dropping or popping a stash, it retains the stash object. Saved work can be applied with its index state into a clean working tree; conflicts are reported without removing the recovery reference.
- Hard reset is refused while untracked or ignored files exist because tracked-work backups cannot recover them. Move those files outside the repository first. Hard reset is also unavailable in repositories containing submodules because their nested worktrees are not backed up. Soft and mixed reset remain available and do not reset nested worktrees.
- Amend retains the previous commit; tag deletion retains the full tag object and annotation. Restore a deleted tag under a new tag name in Recovery.
- Branch deletion retains the branch tip. Remote removal retains tracked commit tips and the remote's fetch/push URLs. Restoring a remote does not recreate its previous upstream/custom configuration; fetch and set upstream again.
- File recovery preserves newer file contents in another backup before restoring. It does not change staging.

GitQt recovery references and backups have no automatic expiry; deleting them manually removes this protection. Reflog entries follow Git's normal expiration rules. Recovery is a set of specific restore actions, not a general undo/redo guarantee. Recovery-only refs are excluded from the default history graph.

Desktop authentication, process-tree cleanup, remote dialogs, first-run setup, and the installed Linux payload have automated local tests. Local acceptance includes a rejecting HTTP endpoint with credential retry and an encrypted SSH key unlocked through desktop askpass; these do not substitute for a real hosting provider or platform credential store. Real provider credentials, Windows/macOS helper behavior, native clean-machine installation, signing/notarization, and the publisher dependency/source audit still require release acceptance.
