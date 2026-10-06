from __future__ import annotations

import sys
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from gitqt.services.process_context import redact


class CrashReporter:
    """Capture unhandled exceptions to local crash-report files."""

    def __init__(self, crash_dir: Path) -> None:
        self._crash_dir = crash_dir
        self._previous_sys_hook = sys.excepthook
        self._previous_thread_hook = threading.excepthook

    def install(self) -> None:
        self._crash_dir.mkdir(parents=True, exist_ok=True)
        sys.excepthook = self._sys_excepthook
        threading.excepthook = self._threading_excepthook

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.install()
        else:
            if sys.excepthook == self._sys_excepthook:
                sys.excepthook = self._previous_sys_hook
            if threading.excepthook == self._threading_excepthook:
                threading.excepthook = self._previous_thread_hook

    def _sys_excepthook(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        self.write_report(exc_type, exc_value, exc_traceback)
        self._previous_sys_hook(exc_type, exc_value, exc_traceback)

    def _threading_excepthook(self, args: threading.ExceptHookArgs) -> None:
        self.write_report(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            thread_name=args.thread.name,
        )
        self._previous_thread_hook(args)

    def write_report(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
        *,
        thread_name: str | None = None,
    ) -> Path:
        self._crash_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_path = self._crash_dir / f"crash-{timestamp}.log"
        trace = redact("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
        thread_label = thread_name or "main"
        report_path.write_text(
            f"timestamp_utc={timestamp}\nthread={thread_label}\nexception_type={exc_type.__name__}\n\n{trace}",
            encoding="utf-8",
        )
        return report_path
