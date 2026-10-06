# Product Requirements Document

## Product Name
Working name: `GitQt`

## Document Status
Draft v0.1

## Summary
`GitQt` is an open source, cross-platform Git desktop client inspired by SourceTree, built with Python and Qt. It should deliver a polished graphical Git experience for developers who want a fast, visual, local-first alternative that works well on Linux while also supporting macOS and Windows.

The product should make common Git workflows approachable without hiding important Git concepts. It should serve both developers who prefer a GUI for everyday work and advanced users who occasionally need deeper repository inspection.

## Problem Statement
Existing Git desktop clients have several gaps:

- SourceTree is not open source.
- SourceTree does not support Linux.
- Many existing Git GUIs are either too minimal, too opinionated, or outdated in UX.
- Some cross-platform clients are built on heavier stacks that feel less native or consume more resources than necessary.
- Advanced Git features are often available only through the terminal, creating a steep learning curve for users who prefer visual workflows.

There is an opportunity to build a modern, open source Git client that combines the usability of SourceTree with strong Linux support, clean architecture, and a sustainable community-driven roadmap.

## Vision
Build the best open source Git desktop client for Linux first, without sacrificing quality on macOS and Windows.

## Goals
- Deliver a polished desktop Git client for Linux, macOS, and Windows.
- Match or exceed SourceTree for common day-to-day workflows.
- Provide a clear visual model of repository state, branches, commits, staging, remotes, and history.
- Keep the application responsive on large repositories.
- Make advanced Git operations safer through previews, confirmations, and recoverability cues.
- Build on an open, extensible architecture that supports community contribution and future plugins.

## Non-Goals
- Replacing the full Git CLI for every edge case in the first release.
- Building a cloud-hosted Git platform.
- Implementing full IDE features such as code editing, debugging, or merge conflict resolution at IDE scale.
- Supporting every DVCS beyond Git in the initial roadmap.
- Shipping a plugin marketplace in MVP.

## Target Users
- Linux developers who want a first-class Git GUI.
- Cross-platform developers working across Linux, macOS, and Windows teams.
- Developers familiar with SourceTree who want an open source equivalent.
- Intermediate Git users who want more visibility and safety than the CLI provides.
- Advanced Git users who want a fast visual companion to the terminal.

## Primary Use Cases
- Clone, open, and browse repositories.
- View working tree changes and selectively stage hunks or lines.
- Create, switch, compare, rename, and delete branches.
- Commit staged changes with validation and templates.
- Pull, fetch, push, and inspect remotes.
- Review commit history and visualize branch structure.
- Resolve common merge and rebase workflows with guided UX.
- Stash, cherry-pick, reset, revert, and tag through safe UI flows.
- Compare branches, commits, and files with strong diff tooling.
- Manage multiple repositories in a single workspace dashboard.

## Product Principles
- Git-native: expose real Git concepts clearly instead of inventing abstractions.
- Safe by default: destructive actions require clarity and confirmation.
- Fast and local: repository operations should feel immediate and not depend on cloud services.
- Cross-platform, Linux-first: every core workflow must work well on Linux from day one.
- Progressive disclosure: simple flows for common tasks, advanced options when needed.
- Open by design: architecture, docs, and contribution model should support community growth.

## Success Metrics
### Product Metrics
- Users can complete the top 10 daily Git workflows without opening a terminal.
- First-run clone or open flow completed in under 2 minutes for new users.
- Time to stage, commit, and push a small change is under 60 seconds for a familiar user.
- Crash-free sessions above 99.5%.
- Large repository interactions remain responsive for target repo sizes.

### Community Metrics
- External contributors can set up the dev environment in under 30 minutes.
- Clear issue triage and release process established by the first public beta.
- Growing monthly active contributors and issue resolution throughput after launch.

## Assumptions
- Python and Qt are acceptable for performance if repository operations are carefully isolated from the UI thread.
- Git CLI will be the primary backend in early versions, with selective lower-level integrations if needed later.
- Users value transparency and safety more than abstracting away all Git complexity.
- A strong Linux experience can become a key differentiator without limiting cross-platform adoption.

## Scope
## MVP Scope
The MVP should cover the most common local and remote Git workflows for individual developers.

