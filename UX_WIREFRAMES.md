# UX Wireframes

## Purpose
This document defines the initial screen structure and interaction model for `GitQt`, with emphasis on the repository dashboard, repository workspace, and history view. The wireframes are intentionally low fidelity so implementation can focus on workflow and information hierarchy before visual polish.

## Design Goals
- feel familiar to SourceTree users without cloning its UI exactly
- work well on Linux first, while remaining native-feeling on macOS and Windows
- prioritize repository visibility, diff clarity, and safe Git actions
- support progressive disclosure for advanced operations
- stay usable on laptop screens without hiding critical information

## UX Principles
- Show repository state first, configuration second.
- Put the next likely action near the current context.
- Keep destructive actions visible but not easy to trigger accidentally.
- Prefer persistent panes over modal flows for inspection tasks.
- Make loading, invalid, and conflict states explicit.

## Primary Navigation Model
The app should have two primary modes:

- dashboard mode for choosing or managing repositories
- repository mode for working inside a selected repository

Recommended top-level structure:
- a single main window
- left-side navigation and repository context controls
- a central content area that swaps between working tree, history, and related panels
- a bottom operation log that can be collapsed

## Screen 1: Dashboard
### Purpose
Help users open, add, remove, and resume work on repositories quickly.

### Layout Wireframe
```text
+----------------------------------------------------------------------------------+
| GitQt                                                           [Settings] [Help] |
+----------------------------------------------------------------------------------+
| Repositories                                                  Git: /usr/bin/git  |
|----------------------------------------------------------------------------------|
| [ Open Repository... ] [ Add Existing... ] [ Clone... ] [ Remove Selected ]     |
|                                                                                  |
| Status: No repositories yet. Add or open a repository to get started.           |
|                                                                                  |
| +------------------------------------------------------------------------------+ |
| | Recent Repositories                                                          | |
| |------------------------------------------------------------------------------| |
| | project-alpha                                            last opened         | |
| | infra-tools                                                                  | |
| | website                                                                      | |
| | old-repo                                                   invalid           | |
| +------------------------------------------------------------------------------+ |
|                                                                                  |
| Empty state help: Drag a repository folder here, or clone a new one.            |
+----------------------------------------------------------------------------------+
| Operation Log (collapsed by default)                                             |
+----------------------------------------------------------------------------------+
```

### Key Behaviors
- Double-clicking a repository opens it.
- Invalid repositories remain visible but clearly marked.
- `Open Repository...` updates the last-opened repository.
- `Add Existing...` adds a repository without switching immediately if that distinction remains useful.
- Dashboard actions should remain keyboard accessible.

### Dashboard States
#### Empty
- show a helpful starter message
- promote `Open`, `Add`, and `Clone`

#### Ready
- show the recent repository list and last-opened indicator
- keep primary actions visible above the list

#### Invalid
- highlight invalid entries inline
- offer `Remove` and `Locate Again` in a later iteration

## Screen 2: Repository Workspace
### Purpose
Provide the day-to-day Git working area for status, staging, commit, history, and repository inspection.

### Layout Wireframe
```text
+------------------------------------------------------------------------------------------------+
| GitQt - project-alpha                                        branch: main   ahead 1 / behind 0 |
+------------------------------------------------------------------------------------------------+
| Sidebar                      | Main Content                               | Inspector            |
|-----------------------------|---------------------------------------------|----------------------|
| Repositories                | Tabs: [Working Tree] [History] [Branches]  | Selection Details    |
| - project-alpha             |---------------------------------------------|----------------------|
| - infra-tools               | File Status                                 | Commit / file info   |
|                             | [Search files.........................]      |                      |
| Branches                    | [All] [Staged] [Unstaged] [Untracked]       | For file selection:  |
| - main                      |                                             | diff summary, path,  |
| - feature/rebase-ui         | M  src/gitqt/app/dashboard.py               | staging actions      |
| - origin/main               | A  src/gitqt/services/status_service.py     |                      |
|                             | ?? docs/notes.md                            | For commit selection:|
| Tags                        |                                             | author, date, refs   |
| - v0.1.0                    |---------------------------------------------|                      |
|                             | Diff / details area                         |                      |
| Stashes                     | @@ ... inline diff content ...              |                      |
| - WIP: repo status          | + new line                                 |                      |
|                             | - old line                                 |                      |
| Remotes                     |                                             |                      |
| - origin                    |---------------------------------------------|                      |
|                             | Commit Composer                              |                      |
|                             | Summary: [................................] |                      |
|                             | Body:                                        |                      |
|                             | [..........................................] |                      |
|                             | [ Stage All ] [ Unstage All ] [ Commit ]    |                      |
+------------------------------------------------------------------------------------------------+
| Operation Log  Fetch completed successfully.                                      [Clear]      |
+------------------------------------------------------------------------------------------------+
```

