"""Application settings dialog.

Lets the user edit the small set of persisted ``AppSettings`` fields that are
relevant to the current milestone: Git executable path, external diff / merge
tools, branch-path splitting in the sidebar, and crash-report opt-in.
"""

from __future__ import annotations

import re
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gitqt.services.git import GitLocator, GitRunner
from gitqt.services.session_jobs import SessionJobs
from gitqt.services.settings import AppSettings, SettingsStore
from gitqt.ui.theme import DEFAULT_UI_FONT_FAMILY, ui_font


class SettingsDialog(QDialog):
    """Modal dialog for editing persisted application settings."""

    settings_saved = Signal(AppSettings)

    def __init__(
        self,
        store: SettingsStore,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("GitQt Settings")
        self.setMinimumWidth(680)
        self._store = store
        self._settings = settings
        self._jobs = SessionJobs(parent=self)

        self._build_ui()

    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        heading = QLabel("Settings")
        heading.setFont(ui_font(13, bold=True))
        root.addWidget(heading)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        self._git_input = QLineEdit(self._settings.git_executable or "")
        self._git_input.setPlaceholderText("Auto-detected when empty")
        form.addRow("Git executable", self._with_browse_button(self._git_input, files=True))

        self._diff_input = QLineEdit(self._settings.external_diff_tool or "")
        self._diff_input.setPlaceholderText("e.g. /usr/bin/meld")
        form.addRow("External diff tool", self._with_browse_button(self._diff_input, files=True))

        self._merge_input = QLineEdit(self._settings.external_merge_tool or "")
        self._merge_input.setPlaceholderText("e.g. /usr/bin/kdiff3")
        form.addRow(
            "External merge tool",
            self._with_browse_button(self._merge_input, files=True),
        )

        self._split_paths_checkbox = QCheckBox('Expand branch paths by "/" in the sidebar')
        self._split_paths_checkbox.setChecked(self._settings.split_branch_paths)
        form.addRow("Sidebar", self._split_paths_checkbox)

        self._alpha_row = self._build_alpha_control(self._settings.history_row_alpha)
        form.addRow("History row transparency", self._alpha_row)

        self._font_combo = QFontComboBox()
        self._font_combo.setCurrentText(self._settings.ui_font_family or DEFAULT_UI_FONT_FAMILY)
        form.addRow("UI font family", self._font_combo)

        self._crash_reports_checkbox = QCheckBox("Enable local crash reports")
        self._crash_reports_checkbox.setChecked(self._settings.crash_reports_enabled)
        form.addRow("Reliability", self._crash_reports_checkbox)

        root.addLayout(form)

        hint = QLabel(
            "Crash reports are written to a local folder only; they are never "
            "uploaded automatically."
        )
        hint.setProperty("dim", True)
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._validation = QLabel()
        self._validation.setWordWrap(True)
        root.addWidget(self._validation)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self._save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_alpha_control(self, current: float) -> QWidget:
        """Slider (0-100%) controlling history alternating-row opacity."""

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self._alpha_slider.setMinimum(0)
        self._alpha_slider.setMaximum(100)
        self._alpha_slider.setSingleStep(5)
        self._alpha_slider.setPageStep(10)
        self._alpha_slider.setValue(int(round(current * 100)))
        layout.addWidget(self._alpha_slider, stretch=1)

        self._alpha_value_label = QLabel(f"{self._alpha_slider.value()}%")
        self._alpha_value_label.setMinimumWidth(42)
        layout.addWidget(self._alpha_value_label)

        def _update_label(value: int) -> None:
            self._alpha_value_label.setText(f"{value}%")

        self._alpha_slider.valueChanged.connect(_update_label)
        return row

    def _with_browse_button(self, line_edit: QLineEdit, *, files: bool) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(line_edit, stretch=1)

        button = QPushButton("Browse\u2026")
        button.setFixedWidth(90)

        def _browse() -> None:
            if files:
                path, _ = QFileDialog.getOpenFileName(self, "Select file", line_edit.text() or "")
            else:
                path = QFileDialog.getExistingDirectory(
                    self, "Select folder", line_edit.text() or ""
                )
            if path:
                line_edit.setText(path)

        button.clicked.connect(_browse)
        layout.addWidget(button)
        return row

    # -----------------------------------------------------------------------

    def _on_save(self) -> None:
        font_family = self._font_combo.currentFont().family().strip()
        if not font_family:
            font_family = DEFAULT_UI_FONT_FAMILY
        updated = replace(
            self._settings,
            git_executable=self._git_input.text().strip() or None,
            external_diff_tool=self._diff_input.text().strip() or None,
            external_merge_tool=self._merge_input.text().strip() or None,
            split_branch_paths=self._split_paths_checkbox.isChecked(),
            crash_reports_enabled=self._crash_reports_checkbox.isChecked(),
            history_row_alpha=self._alpha_slider.value() / 100.0,
            ui_font_family=font_family,
        )
        self._save_button.setEnabled(False)
        self._validation.setText("Validating Git…")

        def validate():
            git = GitLocator().discover(updated.git_executable)
            version = GitRunner(git).run(["--version"], timeout_seconds=10).stdout.strip()
            match = re.search(r"git version (\d+)\.(\d+)", version)
            if match is None or tuple(map(int, match.groups())) < (2, 30):
                raise ValueError(
                    "Git 2.30 or newer is required. Install a supported Git version and choose its"
                    " executable."
                )
            return version

        def apply(version):
            from dataclasses import asdict

            changes = asdict(updated)
            for key in ("recent_repositories", "last_repository", "settings_version"):
                changes.pop(key)
            saved = self._store.update(**changes)
            self._settings = saved
            self.settings_saved.emit(saved)
            self.accept()

        def failed(error):
            self._validation.setText(
                f"{error}\nInstall Git or select its executable, then try again."
            )
            self._save_button.setEnabled(True)

        self._jobs.write(validate, apply, failed)

    def reject(self):
        if not self._jobs.busy:
            super().reject()

    def current_settings(self) -> AppSettings:
        return self._settings
