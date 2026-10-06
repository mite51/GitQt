"""Deterministic initials-based avatar rendering.

Generates a colored circular avatar with the author's initials. Colors are
chosen from a fixed palette based on a hash of the author email (falling
back to the name) so the same author reliably gets the same color without
any network dependency.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QPixmapCache,
)

_AVATAR_PALETTE = (
    "#89b4fa",
    "#a6e3a1",
    "#f9e2af",
    "#f38ba8",
    "#cba6f7",
    "#89dceb",
    "#fab387",
    "#f5c2e7",
    "#74c7ec",
    "#94e2d5",
    "#b4befe",
    "#eba0ac",
)


def initials_for(name: str, email: str = "") -> str:
    """Compute up to two uppercase initials for the author."""

    cleaned = (name or "").strip()
    if cleaned:
        parts = [token for token in cleaned.replace("_", " ").split() if token]
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        if parts:
            token = parts[0]
            letters = [c for c in token if c.isalpha()]
            if len(letters) >= 2:
                return (letters[0] + letters[1]).upper()
            if letters:
                return letters[0].upper()
            return token[:2].upper()

    trimmed_email = (email or "").strip()
    if trimmed_email:
        local = trimmed_email.split("@", 1)[0]
        if local:
            return local[:2].upper()

    return "?"


def color_for(name: str, email: str = "") -> QColor:
    """Deterministic color pick from the palette."""

    key = (email or name or "").lower().encode("utf-8")
    if not key:
        return QColor(_AVATAR_PALETTE[0])
    digest = hashlib.md5(key).digest()  # noqa: S324 - non-cryptographic hash
    index = digest[0] % len(_AVATAR_PALETTE)
    return QColor(_AVATAR_PALETTE[index])


def best_text_color_for(background: QColor) -> QColor:
    """Return a foreground color with good contrast against ``background``."""

    luminance = (
        0.2126 * background.redF() + 0.7152 * background.greenF() + 0.0722 * background.blueF()
    )
    return QColor("#1e1e2e") if luminance > 0.6 else QColor("#ffffff")


def render_avatar(name: str, email: str, size: int) -> QPixmap:
    """Render (or fetch from cache) a circular avatar pixmap."""

    key = f"gitqt_avatar::{size}::{(email or '').lower()}::{(name or '').lower()}"
    cached = QPixmap()
    if QPixmapCache.find(key, cached):
        return cached

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    bg = color_for(name, email)
    painter.setBrush(QBrush(bg))
    painter.setPen(QPen(bg.darker(130), 1))
    painter.drawEllipse(QPoint(size // 2, size // 2), size // 2 - 1, size // 2 - 1)

    text = initials_for(name, email)
    fg = best_text_color_for(bg)
    painter.setPen(fg)
    font = QFont()
    font.setBold(True)
    font.setPointSize(max(6, int(size * 0.42)))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter), text)
    painter.end()

    QPixmapCache.insert(key, pixmap)
    return pixmap
