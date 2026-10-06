# Contributing to GitQt

Thanks for helping improve `GitQt`.

## Local development setup

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install project and developer dependencies:
   ```bash
   python -m pip install -e ".[dev]"
   ```

## Day-to-day workflow

- Run tests before opening a pull request:
  ```bash
  pytest
  ```
- Run lint checks:
  ```bash
  ruff check .
  ```
- Format changes when needed:
  ```bash
  black src tests
  ```

## Session discipline

- Every meaningful change must include or update tests.
- Keep existing tests green while adding new behavior.
- Update `CHANGELOG.md` under `Unreleased` for user-visible, architecture, test, or docs changes.

## Crash reports and privacy

- `GitQt` crash reporting is local-file only and does not send telemetry.
- Reports are written under the app config directory in `crash-reports/` when enabled.
- Users can disable crash report file generation via settings (`crash_reports_enabled`).

## Pull request expectations

- Keep pull requests focused and reasonably small.
- Describe what changed and why.
- Include manual verification notes for UI behavior where applicable.