### MVP Features
- Repository dashboard
- Clone repository
- Open existing repository
- Working tree status view
- File diff viewer
- Stage and unstage by file, hunk, and line where feasible
- Commit creation with author info and commit message validation
- Branch list and branch checkout
- Branch creation and deletion
- Commit history graph
- Fetch, pull, and push
- Remote list and remote configuration view
- Stash create, apply, pop, and drop
- Tag create and checkout
- Basic conflict detection with launch-to-external-tool support
- Repository search and filtering
- App preferences and Git executable detection

### MVP Exclusions
- Interactive rebase UI
- Built-in credential manager
- Submodule management beyond visibility and status
- Git LFS management UI beyond detection
- Built-in hosting provider workflows such as pull request creation
- Plugin SDK
- Full merge conflict editor

## Post-MVP / v1 Features
- Interactive rebase and squash UI
- Cherry-pick and revert workflows
- Advanced reset and restore workflows with safety rails
- Shelving or patch export/import workflows
- Submodule management
- Git LFS support
- Credential helper integration
- Hosted provider integrations for GitHub, GitLab, and Bitbucket
- Pull request and issue linking
- External diff and merge tool configuration
- Saved workspaces, tabs, and session restore
- Keyboard shortcut customization
- Theming improvements and accessibility enhancements
- Plugin API for custom views or actions

## User Experience Requirements
## Core UX Expectations
- The app should launch quickly and restore the last active context when appropriate.
- Repository status should refresh automatically after operations without confusing flicker.
- Long-running operations must show progress, logs, and cancellation where safe.
- Dangerous operations must include plain-language explanations of impact.
- The UI must stay responsive during background Git operations.
- Empty states should help users understand what to do next.

## Key Screens
- Repository dashboard
- Repository view
- History graph view
- Diff and file status panel
- Branch and tag manager
- Stash manager
- Operation log / console view
- Preferences and repository settings

## Repository View Layout
A SourceTree-like layout is desirable, adapted for Qt:

- Left sidebar: repositories, branches, tags, stashes, remotes
- Center panel: history graph or working tree content depending on selected context
- Right panel: diff details, commit details, or action-specific inspector
- Bottom panel: operation log, errors, background job output

The layout should support panel resizing, panel show/hide, and sensible defaults for smaller screens.

## Functional Requirements
## Repository Management
- Users can add, remove, favorite, and group repositories.
- Users can clone via HTTPS or SSH URL.
- Users can open repositories located on local disk.
- The app must detect invalid or non-Git directories gracefully.

## Working Tree and Staging
- Users can view modified, added, deleted, renamed, copied, and untracked files.
- Users can stage and unstage files.
- Users should be able to stage partial changes where supported.
- Diff views must support side-by-side and inline modes in later releases; MVP may ship inline first.
- Binary files should display metadata and safe fallback messaging.

## Commit Workflow
- Users can write commit messages with summary and body.
- Users can amend the last commit in a clearly marked flow after MVP or in MVP only if low risk.
- Users can sign off commits if configured.
- The app should warn on empty commit attempts unless explicitly allowed.

## Branching and History
- Users can visualize commit history as a graph.
- Users can create, rename, checkout, and delete branches.
- Users can identify local vs remote tracking branches.
- Users can compare branches and inspect ahead/behind counts.
- Detached HEAD state must be clearly communicated.

## Remote Operations
- Users can fetch, pull, and push with progress feedback.
- Users can inspect remotes and remote URLs.
- The app should surface authentication failures clearly.
- The app should avoid hiding stderr output that helps debug remote failures.

## Stash, Tags, and Recovery
- Users can create, apply, pop, and drop stashes.
- Users can create lightweight and annotated tags in later phases.
- Recovery-oriented actions should surface relevant reflog or undo guidance when practical.

## Conflicts and Safety
- Merge and rebase conflicts must be clearly indicated.
- The app should support opening an external merge tool.
- Destructive actions such as hard reset, branch delete, or stash drop should require confirmation.
- Confirmation dialogs should explain consequences in Git terms and plain language.

## Search and Inspection
- Users can search commit history by message, author, hash, branch, or file path.
- Users can filter file status by state and text search.
- Users can inspect file history and blame in later phases.