### Information Hierarchy
- Left sidebar: repository navigation and Git objects
- Main content: the active task surface
- Right inspector: details for the current selection
- Bottom log: background job and Git output visibility

### Core Interaction Rules
- Selecting a file updates the diff panel and inspector.
- Selecting a commit in history updates commit details and changed files.
- Risky actions surface confirmation with plain-language impact.
- Commit actions remain disabled until validation passes.
- Background jobs show progress without freezing the workspace.

## Screen 3: Working Tree Focus
### Purpose
Optimize for the most common workflow: inspect changes, stage intentionally, commit, and push.

### Layout Wireframe
```text
+------------------------------------------------------------------------------------------------+
| Working Tree                                                                 [Refresh] [Fetch] |
+------------------------------------------------------------------------------------------------+
| Files                                           | Diff                                           |
|------------------------------------------------|------------------------------------------------|
| [Search]                                       | src/gitqt/app/dashboard.py                     |
|                                                |------------------------------------------------|
| > Staged (2)                                   | @@ -29,6 +29,10 @@                             |
|   M src/gitqt/app/dashboard.py                 |  state = self._repositories.list_recent()      |
|   A src/gitqt/services/status_service.py       | +show invalid repo count                       |
|                                                |                                                |
| > Unstaged (3)                                 | [Stage Hunk] [Discard Hunk]                    |
|   M src/gitqt/ui/main_window.py                |                                                |
|   ?? UX_WIREFRAMES.md                          |                                                |
|   ?? TECHNICAL_ARCHITECTURE.md                 |                                                |
|                                                |                                                |
| [Stage Selected] [Unstage Selected]            |                                                |
+------------------------------------------------------------------------------------------------+
| Commit                                                                                         |
| Summary [.................................................................]                     |
| Body    [.................................................................]                     |
| [Amend] [Sign Off] [Commit] [Commit and Push later phase]                                      |
+------------------------------------------------------------------------------------------------+
```

### Working Tree Interaction Notes
- File grouping should default to staged versus unstaged because it maps to user intent.
- Search filters should apply instantly without losing expanded group state.
- Partial staging controls should appear near diff hunks, not only in a toolbar.
- Binary files should show a safe placeholder instead of a broken text diff.

## Screen 4: History View
### Purpose
Help users understand branch shape, inspect commit ancestry, compare changes, and prepare actions like checkout, cherry-pick, or revert in later phases.

### Layout Wireframe
```text
+------------------------------------------------------------------------------------------------+
| History                                                               [Search commits........] |
+------------------------------------------------------------------------------------------------+
| Graph / Commit List                              | Commit Details                               |
|--------------------------------------------------|----------------------------------------------|
| * 9f3a2d1  Add dashboard view model              | Commit: 9f3a2d1                              |
| | Author: GitQt contributors                     | Author: GitQt contributors                   |
| | Date:   2026-04-13                             | Date:   2026-04-13                           |
| |                                               | Refs: main, origin/main                      |
| * 8dd77ab  Add repository validation service     |                                              |
| |\                                              | Message:                                     |
| | * b712ef0  Spike recent repo persistence       | Add dashboard view model                     |
| |/                                              |                                              |
| * 4c2b009  Bootstrap settings and git locator    | Files changed:                               |
|                                                  | - src/gitqt/app/dashboard.py                 |
|                                                  | - tests/test_dashboard.py                    |
|                                                  |                                              |
|                                                  | [Checkout Commit] [Create Branch]            |
+------------------------------------------------------------------------------------------------+
| Diff For Selected Commit                                                                  [<>] |
+------------------------------------------------------------------------------------------------+
```

### History Behavior
- Search should filter by commit message, author, hash, and file path over time.
- The selected commit should remain visible as filters or branch selections change when possible.
- Branch labels and HEAD markers should be visually distinct.
- The graph should prioritize readability over reproducing every CLI graph character exactly.

## Screen 5: Branch and Reference Sidebar
### Purpose
Give persistent access to branches, tags, stashes, and remotes without forcing a separate management screen for every simple operation.

### Sidebar Wireframe
```text
Branches
  Local
    * main
      feature/history-graph
  Remote
      origin/main
      origin/feature/history-graph

Tags
      v0.1.0
      v0.2.0-beta

Stashes
      stash@{0} WIP on main
      stash@{1} worktree cleanup

Remotes
      origin
      upstream
```

