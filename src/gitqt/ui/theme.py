"""Shared styling, palette, and constants for the GitQt UI."""

from __future__ import annotations

from importlib import resources

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Palette colours
# ---------------------------------------------------------------------------

BG_PRIMARY = QColor("#1e1e2e")
BG_SECONDARY = QColor("#181825")
BG_SURFACE = QColor("#313244")
BG_SURFACE_HOVER = QColor("#45475a")

FG_PRIMARY = QColor("#cdd6f4")
FG_SECONDARY = QColor("#a6adc8")
FG_DIM = QColor("#6c7086")

ACCENT = QColor("#89b4fa")
ACCENT_HOVER = QColor("#74c7ec")
ACCENT_PRESSED = QColor("#89dceb")

SUCCESS = QColor("#a6e3a1")
WARNING = QColor("#f9e2af")
ERROR = QColor("#f38ba8")

DIFF_ADD_BG = QColor(166, 227, 161, 30)
DIFF_DEL_BG = QColor(243, 139, 168, 30)
DIFF_ADD_FG = QColor("#a6e3a1")
DIFF_DEL_FG = QColor("#f38ba8")
DIFF_CONTEXT_FG = QColor("#a6adc8")
DIFF_META_FG = QColor("#89b4fa")
DIFF_LINE_NUM = QColor("#6c7086")

BORDER = QColor("#45475a")
SELECTION_BG = QColor("#45475a")

STATUS_MODIFIED = QColor("#f9e2af")
STATUS_ADDED = QColor("#a6e3a1")
STATUS_DELETED = QColor("#f38ba8")
STATUS_UNTRACKED = QColor("#a6adc8")

GRAPH_COLORS = [
    QColor("#89b4fa"),
    QColor("#a6e3a1"),
    QColor("#f9e2af"),
    QColor("#f38ba8"),
    QColor("#cba6f7"),
    QColor("#89dceb"),
    QColor("#fab387"),
    QColor("#f5c2e7"),
]


def palette_is_dark(palette: QPalette | None = None) -> bool:
    """Return whether the active palette uses a dark window background."""

    if palette is None:
        app = QApplication.instance()
        palette = app.palette() if app is not None else QPalette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def active_branch_color(palette: QPalette | None = None) -> QColor:
    """High-contrast current-branch color for dark and light palettes."""

    return QColor("#f9e2af") if palette_is_dark(palette) else QColor("#8a5d00")


def branch_selection_outline_color(palette: QPalette | None = None) -> QColor:
    """Contrasting outline used to identify a selected history lane."""

    return QColor("#fff4c2") if palette_is_dark(palette) else QColor("#5c4300")


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

DEFAULT_UI_FONT_FAMILY = "Inter"
_UI_FONT_FALLBACKS = "Segoe UI, Cantarell, Noto Sans, sans-serif"
_UI_FONT_FAMILY: str = DEFAULT_UI_FONT_FAMILY
_BUNDLED_FONT_LOAD_ATTEMPTED = False


def set_ui_font_family(family: str | None) -> None:
    """Configure the family used by :func:`ui_font` (and the QApplication font).

    Falls back to :data:`DEFAULT_UI_FONT_FAMILY` if ``family`` is empty or
    ``None``. Subsequent calls to :func:`ui_font` will use the new family.
    """

    global _UI_FONT_FAMILY
    cleaned = (family or "").strip()
    _UI_FONT_FAMILY = cleaned or DEFAULT_UI_FONT_FAMILY


def get_ui_font_family() -> str:
    return _UI_FONT_FAMILY


def _load_bundled_fonts() -> None:
    """Register any TTF/OTF assets bundled in ``gitqt.ui.fonts`` with Qt.

    This makes fonts like Inter available even when the user has not
    installed them system-wide. Missing or unreadable files are skipped
    silently -- the system font fallback chain still applies.
    """

    global _BUNDLED_FONT_LOAD_ATTEMPTED
    if _BUNDLED_FONT_LOAD_ATTEMPTED:
        return
    _BUNDLED_FONT_LOAD_ATTEMPTED = True
    try:
        package = resources.files("gitqt.ui.fonts")
    except (ModuleNotFoundError, FileNotFoundError):
        return
    for entry in package.iterdir():
        name = entry.name.lower()
        if not (name.endswith(".ttf") or name.endswith(".otf")):
            continue
        try:
            with resources.as_file(entry) as path:
                QFontDatabase.addApplicationFont(str(path))
        except OSError:
            continue


def mono_font(size: int = 12) -> QFont:
    font = QFont("JetBrains Mono, Fira Code, Consolas, monospace")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(size)
    return font


def ui_font(size: int = 11, bold: bool = False) -> QFont:
    font = QFont(f"{_UI_FONT_FAMILY}, {_UI_FONT_FALLBACKS}")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSize(size)
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    return font


# ---------------------------------------------------------------------------
# Apply dark palette to QApplication
# ---------------------------------------------------------------------------