## Non-Functional Requirements
## Performance
- UI actions should feel immediate for common operations.
- Repository status refresh should complete quickly on medium-sized repositories.
- History graph rendering must remain usable on repositories with large commit counts.
- Heavy operations must run off the main thread or in worker processes.

## Reliability
- Failures in Git subprocesses must not crash the app.
- The app must retain enough logs to debug failed operations.
- Background jobs must report success, failure, and actionable error details.

## Security
- The app must never store credentials insecurely.
- Sensitive command output should be handled carefully in logs.
- SSH, HTTPS, and credential helper interactions should follow platform conventions where possible.

## Accessibility
- Keyboard navigation should support all core workflows.
- The UI should respect high-DPI environments.
- Color usage in diffs and graph views should not be the only signal.
- Screen reader support should be improved over time, with basic accessibility considered from the start.

## Internationalization
- The architecture should not block future translation support.
- User-facing strings should be isolated to enable localization later.

## Technical Direction
## Platform and Stack
- Language: Python 3.12+
- UI framework: Qt 6 via PySide6
- Packaging: platform-specific bundles for Linux, macOS, and Windows
- Git backend: Git CLI subprocess layer in MVP
- Data/config storage: local app config and repository metadata via structured config files
- Logging: structured local logs for operations and errors

## Rationale
Python plus Qt provides:

- Strong cross-platform desktop support
- A mature widget toolkit suitable for complex multi-pane desktop UX
- Faster iteration and easier contributor onboarding than lower-level native stacks
- Good integration with OS features and packaging workflows

## Proposed Architecture
### Layers
- UI layer: Qt widgets, models, dialogs, and views
- Application layer: commands, workflows, background job orchestration, state management
- Git service layer: subprocess wrappers, parsers, repository inspection, operation execution
- Persistence layer: user settings, recent repositories, cached UI state

### Architectural Requirements
- Git operations must be testable independently of the UI.
- All long-running operations should execute asynchronously.
- Parsing of Git output should be centralized, typed, and version-tolerant.
- The app should use a clear domain model for repositories, refs, status entries, commits, and operations.
- UI state and repository state should be separated to reduce complexity.

## Design Decisions to Validate Early
- PySide6 vs PyQt6 licensing and ecosystem fit
- Qt Widgets vs QML for the initial UI
- Direct Git CLI integration vs libgit2 bindings
- Threading vs multiprocessing for heavy repository tasks
- Local caching strategy for large history graphs

## Recommended Initial Choices
- `PySide6` for licensing clarity and official Qt support
- `Qt Widgets` for faster delivery of desktop productivity UI
- `Git CLI` backend first for compatibility and easier debugging
- `QThreadPool` or equivalent worker abstraction for non-blocking operations

## Open Source Strategy
## License
Recommended: `GPL-3.0-or-later` or `LGPL-3.0-or-later`, depending on desired copyleft strength and plugin strategy.

Decision criteria:
- Strong copyleft if the goal is to keep derivative desktop clients open
- Lesser copyleft if the goal is broader adoption and integration flexibility

This choice should be made before significant external contributions begin.

## Community Expectations
- Public roadmap
- Contributor guide
- Code of conduct
- Issue templates
- Clear architecture docs
- Automated CI for all supported platforms

## Delivery Process Requirements
### Test-First Discipline
- Every implementation session must add or update automated unit tests for the behavior introduced or changed in that session.
- No session is considered complete unless all previously passing unit tests still pass.
- Regressions discovered in an existing test suite take priority over starting the next feature session.
- UI behavior should be validated primarily through presenter, model, and service-layer tests, with limited targeted Qt integration tests where the UI contract matters.
- Repository fixtures should use temporary real Git repositories where possible instead of heavy mocks, so tests validate real command behavior.

### Running Changelog Discipline
- The project must keep a running `CHANGELOG.md` from the first implementation session onward.
- Every completed session must update the changelog with user-visible behavior changes, developer-facing architecture changes, test coverage additions, and notable fixes.
- Changelog entries should be written in concise plain language and grouped under headings such as `Added`, `Changed`, `Fixed`, `Tests`, and `Docs`.
- Pull requests and release preparation should treat a missing changelog update as incomplete work unless the change is explicitly marked internal-only.

