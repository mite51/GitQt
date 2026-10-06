"""Per-job process controls shared by nested Git service calls."""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event, local
from typing import Callable

_LOCAL = local()


def redact(text: str, context=None) -> str:
    context = context or current_context()
    if context is not None:
        for secret in context.secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
    text = re.sub(r"(https?://)[^\s/@]+@", r"\1[redacted]@", text)
    return re.sub(r"(?i)(authorization:|token[=:]|password[=:])\s*\S+", r"\1 [redacted]", text)


@dataclass
class ProcessContext:
    cancel: Event = field(default_factory=Event)
    finished: Event = field(default_factory=Event)
    secrets: list[str] = field(default_factory=list)
    progress: Callable[[str], None] | None = None


def current_context() -> ProcessContext | None:
    return getattr(_LOCAL, "context", None)


@contextmanager
def process_context(context):
    previous = current_context()
    _LOCAL.context = context
    try:
        yield
    finally:
        _LOCAL.context = previous
