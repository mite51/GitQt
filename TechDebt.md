# GitQt feature gaps and technical debt

Reviewed: 2026-10-12. Target: a complete, local-first desktop Git client with the everyday workflow coverage users associate with GitKraken Desktop.

This backlog comes from inspecting the implementation, UI wiring, tests, product requirements, and release configuration, with GitKraken's official documentation as a comparison. It distinguishes missing features from partially implemented workflows. Some larger items are explicitly deferred by `PRD.md`; they are future product scope, not failures to deliver the original MVP. Priorities are recommendations for GitQt, not a requirement to copy every GitKraken feature.

## Current baseline

Already implemented: opening and remembering repositories; cloning with branch and recursive-submodule options; staged/unstaged file lists; whole-file staging; inline text diffs; commit creation, amend with staged changes, and sign-off; a paginated commit graph with branch filters; local branch creation/checkout/deletion; ahead/behind and detached-HEAD indicators; fetch/pull/push; stash create/apply/pop/drop; lightweight and annotated tag creation; basic cherry-pick/revert/merge/rebase/reset; conflict detection and external tools; preferences, local crash reports, and cross-platform test CI.

These features should be extended rather than replaced. In particular, basic rebase, amend, sign-off, graph pagination, external merge tools, and last-repository restoration are **not missing**.

Validation performed:

- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`: **149 passed**, with four Qt deprecation warnings about `invalidateRowsFilter()`.
- Temporary real-repository probes confirmed: no preview hunks for an untracked text file; no affected files listed for the initial commit; message-only amend rejected without staged changes; a partially staged file's selection resolves to the unstaged diff without a way to specify which side was selected.
- UI connections and asynchronous workflows were reviewed in source. Interactive desktop behavior, real provider authentication, installed application bundles, and macOS/Windows behavior were not manually exercised. Concurrency findings below are code-level risks requiring targeted regression tests.

## Implementation progress — delivery step 1

The first delivery step is implemented for the supported text workflows. Original review findings below are retained as the review baseline; use this table for current status.

| Item | Current status | Delivered / remaining |
| --- | --- | --- |
| TD-01 | Implemented for regular text changes | Hunk picker; stage/unstage/discard a hunk or selected changed lines within one hunk; multi-file and all-file stage/unstage. Discard requires confirmation, rejects stale previews, and saves a local file backup. Binary, symlink, submodule, and rename/copy partial actions are explicitly disabled; whole-file staging remains available. |
| TD-02 | Implemented | Explicit staged/unstaged selection, untracked text previews, and explanatory empty/binary/metadata/symlink/submodule states. External diff follows the selected side. |
| TD-03 | Implemented for merge/rebase/cherry-pick/revert | Persistent operation detection, staging resolutions, Continue/Skip/Abort controls as appropriate, fresh-state validation, and restoration from Git state after reopening. Tests cover completion and abort for all four operations, skip for supported operations, and linked worktrees. |
| TD-05 | Failure cleanup fixed; larger item remains open | Remote timeout, launch, worker, and submission exceptions restore toolbar controls and allow retry. Streaming progress, process cancellation, and broader timeout policy remain for step 2. |
| TD-11 | Initial discard protection added; larger item remains open | Discard writes original bytes plus path/mode metadata to `gitqt-discard-backups` in the repository's Git directory, and records the location in the operation log. A recovery browser, undo journal, and other destructive-action recovery remain open. |

Validation for this delivery: **194 tests passed** (45 new regression tests), with the same four pre-existing Qt deprecation warnings. Changed Python files pass Ruff and Black formatting checks. Coverage includes real temporary repositories, UI button wiring, a real worker-thread failure callback, manual-refresh diff updates, and rendered working-tree/conflict controls. Project-wide Ruff still reports 13 findings in untouched files. Native macOS/Windows execution remains unverified in this local run.


## Implementation progress — delivery step 2

Implemented the session-isolation foundation first, then background execution and process controls. Added persistent recovery before exposing remote removal and force-with-lease. The original findings below remain historical review evidence; this table supersedes their implementation status.

| Item | Current status | Delivered / remaining |
| --- | --- | --- |
| TD-06 | Implemented | Session generations and per-channel request identities reject old results/errors. Workers operate on separate view-model snapshots. Mutations are not coalesced; conflicting controls/navigation are gated, with repository locks shared across linked worktrees. Fixed a scheduler race in which an older job could remove a newer queued job. |
| TD-04 | Implemented | Clone, status/diff/history reads, commit/staging, branches, tags, stashes, commit operations, external tools, setup, and recovery use background jobs. UI updates return through Qt signals. Responsiveness tests cover slow clone/checkout/diff/commit paths, including a real slow commit hook. |
| TD-05 | Implemented; platform acceptance pending | Live transfer/hook output, per-operation timeout defaults, cancellable clone/transfers/connection tests, process-tree termination, secret redaction, busy cleanup, and refresh after interruption. Local tests cover launch errors, timeout, child-process cancellation, authentication cancellation, and retry. Windows process-tree handling still needs execution on Windows. Cancellation does not promise rollback. |
| TD-07 | Desktop integration implemented; provider/platform acceptance remains open | Git/OpenSSH askpass prompts use an authenticated localhost bridge, existing helpers/agents remain active, Connection Setup offers diagnostics/retry/cancel and SSH-key loading. Tests exercise real Git HTTPS credential requests, repeated prompts, SSH passphrase handoff, and cancellation during a visible prompt without storing secrets. Actual HTTPS/SSH hosting-provider flows, expired provider access, locked-key unlocking, and platform keychains still require acceptance testing. Key/agent creation and provider OAuth are not implemented. |
| TD-08 | Implemented for single-destination branch transfers | Remote CRUD with URL inspection; remote/branch choices; first publish with upstream; prune; explicit merge/rebase/fast-forward-only pulls; force-with-lease with retained destination tip and commit-count preview. The lease pins the previewed remote object ID; the pushed source commit and destination URL are pinned too. Force push leaves upstream configuration unchanged. Real bare-remote tests cover publish, distinct fetch/push URLs, divergence, remote recovery, and intervening external fetch/push. Multi-push-URL/custom-refspec editors remain outside this UI. |
| TD-09 | Implemented | Debounced polling of local status/refs/HEAD/index/config and operation state, plus application-activation refresh. Linked-worktree metadata is resolved correctly. Drafts, selections, and scroll position are preserved. Activation callbacks disconnect when a repository view is destroyed. No implicit background network fetch. |
| TD-10 | Implemented with explicit restart for Git-path changes | Preferences, identity, and connection setup are available from the application menu/dashboard. Background validation requires Git 2.30+. Global/repository author settings are editable; startup reports missing/unsupported Git. Git-path changes explicitly require restart; crash-report hooks and other supported preferences change immediately. |
| TD-11 | Implemented for documented recovery actions | Recovery browser for retained commits/stashes, file backups, remote URLs, and HEAD reflog. Reset retains HEAD and tracked work; branch deletion and stash pop/drop retain objects. File restoration saves newer edits first. Hard reset refuses untracked/ignored files and repositories containing submodules, whose nested worktrees are not backed up. Recovery refs survive restart and Git garbage collection; reflog remains subject to Git expiry. No universal undo; remote custom/upstream config is not restored. |

Validation: **230 tests passed**, including **36 new regression tests** for this step; four existing Qt deprecation warnings remain. Credential tests require permission to bind a localhost socket and never contact an external host. New modules pass Ruff; repository-wide lint still reports unrelated existing style findings. Offscreen visual checks covered transfer, preferences, and connection-setup dialogs. Cross-platform desktop/manual provider acceptance remains explicitly open under TD-05 and TD-07.


## Implementation progress — delivery step 3

Implemented the daily-driver workflows and distribution foundations. The original findings below remain historical review evidence; this table gives the current status.

| Item | Current status | Delivered / remaining |
| --- | --- | --- |
| TD-12 | Linux artifacts built and smoke-tested; native release acceptance pending | Standalone app and credential helper, bundled Qt/plugins/fonts/icons, reproducible Linux/Windows portable archives, Debian installer/desktop entry, Windows Inno Setup recipe, macOS app/DMG recipe, native lifecycle CI gates, version/tag consistency checks, final checksum verification, license/notices inventory, and source/wheel builds. Isolated-root Debian install/reinstall/removal passes locally. Old-to-new upgrade, Windows/macOS execution, publisher signing/notarization, clean-machine desktop acceptance, and corresponding-source/dependency-license audit remain release gates in `RELEASE.md`. No public release or automatic updater. |
| TD-13 | Implemented | Dashboard New Repository with configurable initial branch and optional uncommitted README/.gitignore; asynchronous Git-based discovery normalizes subfolders and supports linked worktrees. Empty repositories show their unborn branch correctly. Bare repositories are explicitly rejected with guidance; broken Git metadata reports a validation error. |
| TD-14 | Implemented for documented single-destination workflows | Branch rename, upstream setting, tracking checkout, remote branch deletion; local tag inspection/checkout/deletion/push and remote tag loading/deletion. Sidebar actions and Repository → Branches and Tags expose the workflows. Remote deletion retains the exact object and uses an explicit lease against the captured destination URL/object. Tag recovery preserves annotated objects and creates a new tag. Existing tags are never force-overwritten by tag push. |
| TD-18 | Amend corrections implemented; broader composer work remains | Preload the current message, restore the previous draft when leaving Amend, allow message-only amend, reject a changed HEAD, warn about known remote refs containing the commit, and retain the old commit in Recovery. Published-state detection uses locally known remote refs, with an explicit freshness caveat. Templates, signing controls, co-authors, and durable per-repository drafts remain future scope. |
| TD-29 | Foundations implemented | Stable-tip history pagination fetches new pages, with a 5,000-row view limit; bounded LRU caches and explicit 2 MB / 10,000-line diff limits; NUL-delimited affected-file parsing and literal diff paths; root/first-parent merge coverage; no Qt filter deprecation warnings. A repeatable 5,000-commit benchmark establishes initial budgets. Larger branching graphs, non-UTF-8 paths, network filesystems, total GUI/process memory, and native OS/Git-version acceptance remain open. |
| TD-30 | Foundations implemented | Atomic/fsynced settings replacement and previous-good backup, preserved corrupt-file recovery with a visible notice, fresh preference updates that preserve recents, bounded rotating logs with command duration/exit status, redacted log/trace output, and an opt-in sanitized local support ZIP. History failures remain visible instead of becoming empty results. Cross-process settings coordination and broader actionable-error UX remain future work. |

Validation: **258 tests passed** (**28 added** in this step), including real-repository GUI workflows, stale remote deletion leases, message-only amend, root/merge/unusual-path history, output limits, interrupted settings writes, and sanitized export. Repository-wide Ruff and Black checks pass. Offscreen visual inspection covered the creation and branch/tag dialogs. The standalone Linux executable and extracted Debian payload pass the packaged smoke test outside the checkout, including their bundled credential helper. Linux benchmark: 81.6 ms first page, 27.8 ms next page, 47.3 ms layout of 5,000 rows, 5.0 MiB traced Python peak. Native desktop/provider acceptance remains explicitly open; see `RELEASE.md`.

## Current acceptance matrix

The gap-closure delivery passes **328 tests**, with **3 native Windows tests skipped** on Linux, up from the **258-test** baseline. New coverage exercises workflow boundaries, worker lifetime, real remote UI controls, first-run setup, local authentication, release tooling, and data-loss prevention. Repository-wide Ruff, Black formatting, and whitespace checks pass. Independent review reproduced and verified fixes for unsafe external-merge filenames, submodule hard reset, and Windows process ownership; native Windows execution remains pending.

| Area | Linux | Windows | macOS |
| --- | --- | --- | --- |
| Source test suite | 328 passed; 3 native Windows tests skipped locally | CI configured; current branch run pending | CI configured; current branch run pending |
| Portable application | Built and smoke-tested locally outside the checkout | Build recipe/CI only; native artifact acceptance pending | Build recipe/CI only; native artifact acceptance pending |
| Installer | Isolated-root install, same-version reinstall, packaged smoke, removal, and retained-settings check passed locally; old-to-new and clean-machine checks pending | Inno Setup recipe and native lifecycle CI gate; current branch execution and clean-machine checks pending | DMG recipe and native lifecycle CI gate; current branch execution and clean-machine checks pending |
| Real HTTPS/SSH provider authentication and platform credential storage | Pending | Pending | Pending |
| Process-tree cancellation | Local POSIX coverage exists | Native Windows descendant-process acceptance pending | Native execution pending |
| Publisher trust | No publisher signing acceptance recorded | Authenticode signing and verification pending | Developer ID signing, notarization, stapling, and verification pending |
| Dependency notices and corresponding source | Automated inventory exists; publisher audit pending | Platform-native library audit pending | Platform-native library audit pending |

Evidence commands and release gates are maintained in `RELEASE.md`. No row above implies a public release or automatic updater.


## Priorities

| Priority | Meaning |
| --- | --- |
| P1 | Complete or repair everyday workflows before calling the app a dependable daily driver. |
| P2 | Broader feature coverage expected from a complete Git desktop client. |
| P3 | Optional product expansion and polish after the core workflows are dependable. |

## P1 — Daily workflow gaps

### TD-01 — Finish partial staging and discard actions

**Current status:** Implemented for regular text changes. `StatusService.apply_hunk()` and the working-tree diff controls support hunk and selected-line stage/unstage/discard, including new/deleted files and files without a trailing newline. Unsupported binary, symlink, submodule, mode-only, and rename/copy partial actions are disabled with whole-file actions retained. Rename previews and whole-rename unstage behavior now have targeted regression coverage.

**Historical finding:** The initial review found an unconnected hunk action and no partial unstage/discard workflow. That evidence describes the pre-delivery baseline and is no longer current.

**Done when:** Users can select a specific hunk or changed lines and stage/unstage them, discard selected changes with an impact preview, and stage/unstage multiple files or all files. Verify the resulting index and working tree in real repositories, including deletions and files without a trailing newline. Disable unsupported actions explicitly until implemented. Coordinate recoverable discard with TD-11.

### TD-02 — Show the correct staged, unstaged, and new-file content

**Current status:** Implemented. Selection carries the staged/unstaged side, untracked text is previewed, and empty, binary, metadata-only, symlink, submodule, rename/copy, and conflict states have explicit explanations. Regression coverage includes files with both staged and unstaged edits plus rename, mode-only, and submodule boundaries.

**Historical finding:** The initial review found implicit side selection and no untracked preview. Those paths were replaced by explicit preview state.

**Done when:** Selection includes an explicit index/working-tree side; untracked text files have content previews; binary, empty, mode-only, symlink, and unsupported content changes have accurate explanations. Regression tests cover a file with both staged and unstaged edits and verify each view separately.

### TD-03 — Complete interrupted merge/rebase/cherry-pick/revert workflows

**Current status:** Implemented for merge, rebase, cherry-pick, and revert. Persistent Git-state detection and Continue/Skip/Abort actions survive reopening and remain visible after the last resolution is staged. Targeted UI coverage now verifies reopened Continue controls for rebase, cherry-pick, and revert as well as existing real-operation lifecycle tests. External merge supports Unicode and spaces; filenames unsafe for Git’s shell launcher are rejected before mutation with guidance to resolve manually and stage.

**Historical finding:** The initial review found start-only actions and conflict detection tied to unmerged files. The current operation service and banner supersede that behavior.

**Done when:** A persistent operation banner detects Git operation state independently of unmerged files, identifies the operation correctly, and provides appropriate continue/skip/abort controls. Preserve this state after restarting the app. Test real conflicting operations through resolution and abort, including the fully staged but unfinished state. External resolution must remain a supported path; an internal editor is TD-17.

### TD-04 — Move all potentially slow Git work off the UI thread

**Current status:** Implemented for supported workflows through `SessionJobs` and the worker pool. Clone, reads, working-tree mutations, commit operations, reference management, setup, external tools, and recovery run in background jobs; Qt state is applied on the UI thread. Slow clone/checkout/diff/commit and real slow-hook regressions cover responsiveness.

**Historical finding:** The initial review identified direct blocking calls in UI handlers. Those handlers now submit background work.

**Done when:** All potentially slow subprocess work uses a common background command layer, UI changes stay on the Qt thread, and relevant controls show busy state. Verify responsiveness during a slow clone, commit hook, checkout, and diff load.

### TD-05 — Add real progress, cancellation, and reliable timeout recovery

**Current status:** Implemented locally; native Windows acceptance remains open. Git output streams through process contexts, operation-specific timeouts replace the original short default, cancellable operations terminate process trees, and all result/submission paths release scheduler bookkeeping and restore controls. Current regressions add timeout cleanup for pipe-holding descendants and failed-submission/retry handling. Windows now owns each command and its descendants in a Job Object before execution; native tests for exited-parent timeout, cancellation, and detached-child cleanup still require Windows execution.

**Historical finding:** The initial review found exit-only output, scheduler-only cancellation, and cleanup paths that could leave controls disabled. Those implementation gaps are closed in the current source.

**Done when:** Stream output, use suitable per-operation timeouts, expose cancellation where safe, handle child processes, and restore controls on every completion path. Refresh repository state after interruption rather than implying a rollback. Include timeout, launch-failure, authentication-wait, and cancellation tests.

### TD-06 — Isolate repository sessions and serialize conflicting writes

**Current status:** Implemented. Session generations and per-channel identities reject stale results and errors, mutations are distinct, incompatible controls/navigation are gated, and repository locks cover linked worktrees. Current worker-lifecycle regressions verify completed keys are released, failed submission permits retry, and late errors cannot revive superseded requests.

**Historical finding:** The initial review found exact-key suppression without session identity or repository-wide write gating. `SessionJobs` and worker-pool lifecycle cleanup supersede that design.

**Done when:** Use immutable results plus active-session/generation checks, invalidate old callbacks on navigation, and serialize incompatible writes per repository. Read-job deduplication must not silently cancel or repeat user-requested mutations. Test switching repositories and filters while jobs are delayed, and attempting branch/stash/commit actions during a pull.

### TD-07 — Make private-repository authentication usable from the desktop

**Current status:** Desktop integration is implemented; provider/platform acceptance remains open. Git/OpenSSH askpass uses an authenticated localhost bridge while preserving configured helpers and agents. Connection Setup provides diagnostics, retry/cancel, and existing-key loading. Current local acceptance uses a real rejecting HTTP endpoint to verify bad-token retry, and a real encrypted SSH key/agent flow to verify desktop passphrase unlocking; prompt capability cleanup and redaction have additional regressions. No real hosting provider or platform keychain has been accepted.

**Historical finding:** The initial review found no desktop prompt or guided diagnostics. The askpass broker and Connection Setup replace that behavior. Key creation and provider OAuth remain outside this item.

**Done when:** Integrate platform credential helpers/askpass and SSH agents, provide actionable connection diagnostics, and support retry/cancel without storing secrets in ordinary settings or logs. Validate HTTPS and SSH with existing credentials, missing credentials, expired access, and locked keys. Hosting-provider OAuth is an optional extension under TD-26.

### TD-08 — Finish remote configuration, publish, and pull/push options

**Current status:** Implemented for documented single-destination branch transfers. Remote CRUD, destination inspection, first publish/upstream, prune, merge/rebase/fast-forward-only pulls, and pinned force-with-lease previews are exposed. Current real-UI regressions cover add/edit/remove, publish, prune, and all three pull modes. Multi-push-URL and custom-refspec editors remain outside this UI.

**Historical finding:** The initial review found backend flags without the corresponding dialogs. The remote dialogs and guarded transfer flows supersede that state.

**Done when:** Add remote CRUD with URL inspection, explicit remote/branch selection, publish-and-track for a new branch, fetch/prune options, and merge/rebase/fast-forward-only pull choices. Offer force-with-lease with a concrete impact preview; show the destination before pushing. Test first publish, fork/upstream selection, and non-fast-forward recovery.

### TD-09 — Refresh automatically after external repository changes

**Current status:** Implemented with debounced polling and application-activation refresh. Local worktree/Git metadata changes include linked-worktree paths; refresh preserves drafts, selection, and history position and does not fetch. Current activation coverage verifies draft/selection preservation and absence of network commands; model switches no longer copy commit drafts between repository view models.

**Historical finding:** The initial review found action-triggered refresh only. Repository fingerprints and activation observation replace that behavior.

**Done when:** Observe relevant worktree/Git changes with debouncing, refresh on app activation, and preserve selection, scroll position, and commit drafts. Handle linked-worktree metadata paths. Make background remote fetching a separate configurable preference rather than an automatic consequence of local refresh.

### TD-10 — Complete first-run setup and apply settings consistently

**Current status:** Implemented with an explicit restart boundary for Git-path changes. Preferences, Commit Identity, and Connection Setup remain accessible before a repository opens. Git 2.30+ validation, global/repository identity, immediate crash-hook updates, and restart guidance are implemented. Current first-run regressions cover missing Git and Git 2.29 while proving setup actions remain available.

**Historical finding:** The initial review found repository-only settings and ambiguous runtime application. Application-level setup and explicit restart messaging supersede that behavior.

**Done when:** Offer preferences from the dashboard/application menu, validate Git and supported capabilities, explain installation/configuration failures, and configure global/repository identity. Apply service-affecting settings safely or explicitly request a restart. Verify the displayed crash-report preference matches actual behavior.

### TD-11 — Provide practical recovery and accurate destructive-action guidance

**Current status:** Implemented for documented recovery actions. The Recovery browser exposes retained commits/stashes/tags, discard backups, remote URLs, and HEAD reflog. Reset and destructive reference actions retain supported data; hard reset refuses untracked/ignored content and repositories containing submodules because nested worktrees are not backed up. Current regressions cover Recovery-browser file restoration with preservation of newer edits and backup/recovery before the first commit. There is no universal undo, and restored remotes do not recreate custom/upstream configuration.

**Historical finding:** The initial review found advice-only recovery and unreliable stash wording. Persistent recovery refs/backups and the Recovery browser supersede it.

**Done when:** Add a reflog/recovery browser, retain recovery information for supported destructive actions, and offer undo only where the app can safely support it. Distinguish committed-history recovery from backups of uncommitted work. Capture stash object IDs before dropping and explain retention limits accurately. Test recovery across restart and intervening external Git changes. GitKraken's [bounded undo/redo workflow](https://help.gitkraken.com/gitkraken-desktop/undo-and-redo/) is a useful reference.

### TD-12 — Deliver installable releases and real installation checks

**Current status:** Linux artifacts are built and locally smoke-tested; native release acceptance remains pending. The repository contains standalone bundle, Debian, Inno Setup, DMG, checksum, source/wheel, and native CI recipes plus version/license/notices assets. `RELEASE.md` records the exact commands and unresolved gates. Windows/macOS execution, clean-machine lifecycle checks, publisher signing/notarization, real-provider acceptance, dependency/corresponding-source audit, and public distribution remain incomplete. There is no automatic updater.

**Historical finding:** The initial review found only source packaging and test CI. Desktop build/install recipes and Linux smoke evidence now exist, but recipes and uploaded review artifacts are not accepted releases.

**Done when:** Produce and smoke-test supported Linux, macOS, and Windows distributions, including Qt plugins, fonts, and icons; document Git installation/bundling and supported versions; provide application identity/version information, release notes, checksums, and an update path. Address platform signing/notarization where applicable. Include a top-level license file and required bundled dependency notices consistent with the declared license.

## P2 — Complete desktop-client coverage

### TD-13 — Initialize repositories and handle repository types explicitly

**Status:** Open/clone exist; init and robust repository discovery are missing.

**Evidence:** `src/gitqt/ui/dashboard_view.py` has open/add/clone/remove flows, but no initialization flow. `RepositoryValidator` in `src/gitqt/services/repositories.py` recognizes filesystem shapes, including bare repositories and `.git` files, without querying Git to validate them. A single worktree-oriented workspace is used afterward.

**Done when:** Create a repository with an initial branch and optional starter files; normalize a selected subdirectory to its repository root; resolve linked worktrees correctly; and clearly support or reject bare repositories without broken worktree actions. Test corrupt `.git` references, empty repositories, subdirectories, and bare repositories.

### TD-14 — Finish branch and tag management

**Status:** Basic local branch actions and tag creation exist; lifecycle coverage is incomplete.

**Evidence:** `BranchService` has create/checkout/delete but no rename or explicit upstream-setting methods. `Sidebar` has no remote-branch checkout action and explicitly does nothing for tag context menus. `TagService` only lists and creates tags.

**Done when:** Rename branches; create a local tracking branch from a remote branch; change upstream; delete remote branches with safeguards; inspect/checkout/delete tags; and push/delete selected remote tags. Show tag targets and annotations. Ensure checkout of a remote branch produces the intended local tracking branch rather than an accidental detached HEAD.

### TD-15 — Compare arbitrary branches, commits, and files

**Status:** Single-commit affected files/diffs exist; general comparison is missing.

**Evidence:** `HistoryView` uses single selection and `HistoryService` exposes graph and single-commit inspection, without a two-revision comparison API.

**Done when:** Choose two refs/commits and inspect changed files and per-file diffs, compare a branch with its base, compare against the working tree, and explain merge-base versus direct endpoint comparisons. Show both refs and comparison direction. GitKraken's [diff documentation](https://help.gitkraken.com/gitkraken-desktop/diff/) provides a reference for revision comparison.

### TD-16 — Add visual interactive rebase and batch commit operations

**Status:** Basic rebase and single-commit operations exist; visual history editing is missing.

**Evidence:** `CommitOperationsService.rebase_onto()` runs a plain rebase; history menus operate on one selected commit. `PRD.md` explicitly defers interactive rebase beyond MVP.

**Done when:** Preview a commit sequence and support reorder, squash/fixup, reword, and drop; support ordered multi-commit cherry-pick/revert; explain published-history effects and handle conflicts through TD-03. Define behavior for merge commits, including mainline-parent selection where appropriate. Use [GitKraken's interactive rebase workflow](https://help.gitkraken.com/gitkraken-desktop/interactive-rebase/) as a comparison, not an implementation constraint.

### TD-17 — Add an integrated conflict editor

**Status:** External merge-tool integration exists; internal conflict resolution is missing and was excluded from MVP.

**Evidence:** Conflict UI in `src/gitqt/ui/working_tree_view.py` is a banner plus external-tool launch. `DiffView` is read-only and has no base/ours/theirs/result model.

**Done when:** Provide base/current/incoming context, per-block selection, an editable result preview, unresolved-marker checks, and explicit mark-resolved/stage controls. Explain the meaning of each side during rebase. Provide clear handling or external fallback for binary, modify/delete, and rename conflicts. Depends on TD-03 and TD-04.

### TD-18 — Improve commit composition, amend, identity, and signing

**Status:** Summary/body, amend, and sign-off exist; important refinements are missing.

**Evidence:** `CommitService.commit()` requires staged changes before considering amend, preventing ordinary message-only amendment. The composer does not preload the previous message or warn about amending published commits. There is no template/co-author/signing configuration UI; author metadata is read-only. Existing Git configuration may already cause signing and hooks to run.

**Done when:** Permit message-only amend, preload the previous message, show shared-history warnings, load configured templates, add co-author trailers, and persist drafts per repository. Offer GPG/SSH signing setup/status and actionable hook/signing failures. Preserve inherited Git behavior and integrate passphrase prompts through TD-07. GitKraken documents [templates, co-authors, and amend](https://help.gitkraken.com/gitkraken-desktop/commits/) as part of commit composition.

### TD-19 — Add file history, blame, and complete history search

**Status:** Loaded commit rows can be filtered; repository-wide investigation is missing.

**Evidence:** `HistoryFilterProxyModel` filters summary/author/hash/refs in loaded rows. `HistoryService.get_graph()` accepts refs and a count, but no author/path/date/message search query. There are no file-history or blame services. Branch-filter choices are derived from refs observed in loaded history rows.

**User impact:** An older matching commit or branch can be absent until enough history is loaded; an empty filtered list does not establish that the repository has no match.

**Done when:** Search Git history independently of displayed pages, support paths/dates/authors/full hashes, show search/loading scope, and populate ref choices from the complete ref list. Add rename-aware file history and blame with navigation to the originating commit. See [GitKraken's file history and blame workflow](https://help.gitkraken.com/gitkraken-desktop/diff/).

### TD-20 — Improve diff presentation and commit-inspection edge cases

**Status:** Inline colored text hunks exist; richer review and some commit types are incomplete.

**Evidence:** `src/gitqt/ui/diff_view.py` has one nonwrapping text presentation with line-type coloring. `HistoryService.get_commit_files()` invokes `diff-tree` without `--root` or an explicit merge-parent strategy, so the initial commit has no affected files and merge review is incomplete. This is separate from TD-02's working-tree selection issue.

**Done when:** Support side-by-side and word-level diffs, whitespace/context controls, search and next-change navigation, optional syntax highlighting, and clear binary/image previews or fallback metadata. Show initial-commit files, full commit messages, and parent-aware merge diffs. Validate large files, encodings, CRLF, renames, and missing trailing newlines. [GitKraken's diff modes](https://help.gitkraken.com/gitkraken-desktop/diff/) illustrate the broader review experience.

### TD-21 — Add ignore rules, file actions, and patch exchange

**Status:** Whole-file staging and external diff exist; common file-management actions are missing.

**Evidence:** `StatusService` has no ignore-rule editing, worktree restore/discard, or patch import/export API. `src/gitqt/parsers/patch.py` builds patches for staging, not user-facing patch exchange.

**Done when:** Ignore a file/pattern through `.gitignore` or local excludes; explain ignored/tracked state; open a file in an editor or file manager; restore a file from a chosen revision; and preview/import/export patches. Define whether binary patches are supported. Keep destructive behavior consistent with TD-01 and TD-11. Patch exchange is also covered by [GitKraken's diff workflow](https://help.gitkraken.com/gitkraken-desktop/diff/).

### TD-22 — Add stash inspection and selective stash workflows

**Status:** Create/apply/pop/drop exist; inspection and selective use are missing.

**Evidence:** `src/gitqt/services/stash.py` and the sidebar provide list/action flows without a stash file/diff inspector or a stash-to-branch action.

**Done when:** Inspect stash contents before applying/dropping, stash selected paths or changes, restore selected files, and create a branch from a stash. Expose whether untracked files and staged state are included/restored. Surface apply/pop conflicts and retain recoverable stash identity; integrate with TD-03 and TD-11.

### TD-23 — Add Git worktree management

**Status:** Opening a `.git`-file directory is accepted; dedicated worktree management is missing.

**Evidence:** Repository validation accepts `.git` files, but there is no worktree service or sidebar section.

**Done when:** List/create/open/remove/prune/lock/unlock linked worktrees; show each path and branch; explain a branch already checked out elsewhere; and protect dirty worktrees from accidental removal. Coordinate shared refs and refresh with TD-06 and TD-09. GitKraken offers [worktree management](https://help.gitkraken.com/gitkraken-desktop/worktrees/) for parallel branch workflows.

### TD-24 — Add ongoing submodule management

**Status:** Recursive clone exists; lifecycle and dedicated status are missing.

**Evidence:** The only explicit submodule UI is the clone dialog's `--recurse-submodules` checkbox. No submodule service or dedicated status/navigation panel exists.

**Done when:** Detect submodules; show recorded versus checked-out commits and dirty/uninitialized state; open, initialize, update, sync, add, and remove them with clear nested-repository behavior. Avoid treating all gitlink changes as ordinary text files. Compare with [GitKraken's submodule support](https://help.gitkraken.com/gitkraken-desktop/submodules/).

### TD-25 — Add Git LFS detection and management

**Status:** No explicit LFS support; configured Git filters may work transparently.

**Evidence:** No LFS service, detection, or controls appear in the reviewed source. `PRD.md` anticipates detection in MVP and fuller support later.

**Done when:** Detect the LFS dependency and tracked patterns, distinguish pointer files from fetched content, offer install/setup guidance and track/untrack actions, and display fetch/pull/push progress/errors. Support locks where available, with a separate explicit workflow for history migration. See [GitKraken's LFS workflow](https://help.gitkraken.com/gitkraken-desktop/git-lfs/).

### TD-26 — Integrate hosting providers and pull requests

**Status:** Git transport works; provider-specific collaboration is missing and was excluded from MVP.

**Evidence:** There are no hosting-provider API services or account/PR/issue panels. Remote URLs are used as Git transport endpoints only.

**Done when:** Start with a deliberately chosen provider: securely connect an account, browse/clone repositories, open the current repo/commit in a browser, create/check out pull requests, and show review/build status. Add diffs, comments, approvals, merges, issue linking, enterprise endpoints, and additional providers incrementally. Keep core local Git usable without a provider account. [GitKraken's pull-request workflow](https://help.gitkraken.com/gitkraken-desktop/pull-requests/) is a comparison reference.

### TD-27 — Add repository tabs, workspaces, and complete session restoration

**Status:** Recent repositories and restoration of the last repository exist; workspace organization is missing.

**Evidence:** `MainWindow` in `src/gitqt/ui/main_window.py` reuses one repository view. `RecentRepositoriesService` keeps a flat list capped at 20. Existing Working Tree/History tabs are views within one repository, not repository tabs.

**Done when:** Open multiple repository tabs/windows, favorite/group/search repositories, and restore open tabs, active view, panel sizes, selections, and repository-specific commit drafts. Prevent a draft from one repository carrying into another. Show useful dashboard state such as current branch, local changes, and ahead/behind counts without blocking startup. Depends on TD-06.

### TD-28 — Make common workflows accessible from the keyboard

**Status:** Standard Qt controls and two explicit shortcuts exist; broader navigation and accessibility work remains.

**Evidence:** `MainWindow._setup_shortcuts()` defines Ctrl+O and F5 only. The custom graph/pills and dark theme have no dedicated accessibility verification in the reviewed tests. Existing diff +/- markers provide non-color information and should be retained.

**Done when:** Provide discoverable platform-appropriate shortcuts for commit, stage/unstage, search, repository switching, and operation controls; add shortcut customization and a command palette. Audit focus order, accessible names, screen-reader descriptions of graph/ref state, text scaling, contrast, and light/system themes. Test core workflows without a mouse. See [GitKraken's command palette](https://help.gitkraken.com/gitkraken-desktop/command-palette/) and [keyboard shortcuts](https://help.gitkraken.com/gitkraken-desktop/keyboard-shortcuts/).

### TD-29 — Harden performance, path handling, and workflow regression coverage

**Status:** Caches, a table model, workers, and service tests exist; scale and end-to-end guarantees remain limited.

**Evidence:** `HistoryView._load_more()` increases the total limit, reruns the history prefix, and replaces model rows rather than fetching only a new page. Status and affected-file parsing use line-delimited output rather than NUL-delimited paths. `tests/test_worker_pool.py` covers `ImmediateWorkerPool`; commit-operation tests mostly verify command construction. The test suite passes despite the disconnected partial-staging UI.

**Done when:** Establish large-repository latency/memory budgets, avoid repeated full-prefix history loading, bound caches and diff rendering, and parse arbitrary valid Git paths safely. Add focused GUI-to-real-repository tests for staged-side selection, partial staging, new-branch publish, conflict continuation, actual threaded callbacks, and recovery. Cover Unicode/tab/newline filenames, linked worktrees, empty/root/merge commits, and representative OS/Git versions. Resolve the Qt filter deprecation before updating to an incompatible release.

### TD-30 — Make logs, errors, and saved settings dependable

**Status:** An operation panel, local crash reports, and versioned settings exist; diagnostic/recovery coverage is incomplete.

**Evidence:** Git command errors include command arguments and remote failures expose stderr; there is no centralized sanitization layer. Some history failures are converted to empty lists, hiding the difference between no results and a failed query. `SettingsStore.save()` writes directly to the settings file; malformed JSON recovery is not implemented. Existing settings tests explicitly expect rejection of a non-object payload.

**Done when:** Distinguish errors from empty states, provide actionable retry/details, and offer a sanitized support bundle with bounded logs and command timing. Redact credentials embedded in URLs/output. Save settings atomically and recover from damaged settings without preventing startup. Test secrets in diagnostics, interrupted writes, and invalid configuration. Preserve local-only crash reporting.

## P3 — Optional expansion

These are product decisions rather than prerequisites for a useful local Git client.

| ID | Gap and current evidence | Completion target |
| --- | --- | --- |
| TD-31 | Terminal/editor integration: external diff/merge exists, but no general terminal/editor launcher or embedded terminal. | Open the active repository/worktree in a configured terminal/editor; consider an embedded terminal only if valuable. GitKraken has a [built-in terminal](https://help.gitkraken.com/gitkraken-desktop/terminal/). |
| TD-32 | Advanced repository workflows: no dedicated bisect, sparse-checkout, shallow-clone/deepen, or Git-flow UI. | Select features from demonstrated user demand; provide previews and recovery for workflows the app takes ownership of. These are recommendations for GitQt, not claims that every competitor offers every item. |
| TD-33 | Localization and help: UI strings are embedded in Python widgets, and the README is development-oriented. | Introduce translatable strings, user-facing workflow documentation, searchable help, and contextual explanations of Git states. |
| TD-34 | Extensibility and assisted workflows: no plugin API, custom-action framework, or AI assistance. | Consider stable extension points and opt-in assistance only after core workflows are complete. Respect `PRD.md`'s plugin-marketplace/IDE non-goals; provider accounts and AI must not become prerequisites for local Git. |

## Suggested delivery sequence

1. **Restore confidence in visible controls — delivered for supported workflows:** TD-01, TD-02, TD-03, and the TD-05 stuck-button failure path. See the implementation-progress table for coverage and remaining boundaries.
2. **Make operations dependable — implementation delivered; platform/provider acceptance pending:** TD-04 through TD-10, with session isolation implemented first and TD-11 recovery added before expanding destructive actions. See the delivery-step-2 table for supported boundaries and remaining acceptance checks.
3. **Ship an installable daily driver — workflows and Linux build delivered; release acceptance pending:** TD-12, TD-13, TD-14, the amend corrections in TD-18, and foundational work from TD-29/TD-30.
4. **Expand review and history editing:** TD-15 through TD-22, then improve multi-repository work and accessibility with TD-27/TD-28.
5. **Choose audience-specific breadth:** Worktrees, submodules, LFS, and provider collaboration (TD-23 through TD-26), then selected P3 items.

For each completed item, verify the user can finish the workflow through the actual UI, include appropriate regression coverage, update the changelog, and revise this backlog. A service method alone is not completion of a desktop feature.