### Sidebar Behaviors
- Context menus should host less-common actions.
- Double-click on a branch should checkout when safe.
- Right-click on a stash should offer apply, pop, and drop with confirmation.
- Remote nodes should expose fetch and push actions.

## Screen 6: Operation Log
### Purpose
Keep Git activity visible and debuggable without forcing users into a terminal.

### Layout Wireframe
```text
+----------------------------------------------------------------------------------+
| Operation Log                                                     [Clear] [Hide] |
|----------------------------------------------------------------------------------|
| 10:42:15 INFO  Fetch origin started                                              |
| 10:42:16 INFO  Fetch origin completed in 1.2s                                    |
| 10:43:10 WARN  Push rejected: non-fast-forward                                   |
| 10:43:10 INFO  Suggestion: pull or rebase before retrying                        |
| 10:44:01 ERROR Authentication failed for origin                                  |
+----------------------------------------------------------------------------------+
```

### Log Rules
- Log entries should summarize the operation in plain language first.
- Expandable detail can include raw Git stderr where helpful.
- The log should not be modal and should remain available across screens.

## Dialog Wireframes
## Clone Repository
```text
+--------------------------------------------------------------+
| Clone Repository                                             |
|--------------------------------------------------------------|
| URL            [ git@github.com:org/repo.git              ]  |
| Destination    [ /home/user/dev/repo                      ]  |
| Name           [ repo                                      ] |
| Branch         [ default                                   ] |
| [ ] Recurse submodules                                      |
|                                                              |
|                               [Cancel] [Clone Repository]    |
+--------------------------------------------------------------+
```

## Create Branch
```text
+--------------------------------------------------------------+
| Create Branch                                                |
|--------------------------------------------------------------|
| Branch name     [ feature/new-panel                        ] |
| Start point     [ main                                     ] |
| [ ] Checkout immediately                                     |
|                                                              |
|                                   [Cancel] [Create Branch]   |
+--------------------------------------------------------------+
```

## Dangerous Confirmation
```text
+--------------------------------------------------------------+
| Delete Branch                                                |
|--------------------------------------------------------------|
| Delete local branch `feature/old-spike`?                     |
|                                                              |
| This branch has not been merged into `main`.                 |
| Deleting it removes the branch reference but not commits     |
| that are still reachable elsewhere.                          |
|                                                              |
|                         [Cancel] [Delete Branch]             |
+--------------------------------------------------------------+
```

## Empty, Loading, and Error States
### Empty Repository State
- show a prompt to make the first commit or fetch remote history
- clarify whether the repository has no commits or only no current changes

### Loading State
- show progress inline near the active panel
- keep prior content visible if it reduces flicker and confusion

### Error State
- show a concise explanation and a recovery action
- place raw Git detail in an expandable area or the operation log

## Interaction Details
## Keyboard Support
- `Ctrl+O` or platform equivalent for open repository
- `Ctrl+Shift+O` for add existing repository if retained
- `Ctrl+Enter` for commit when the commit form is valid
- `F5` or equivalent for refresh
- `Ctrl+P` later for command palette or quick repository switcher

## Context Menus
Context menus should exist for:
- repository list items
- branch entries
- stash entries
- commit entries
- file status entries

They should expose secondary actions without overcrowding the primary layout.

## Responsive Behavior
### Minimum Comfortable Layout
Target a usable desktop layout around 1280px width.

### Narrower Widths
- collapse the right inspector first
- allow the bottom log to auto-collapse
- keep the file list and diff area available before hiding the sidebar entirely

## Visual Design Notes
- Use a restrained desktop look with native-feeling controls.
- Rely on spacing and grouping before adding heavy chrome.
- Diff colors must remain readable in light and dark themes.
- Graph colors must not be the only distinction between refs or states.

## Accessibility Notes
- every action available by toolbar should also be reachable by keyboard or menus
- focus order should follow the left-to-right inspection flow
- status and error messaging should not rely only on color
- row selections should remain obvious in high-contrast themes

## MVP Implementation Guidance
### Phase 1
- keep the current dashboard shell simple but align it toward the dashboard wireframe
- add top-level navigation placeholders for repository mode early

### Phase 2
- build the working tree screen first because it supports the most frequent workflow
- add the history view once repository shell navigation exists

### Phase 3
- add richer dialogs and operation log polish
- defer advanced view customization until core workflows feel stable

## Acceptance Notes
These wireframes are intentionally schematic. They should be treated as:
- layout and workflow guidance for implementation sessions
- a shared reference for future visual design
- a source for UI acceptance criteria when building repository and history screens
