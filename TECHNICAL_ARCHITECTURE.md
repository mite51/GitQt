# Technical Architecture

## Purpose
This document translates the product direction in `PRD.md` into an implementation-oriented architecture for `GitQt`, an open source Git desktop client built with Python and Qt.

It is intended to:
- define the main runtime layers and their responsibilities
- align new code with the current package structure
- establish boundaries that keep Git behavior testable and UI code maintainable
- guide implementation sessions from the current dashboard foundation toward the full MVP

## Current Baseline
The project already includes a first-pass foundation:

- application bootstrap and service wiring in `src/gitqt/app/bootstrap.py`
- dashboard view-model logic in `src/gitqt/app/dashboard.py`
- a Qt main window shell in `src/gitqt/ui/main_window.py`
- Git executable discovery in `src/gitqt/services/git.py`
- settings persistence in `src/gitqt/services/settings.py`
- recent repository management in `src/gitqt/services/repositories.py`
- initial unit tests for bootstrap, settings, Git discovery, repositories, and dashboard behavior

This architecture extends that baseline rather than replacing it.

## Architecture Goals
- Keep Git operations and parsing independent from Qt widgets.
- Preserve a responsive UI by pushing expensive work off the main thread.
- Prefer thin widgets and richer application and service layers.
- Keep repository state explicit and serializable where possible.
- Make new behavior easy to test with `pytest` and temporary Git repositories.
- Support incremental delivery sessions without large rewrites between milestones.

## Architectural Principles
### 1. Clear Layering
Each layer should depend inward:

- `ui` depends on `app`
- `app` depends on `services` and domain models
- `services` depend on Git subprocesses, persistence, and pure parsing helpers

UI code should not call raw Git subprocesses directly.

### 2. Explicit State
Views should render from state objects and action results rather than ad hoc widget mutations spread across the codebase.

### 3. Async by Default for Repository Work
Anything that can block on disk, process execution, or large parsing should run outside the Qt main thread.

### 4. Realistic Tests Over Heavy Mocks
Prefer temporary repositories plus real Git commands for service tests. Use mocks mainly for OS integration boundaries and error injection.

### 5. Evolve by Vertical Slices
Each implementation session should add one coherent slice across service, app, UI, and tests rather than building all backend pieces first.

## Recommended Stack
- Python `3.12+`
- `PySide6` for Qt bindings
- Qt Widgets for MVP UI delivery
- `pytest` for unit and service tests
- `pytest-qt` for targeted widget and signal/slot tests
- Git CLI subprocess integration for MVP and early v1
- `ruff` and `black` for linting and formatting

## Top-Level Runtime Design
At runtime, the application should be organized into four layers.

### UI Layer
Owns Qt widgets, dialogs, list/tree models, and interaction wiring.

Responsibilities:
- render view-model state
- collect user input
- forward actions to application-layer controllers or view models
- show progress, warnings, and confirmations

Non-responsibilities:
- parsing Git output
- persisting settings directly
- deciding business rules for Git workflows

### Application Layer
Owns view models, workflow orchestration, background job coordination, and action policies.

Responsibilities:
- translate user actions into service calls
- aggregate service outputs into UI-ready state
- coordinate refreshes after Git operations
- centralize enablement rules for actions
- interpret errors into user-facing messages

Examples:
- dashboard state and actions
- repository screen state
- branch manager state
- commit composer validation
- operation log model

### Service Layer
Owns Git execution, repository inspection, parsing, settings, logging, and local persistence.

Responsibilities:
- run Git commands safely
- normalize and parse command output
- validate repositories
- persist settings and local caches
- expose typed results and typed errors

Examples:
- Git executable discovery
- repository status service
- history service
- branch service
- remote service
- settings store
- logging configuration

### Domain Layer
Owns shared data structures and value types used across the application.

Examples:
- repository identity and validation results
- status entries and diff hunks
- commit graph nodes
- refs, branches, tags, and stash entries
- operation progress and error categories

The domain layer can initially live alongside `app` and `services`, but should become its own package once model count grows.

## Proposed Package Layout
This layout preserves the current code structure while making room for new features:

```text
src/gitqt/
  __init__.py
  main.py
  app/
    bootstrap.py
    dashboard.py
    repository.py
    history.py
    commit.py
    operations.py
  domain/
    repositories.py
    status.py
    commits.py
    refs.py
    operations.py
  services/
    git.py
    git_runner.py
    git_parsers/
      status.py
      diff.py
      history.py
      refs.py
    repositories.py
    status_service.py
    history_service.py
    branch_service.py
    remote_service.py
    stash_service.py
    settings.py
    logging.py
  ui/
    main_window.py
    dashboard/
      view.py
      widgets.py
    repository/
      view.py
      diff_panel.py
      history_panel.py
      commit_panel.py
    dialogs/
      clone_dialog.py
      branch_dialog.py
      preferences_dialog.py
```