def apply_dark_palette(app: QApplication) -> None:
    app.setStyle("Fusion")
    _load_bundled_fonts()
    palette = QPalette()

    palette.setColor(QPalette.ColorRole.Window, BG_PRIMARY)
    palette.setColor(QPalette.ColorRole.WindowText, FG_PRIMARY)
    palette.setColor(QPalette.ColorRole.Base, BG_SECONDARY)
    palette.setColor(QPalette.ColorRole.AlternateBase, BG_SURFACE)
    palette.setColor(QPalette.ColorRole.ToolTipBase, BG_SURFACE)
    palette.setColor(QPalette.ColorRole.ToolTipText, FG_PRIMARY)
    palette.setColor(QPalette.ColorRole.Text, FG_PRIMARY)
    palette.setColor(QPalette.ColorRole.Button, BG_SURFACE)
    palette.setColor(QPalette.ColorRole.ButtonText, FG_PRIMARY)
    palette.setColor(QPalette.ColorRole.BrightText, ACCENT)
    palette.setColor(QPalette.ColorRole.Link, ACCENT)
    palette.setColor(QPalette.ColorRole.Highlight, ACCENT)
    palette.setColor(QPalette.ColorRole.HighlightedText, BG_PRIMARY)
    palette.setColor(QPalette.ColorRole.PlaceholderText, FG_DIM)

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, FG_DIM)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, FG_DIM)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, FG_DIM)

    app.setPalette(palette)
    app.setFont(ui_font())

    app.setStyleSheet(_GLOBAL_STYLESHEET)


# ---------------------------------------------------------------------------
# Global stylesheet supplement (applied on top of the palette)
# ---------------------------------------------------------------------------

_GLOBAL_STYLESHEET = """
QMainWindow {
    background: #1e1e2e;
}

QWidget {
    color: #cdd6f4;
}

QSplitter::handle {
    background: #45475a;
}
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical   { height: 1px; }

QTreeWidget, QListWidget, QTableWidget {
    background: #181825;
    border: 1px solid #45475a;
    border-radius: 6px;
    outline: none;
    padding: 2px;
}
QTreeWidget::item, QListWidget::item {
    padding: 3px 6px;
    border-radius: 4px;
}
QTreeWidget::item:selected, QListWidget::item:selected {
    background: #45475a;
    color: #cdd6f4;
}
QTreeWidget::item:hover, QListWidget::item:hover {
    background: #313244;
}
QHeaderView::section {
    background: #313244;
    color: #a6adc8;
    border: none;
    padding: 4px 8px;
    font-weight: 600;
}

QPushButton {
    background: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 5px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background: #45475a;
    border-color: #89b4fa;
}
QPushButton:pressed {
    background: #585b70;
}
QPushButton:disabled {
    color: #6c7086;
    border-color: #313244;
}
QPushButton[accent="true"] {
    background: #89b4fa;
    color: #1e1e2e;
    border-color: #89b4fa;
    font-weight: 600;
}
QPushButton[accent="true"]:hover {
    background: #74c7ec;
    border-color: #74c7ec;
}
QPushButton[accent="true"]:pressed {
    background: #89dceb;
}
QPushButton[danger="true"] {
    border-color: #f38ba8;
    color: #f38ba8;
}
QPushButton[danger="true"]:hover {
    background: rgba(243, 139, 168, 40);
}

QPushButton[accent="true"]:disabled, QPushButton[danger="true"]:disabled {
    background: #313244;
    color: #6c7086;
    border-color: #313244;
}

QLineEdit, QTextEdit, QPlainTextEdit {
    background: #181825;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 5px 8px;
    color: #cdd6f4;
    selection-background-color: #45475a;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #89b4fa;
}

QTabWidget::pane {
    border: 1px solid #45475a;
    border-radius: 6px;
    background: #1e1e2e;
    top: -1px;
}
QTabBar::tab {
    background: #1e1e2e;
    color: #a6adc8;
    border: 1px solid #45475a;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 16px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #89b4fa;
    color: #11111b;
    border-color: #89b4fa;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background: #45475a;
}

QScrollBar:vertical {
    background: #181825;
    width: 10px;
    border: none;
}
QScrollBar::handle:vertical {
    background: #45475a;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #585b70;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

QScrollBar:horizontal {
    background: #181825;
    height: 10px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: #45475a;
    min-width: 30px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #585b70;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

QToolTip {
    background: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 4px 8px;
    border-radius: 4px;
}

QMenu {
    background: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 5px 24px 5px 12px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #45475a;
}
QMenu::separator {
    height: 1px;
    background: #45475a;
    margin: 4px 8px;
}

QGroupBox {
    border: 1px solid #45475a;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 16px;
    font-weight: 600;
    color: #a6adc8;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}

QLabel[heading="true"] {
    font-size: 13pt;
    font-weight: 600;
    color: #cdd6f4;
}
QLabel[subheading="true"] {
    font-size: 10pt;
    color: #a6adc8;
}
QLabel[dim="true"] {
    color: #6c7086;
}
"""
