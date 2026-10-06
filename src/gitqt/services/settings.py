from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4


class SettingsError(RuntimeError):
    """Raised when settings cannot be loaded from disk."""


DEFAULT_UI_FONT_FAMILY = "Inter"


@dataclass(slots=True)
class AppSettings:
    settings_version: int = 4
    recent_repositories: list[str] = field(default_factory=list)
    git_executable: str | None = None
    last_repository: str | None = None
    external_diff_tool: str | None = None
    external_merge_tool: str | None = None
    crash_reports_enabled: bool = True
    split_branch_paths: bool = True
    history_row_alpha: float = 0.5
    ui_font_family: str = DEFAULT_UI_FONT_FAMILY


_LOCKS = defaultdict(RLock)


class SettingsStore:
    """Load and save application settings as JSON."""

    CURRENT_VERSION = 4

    def __init__(self, path: Path) -> None:
        self.path = path
        self.warning: str | None = None
        self._lock = _LOCKS[str(path.resolve())]

    def load(self) -> AppSettings:
        with self._lock:
            return self._load()

    def _load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            return self._recover()

        if not isinstance(payload, dict):
            return self._recover()

        migrated = self._migrate_payload(payload)
        return AppSettings(
            settings_version=self.CURRENT_VERSION,
            recent_repositories=self._coerce_string_list(migrated.get("recent_repositories")),
            git_executable=self._coerce_optional_string(migrated.get("git_executable")),
            last_repository=self._coerce_optional_string(migrated.get("last_repository")),
            external_diff_tool=self._coerce_optional_string(migrated.get("external_diff_tool")),
            external_merge_tool=self._coerce_optional_string(migrated.get("external_merge_tool")),
            crash_reports_enabled=self._coerce_bool(
                migrated.get("crash_reports_enabled"), default=True
            ),
            split_branch_paths=self._coerce_bool(migrated.get("split_branch_paths"), default=True),
            history_row_alpha=self._coerce_alpha(migrated.get("history_row_alpha"), default=0.5),
            ui_font_family=self._coerce_font_family(
                migrated.get("ui_font_family"),
                default=DEFAULT_UI_FONT_FAMILY,
            ),
        )

    def _recover(self) -> AppSettings:
        damaged = self.path.with_name(f"{self.path.name}.corrupt-{uuid4().hex[:8]}")
        os.replace(self.path, damaged)
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        try:
            content = backup.read_bytes()
            if not isinstance(json.loads(content), dict):
                raise ValueError("Invalid backup")
        except (OSError, ValueError):
            self.warning = f"Damaged settings saved to {damaged.name}; using defaults."
            return AppSettings()
        self._atomic_write(self.path, content)
        self.warning = f"Damaged settings saved to {damaged.name}; restored the previous settings."
        return self._load()

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            if os.name == "posix":
                descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, settings: AppSettings) -> None:
        with self._lock:
            settings.settings_version = self.CURRENT_VERSION
            content = (json.dumps(asdict(settings), indent=2, sort_keys=True) + "\n").encode()
            if self.path.exists():
                previous = self.path.read_bytes()
                try:
                    valid = isinstance(json.loads(previous), dict)
                except ValueError:
                    valid = False
                if valid:
                    self._atomic_write(self.path.with_suffix(self.path.suffix + ".bak"), previous)
            self._atomic_write(self.path, content)

    def update(self, **changes) -> AppSettings:
        with self._lock:
            settings = self._load()
            for name, value in changes.items():
                setattr(settings, name, value)
            self.save(settings)
            return settings

    def _migrate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_version = payload.get("settings_version")
        if not isinstance(source_version, int) or source_version < 1:
            source_version = 1

        migrated = dict(payload)
        if source_version < 2:
            migrated = self._migrate_v1_to_v2(migrated)
        if source_version < 3:
            migrated = self._migrate_v2_to_v3(migrated)
        if source_version < 4:
            migrated = self._migrate_v3_to_v4(migrated)
        migrated["settings_version"] = self.CURRENT_VERSION
        return migrated

    @staticmethod
    def _migrate_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(payload)
        if "git_executable" not in migrated and "git_path" in migrated:
            migrated["git_executable"] = migrated.get("git_path")
        if "recent_repositories" not in migrated and "repositories" in migrated:
            migrated["recent_repositories"] = migrated.get("repositories")
        migrated.setdefault("crash_reports_enabled", True)
        return migrated

    @staticmethod
    def _migrate_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(payload)
        migrated.setdefault("split_branch_paths", True)
        return migrated

    @staticmethod
    def _migrate_v3_to_v4(payload: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(payload)
        migrated.setdefault("ui_font_family", DEFAULT_UI_FONT_FAMILY)
        return migrated

    @staticmethod
    def _coerce_font_family(value: Any, *, default: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    @staticmethod
    def _coerce_optional_string(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str) and item]

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        return default

    @staticmethod
    def _coerce_alpha(value: Any, *, default: float) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            coerced = float(value)
            if 0.0 <= coerced <= 1.0:
                return coerced
        return default
