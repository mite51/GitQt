"""An opt-in local support bundle with no repository contents or settings file."""

import json
import platform
import zipfile
from pathlib import Path

from gitqt import __version__
from gitqt.services.process_context import redact


def export_support_bundle(services, destination):
    from PySide6.QtCore import qVersion

    data = {
        "application": "GitQt",
        "version": __version__,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "qt": qVersion(),
        "git_configured": services.git is not None,
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("diagnostics.json", json.dumps(data, indent=2) + "\n")
        for path in sorted(services.paths.log_dir.glob("gitqt.log*"))[:4]:
            # Bound exported data even when a log was externally replaced with a huge file.
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 1_000_000))
                content = stream.read(1_000_000).decode("utf-8", errors="replace")
            bundle.writestr(path.name, redact(content))
    return str(Path(destination))
