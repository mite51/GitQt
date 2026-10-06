from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gitqt import __version__
from gitqt.app.bootstrap import build_services
from gitqt.ui.main_window import MainWindow
from gitqt.ui.theme import apply_dark_palette, set_ui_font_family


def create_qt_app(argv: Sequence[str] | None = None) -> QApplication:
    instance = QApplication.instance()
    if instance is not None:
        return instance
    return QApplication(list(argv or sys.argv))


def main(argv: Sequence[str] | None = None, base_config_dir: Path | None = None) -> int:
    args = list(argv if argv is not None else sys.argv)
    if "--version" in args:
        print(f"GitQt {__version__}")
        return 0
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetDllDirectoryW(None)
    if "--smoke-test" in args:
        from gitqt.app.smoke import smoke_test

        return smoke_test()
    app = create_qt_app(args)
    app.setApplicationName("GitQt")
    app.setOrganizationName("GitQt")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "ui/icons/gitqt.svg")))
    services = build_services(base_config_dir=base_config_dir)
    set_ui_font_family(services.settings.ui_font_family)
    apply_dark_palette(app)
    window = MainWindow(services)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