The exact split can evolve, but the key boundary is that parsing and Git command execution stay out of `ui`.

## Core Data Flows
### Application Startup
1. `gitqt.main` creates the Qt application.
2. `app.bootstrap.build_services()` resolves config paths, logging, settings, and Git availability.
3. `MainWindow` receives the service container.
4. `MainWindow` constructs the initial dashboard view model and renders the last known repository context if available.

### Dashboard Repository Open Flow
1. User chooses a path from a dialog or double-clicks a recent repository.
2. UI forwards the request to a dashboard or repository launcher view model.
3. The repositories service validates and persists the repository selection.
4. The application layer creates repository screen state and triggers initial background refresh jobs.
5. The UI swaps from dashboard mode into repository mode.

### Status Refresh Flow
1. Repository screen requests a refresh.
2. Application layer enqueues a background job.
3. Service layer runs Git commands and parses output into typed domain objects.
4. Results are published back to the view model on the UI thread.
5. UI models update file lists, diff panes, action enablement, and operation log entries.

### Write Operation Flow
Example operations: stage, unstage, commit, checkout branch, fetch, push.

1. UI gathers input and confirms risky actions.
2. Application layer validates prerequisites.
3. Background worker executes the service-layer command.
4. Service layer returns a success result or typed failure.
5. Application layer records operation output, triggers follow-up refreshes, and emits user-facing status.
6. UI reflects the updated repository state.

## Service Design
## Git Command Execution
For MVP, the project should use the Git CLI through a dedicated runner abstraction.

Recommended shape:
- `GitLocator` continues to discover the executable path
- `GitRunner` becomes the single place that invokes subprocesses
- feature services build commands through the runner rather than calling `subprocess` directly

The runner should:
- accept repository working directory, arguments, environment overrides, and timeouts
- capture `stdout`, `stderr`, exit code, and elapsed time
- redact sensitive output where needed before logging
- raise or return typed errors for process launch failure, timeout, and Git exit failure

### Why a Runner Matters
- consistent logging
- easier tests
- centralized timeout and environment handling
- fewer subprocess details leaking into feature code

## Parsing Strategy
Git output parsing should be centralized in narrowly scoped parser modules rather than embedded in widgets or large service methods.

Examples:
- status parser for porcelain output
- diff parser for hunks and line metadata
- ref parser for branches, tags, and remotes
- history parser for commit graph rows and commit metadata

Parser requirements:
- typed outputs
- compatibility with expected Git versions on Linux, macOS, and Windows
- tests with representative command output samples

## Feature Services
Recommended service split:

- `RepositoryService`: repository metadata, root detection, and validation helpers
- `StatusService`: working tree status, staged state, diff summaries
- `CommitService`: commit validation and execution
- `BranchService`: list, create, checkout, delete, ahead/behind
- `HistoryService`: commit list, graph projection, comparisons
- `RemoteService`: fetch, pull, push, remote list, auth-related failures
- `StashService`: create, apply, pop, drop
- `TagService`: create and inspect tags
- `ConflictService`: detect merge or rebase conflict states

These services should return typed result objects rather than raw command output.

## State Management Approach
The current `DashboardViewModel` is a good foundation and should become the pattern for future screens.

Recommended pattern:
- immutable or replace-on-update state dataclasses
- simple action methods on view models
- explicit `ActionResult` or `OperationResult` objects
- one state owner per major screen or panel

Benefits:
- easier unit testing
- more predictable widget updates
- reduced hidden coupling between UI controls

For larger screens, split state by responsibility:
- repository shell state
- file status panel state
- diff panel state
- history panel state
- commit composer state
- operation log state

## Concurrency Model
Qt must stay responsive while Git commands run in the background.

Recommended MVP approach:
- use `QThreadPool` with worker wrappers for long-running tasks
- deliver results back through signals or queued callbacks
- ensure only UI-thread code touches widgets

The realization of this guidance is `src/gitqt/services/worker_pool.py`:

- `WorkerPool` owns a `QThreadPool` and wraps `WorkerScheduler` so submitted
  work is scheduled by a stable key (e.g. `history::<path>`), executed on a
  background thread, and the result is emitted back on the UI thread via a
  `QueuedConnection` signal. Superseded / cancelled jobs are dropped and never
  call back; exceptions are wrapped in `WorkerError` so the UI can log them
  without crashing the worker thread.
- `ImmediateWorkerPool` runs submitted work inline and is used by tests so
  view-model tests keep running synchronously without spinning up a Qt event
  loop. `AppServices.worker_pool` is created at bootstrap and owned by the UI.
- Read-only refreshes (branch snapshot, sidebar branches, references, remote
  ops, history graph) and remote write actions (fetch/pull/push) in
  `RepositoryView` submit through the pool; the scheduler's key-based
  coalescing means rapid clicks are deduplicated automatically.

Guidelines:
- do not block the UI thread on repository refresh or remote operations
- coalesce repeated refresh requests when possible
- prevent stale background jobs from overwriting newer state
- support cancellation for safe operations where feasible

