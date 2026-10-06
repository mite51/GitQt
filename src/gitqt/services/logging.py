from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from gitqt.services.process_context import redact


def configure_logging(log_dir: Path) -> logging.Logger:
    """Create the application logger and a file sink."""

    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gitqt")
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        if (
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename) != log_dir / "gitqt.log"
        ):
            logger.removeHandler(handler)
            handler.close()

    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename) == log_dir / "gitqt.log"
        for handler in logger.handlers
    ):
        file_handler = RotatingFileHandler(
            log_dir / "gitqt.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.addFilter(_Sanitize())
        file_handler.setFormatter(
            _SanitizedFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(file_handler)

    return logger


class _Sanitize(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class _SanitizedFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))