### Definition of Done Per Session
- Feature scope for the session is implemented.
- New or changed unit tests are added.
- Existing unit tests still pass.
- Changelog is updated.
- Any new CLI or UX behavior is documented if it affects contributors or users.

## Testing Strategy
### Automated Test Stack
- `pytest` for unit and service tests
- `pytest-qt` for targeted Qt widget and signal/slot behavior
- `pytest-xdist` optionally later for faster local and CI execution
- Coverage reporting enabled in CI from the first working test suite

### Test Layers
- Domain and parser tests for Git output parsing, domain models, and command result mapping
- Service tests for repository operations, error handling, and workflow orchestration
- UI-model tests for selection state, filtering, action enablement, and view model updates
- Narrow Qt integration tests for high-value interactions such as staging actions, commit flow validation, and operation status updates

### Test Data Strategy
- Use temporary repositories initialized during tests for realistic Git states
- Keep fixtures small, deterministic, and platform-safe
- Capture representative edge cases: detached HEAD, merge conflicts, large diffs, binary files, invalid remotes, and authentication failures where simulation is feasible

### Regression Policy
- Each session inherits the full passing test baseline from all previous sessions
- Bug fixes should begin with a failing test when practical
- Refactors must preserve or improve test coverage before feature expansion continues
- If a flaky test appears, stabilizing or quarantining it becomes part of the active session before proceeding

## Release Strategy
## Milestones
### Milestone 0: Foundation
- App shell
- Repository open and clone
- Git executable detection
- Settings and logging
- CI and packaging skeleton

### Milestone 1: Daily Workflow MVP
- Working tree view
- Diff viewer
- Staging
- Commit flow
- Basic branching
- Fetch, pull, push
- History graph

### Milestone 2: Beta
- Stash support
- Better error handling
- External merge tool integration
- Repository dashboard polish
- Performance tuning on large repos

### Milestone 3: v1
- Interactive rebase
- Cherry-pick and revert
- Provider integrations
- Accessibility and theming improvements

## Implementation Sessions
The project should be delivered in small implementation sessions. Each session adds production code, the associated unit tests, and keeps all prior tests passing before moving on.

### Session 0: Project Bootstrap
Goals:
- Create repository structure, Python packaging, dependency management, CI skeleton, linting, formatting, and test harness
- Establish application shell, logging, settings bootstrap, and Git executable detection

Required unit tests:
- Settings load and save behavior
- Git executable discovery success and failure paths
- Basic application service initialization tests

Exit criteria:
- App launches to a placeholder shell
- CI runs tests on Linux, macOS, and Windows where feasible
- `CHANGELOG.md` is created and updated for the session

### Session 1: Repository Open and Dashboard
Goals:
- Implement repository add, open, remove, recent repositories, and dashboard view
- Detect invalid directories and non-Git folders gracefully

Required unit tests:
- Repository discovery and validation logic
- Recent repository persistence
- Dashboard view-model state for empty, valid, and invalid repository states

Regression requirement:
- Session 0 tests remain green

### Session 2: Working Tree Status and Diff Foundation
Goals:
- Build status scanning, file state grouping, and basic inline diff viewer
- Support modified, added, deleted, and untracked file states

Required unit tests:
- Git status parser coverage across file states
- Diff parsing and rendering model tests
- Selection and filtering behavior in the working tree model

Regression requirement:
- Sessions 0-1 tests remain green

### Session 3: Staging and Unstaging
Goals:
- Add stage and unstage by file
- Add hunk or line staging where practical in the selected implementation

Required unit tests:
- Stage and unstage service behavior against temporary repositories
- Partial staging parser or patch-generation tests
- UI-model enablement and state refresh tests after staging actions

Regression requirement:
- Sessions 0-2 tests remain green

### Session 4: Commit Workflow
Goals:
- Add commit composer, author metadata display, summary and body validation, and commit execution
- Warn on empty commits and invalid commit message states

Required unit tests:
- Commit message validation rules
- Commit execution success and failure flows
- Post-commit refresh behavior in repository state models

Regression requirement:
- Sessions 0-3 tests remain green