Potential later refinement:
- introduce a job manager that tracks operation IDs, progress, and cancellation tokens

## Persistence and Local Data
### Settings
Current settings are persisted as JSON via `SettingsStore`. Continue this approach for MVP.

Short-term settings fields:
- recent repositories
- last opened repository
- configured Git path
- diff view preferences
- external tool paths
- window layout and panel visibility

Longer-term settings concerns:
- versioned settings migrations
- OS-specific path normalization
- import and export of preferences if needed

### Caches
Avoid persistent caches until they solve a real performance problem. For early sessions, prefer in-memory caches for:
- recent history graph projections
- diff rendering artifacts
- repository refresh snapshots

If persistent caches are introduced later, keep them disposable and derived from repository state.

## Logging and Diagnostics
The current logger setup in `services/logging.py` should evolve into a small diagnostics system.

Requirements:
- keep structured file logging
- include operation IDs for longer workflows
- log command intent and elapsed time
- avoid logging secrets or raw credentials
- surface actionable failures in the operation log UI

Useful categories:
- app startup
- settings I/O
- Git discovery
- repository validation
- Git command execution
- parser failures
- UI action failures

## Error Handling Model
Prefer typed, user-meaningful errors instead of propagating raw exceptions across the UI.

Suggested categories:
- configuration error
- repository validation error
- Git unavailable
- command failed
- authentication failed
- merge conflict state
- timeout or cancellation
- unsupported operation

The application layer should map these into:
- inline validation messages
- status banners
- dialog warnings
- operation log entries

## UI Architecture Notes
For MVP, use Qt Widgets rather than QML.

Why:
- better fit for dense desktop productivity layouts
- easier incremental adoption with Python-first code
- straightforward testing with `pytest-qt`

Widget guidance:
- keep widget classes mostly responsible for layout and signal wiring
- move enablement logic and complex branching into view models
- avoid embedding command parsing in widgets

## Testing Architecture
The test suite should mirror the layered design.

### Test Types
- parser tests for Git output normalization
- service tests using temporary repositories and real Git commands
- application-layer tests for view models and workflow logic
- targeted widget tests for critical interactions

### Current and Planned Layout
```text
tests/
  test_bootstrap.py
  test_settings.py
  test_git.py
  test_repositories.py
  test_dashboard.py
  test_status_service.py
  test_commit_service.py
  test_branch_service.py
  test_history_service.py
  test_repository_view_model.py
  test_commit_view_model.py
```

### Test Rules
- every feature session adds tests in the same change set as the feature
- previously passing tests stay green before new sessions proceed
- bug fixes add regression tests when practical
- parser fixtures stay small and easy to audit

## Packaging and Distribution
Packaging should remain a first-class architecture concern because cross-platform delivery is part of the product goal.

Targets:
- Linux: start with `AppImage`, evaluate `Flatpak` soon after
- macOS: signed app bundle later in the roadmap
- Windows: installer or packaged executable bundle

Application code should avoid assumptions about:
- shell path separators
- case sensitivity
- bundled Git paths
- Linux-only config directories

## Security Considerations
- do not store raw credentials in app-managed config files
- rely on Git and OS credential helpers where possible
- sanitize logs that may contain remote URLs or command output
- validate paths before passing them to subprocess execution
- avoid shell invocation when direct argument execution is possible

## Evolution by Implementation Session
### Sessions 0-1
Keep the current bootstrap, settings, Git locator, and dashboard foundation stable. Extract shared types only when a second consumer appears.

### Sessions 2-4
Add status, diff, staging, and commit services. Introduce the first repository screen view model and background job execution path.

### Sessions 5-8
Expand into branch, history, remotes, stash, tags, and conflict-state workflows. Add an operation log model and more explicit error categories.

### Sessions 9-10
Focus on responsiveness, cache policy, UI polish, packaging, and settings migration support.

## Deferred Decisions
These should remain open until the first few sessions produce real usage feedback:
- whether libgit2 bindings are worth adopting for selected operations
- whether history graph rendering needs a dedicated scene or custom model
- whether plugin support needs early extension points
- whether a command bus or event system is necessary beyond explicit view-model calls

## Decision Summary
Recommended architectural decisions for the current phase:

- Use `PySide6` and Qt Widgets for MVP.
- Keep Git CLI integration behind a dedicated runner abstraction.
- Add feature services instead of growing a single large repository service.
- Use view-model state objects as the UI contract.
- Use `QThreadPool` or equivalent for background work.
- Prefer temporary real repositories in tests over broad mocking.

## Immediate Next Steps
1. Introduce a `GitRunner` abstraction and typed command results.
2. Create domain models for repository status, refs, commits, and operation errors.
3. Add a repository screen view model separate from the dashboard.
4. Implement background job execution for status refresh.
5. Start parser modules for status and diff output before building the working tree UI.