### Session 5: Branching and History Graph
Goals:
- Add branch list, checkout, create, delete, and history graph basics
- Show tracking status and detached HEAD state

Required unit tests:
- Branch operation service tests
- Ahead/behind calculation and ref mapping tests
- History graph transformation tests for representative commit layouts

Regression requirement:
- Sessions 0-4 tests remain green

### Session 6: Remote Operations
Goals:
- Add fetch, pull, push, remote inspection, progress reporting, and failure surfaces
- Surface stderr and authentication-related failures clearly

Required unit tests:
- Remote command orchestration tests
- Error mapping tests for common fetch, pull, and push failures
- Progress and operation-log model tests

Regression requirement:
- Sessions 0-5 tests remain green

### Session 7: Stash, Tags, and Recovery Cues
Goals:
- Add stash create, apply, pop, drop, and tag creation basics
- Add recoverability guidance for risky operations where appropriate

Required unit tests:
- Stash and tag service tests
- Confirmation and recovery hint state tests
- Repository refresh tests after stash and tag workflows

Regression requirement:
- Sessions 0-6 tests remain green

### Session 8: Conflict Detection and External Tooling
Goals:
- Detect merge and rebase conflicts clearly
- Support launching configured external diff and merge tools

Required unit tests:
- Conflict detection tests using temporary repositories
- External tool configuration validation tests
- UI-state tests for conflict banners, disabled actions, and recovery messaging

Regression requirement:
- Sessions 0-7 tests remain green

### Session 9: Performance and UX Hardening
Goals:
- Improve responsiveness on larger repositories
- Refine background job handling, refresh policies, empty states, and operation logging UX

Required unit tests:
- Caching and invalidation tests for repository state refresh
- Worker scheduling and cancellation tests at the service layer
- UI-model tests for loading, empty, and error states

Regression requirement:
- Sessions 0-8 tests remain green

### Session 10: Beta Readiness
Goals:
- Close high-priority usability gaps
- Tighten packaging, settings migration, telemetry-free crash reporting if desired, and contributor docs

Required unit tests:
- Settings migration tests if config structure changed
- Packaging smoke checks where automatable
- High-priority bug regression tests added during beta stabilization

Regression requirement:
- Sessions 0-9 tests remain green

## Suggested Test Ownership by Layer
- Git parsing and command wrappers should receive the highest unit-test density
- View models and application services should receive focused behavior tests
- Qt widget tests should be selective and reserved for behaviors that cannot be verified cleanly below the widget layer
- End-to-end GUI automation should remain minimal until the MVP stabilizes

## Risks
- Python performance may become an issue on very large repositories if history and diff parsing are not designed carefully.
- Cross-platform packaging and signing may be more time-consuming than expected.
- Git edge cases can quickly expand scope if not constrained for MVP.
- A GUI that hides too much Git complexity may frustrate advanced users, while one that exposes too much may overwhelm newer users.
- Licensing decisions may affect contribution patterns and future plugin architecture.

## Competitive Positioning
`GitQt` should position itself as:

- Open source alternative to SourceTree
- Best-in-class Linux desktop Git client
- Cross-platform GUI companion for serious Git users
- Modern desktop app focused on clarity, speed, and safe workflows

## Open Questions
- Should MVP prioritize Linux packaging formats such as AppImage, Flatpak, and Snap equally, or focus on one first?
- Should interactive rebase be deferred entirely until after beta?
- Should merge conflict editing be built in, or should the app rely on external tools for v1?
- How much hosting provider integration is necessary before v1?
- Should plugin support be part of the architecture from day one, even if not user-facing in MVP?
- Is support for enterprise proxy, custom SSH, and signed commit workflows required for early adopters?

## Appendix
## Example MVP User Stories
- As a developer, I can clone a repository and immediately see its branch and working tree state.
- As a developer, I can stage part of a file and commit only the changes I intend.
- As a developer, I can inspect history visually to understand when and where branches diverged.
- As a developer, I can safely switch branches without losing track of uncommitted work.
- As a developer, I can fetch and push changes without leaving the GUI.

## Suggested Next Documents
- Technical architecture document
- UX wireframes for repository and history views
- Contributor onboarding guide
- MVP milestone backlog
- Packaging and distribution strategy
