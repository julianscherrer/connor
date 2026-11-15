"""Look and feel: colours, stylesheet and the app icon (drawn in code, no image files needed)."""
from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

BG = "#16171d"
SURFACE = "#1f2029"
SURFACE_2 = "#292a37"
BORDER = "#34364a"
TEXT = "#e8e8f0"
MUTED = "#9a9cb0"
ACCENT = "#8b5cf6"
ACCENT_HOVER = "#7c4ff0"
ACCENT_2 = "#ec4899"
ACCENT_SOFT = "#2f2754"
USER_BUBBLE = "#3a2d6b"
SUCCESS = "#34d399"
WARNING = "#fbbf24"
DANGER = "#f87171"
LINK = "#b69cff"

STYLESHEET = f"""
QMainWindow, QDialog {{ background-color: {BG}; }}
QToolTip {{ background-color: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; }}
QTabBar {{ background: {BG}; border: none; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 10px 18px; border: none;
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QPushButton {{
    background-color: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: {ACCENT_SOFT}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {SURFACE_2}; }}
QPushButton#primary {{ background-color: {ACCENT}; border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background-color: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background-color: {ACCENT_SOFT}; color: {MUTED}; }}
QPushButton#danger {{ background-color: transparent; border: 1px solid {DANGER}; color: {DANGER}; font-weight: 600; }}
QPushButton#chip {{
    background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px;
    padding: 5px 12px; font-size: 9.5pt;
}}
QPushButton#chip:hover {{ border-color: {ACCENT}; }}
QPushButton#flat {{ background: transparent; border: none; color: {MUTED}; padding: 2px 6px; font-size: 9pt; }}
QPushButton#flat:hover {{ color: {TEXT}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    background-color: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 8px; selection-background-color: {ACCENT}; selection-color: white;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QDateTimeEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit#titleEdit {{ font-size: 14pt; font-weight: 700; border: none; background: transparent; padding: 4px 0; }}
QPlainTextEdit#storyEditor {{ padding: 14px 18px; border-radius: 12px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SOFT}; outline: none;
}}

QListWidget, QTableWidget {{
    background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; outline: none;
}}
QListWidget#sideList {{ background: transparent; border: none; }}
QTableWidget {{ gridline-color: {SURFACE_2}; alternate-background-color: {BG}; }}
QTableWidget::item {{ padding: 6px; }}
QTableWidget::item:selected {{ background-color: {ACCENT_SOFT}; color: {TEXT}; }}
QHeaderView::section {{
    background-color: {SURFACE_2}; color: {MUTED}; border: none; padding: 6px 8px; font-weight: 600;
}}
QTableCornerButton::section {{ background-color: {SURFACE_2}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSplitter::handle {{ background: {BG}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {MUTED}; border-radius: 4px; background: {SURFACE};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 10px; margin-top: 16px; padding: 14px 12px 10px 12px;
    font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
QMenu {{ background-color: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {ACCENT_SOFT}; }}

QFrame#sidebar {{ background-color: {SURFACE}; border-right: 1px solid {BORDER}; }}
QFrame#panel {{ background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QScrollArea#chatScroll, QWidget#chatContainer {{ background-color: {BG}; border: none; }}
QFrame#bubbleUser {{ background-color: {USER_BUBBLE}; border-radius: 14px; }}
QFrame#bubbleAssistant {{ background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QLabel#bubbleName {{ font-weight: 700; }}
QLabel#eventLabel {{ color: {MUTED}; font-size: 9pt; }}
QLabel#errorLabel {{
    color: {DANGER}; background-color: #2a1618; border: 1px solid #5a2a2e; border-radius: 8px; padding: 8px 12px;
}}
QLabel#muted {{ color: {MUTED}; }}
QLabel#small {{ color: {MUTED}; font-size: 9pt; }}
QLabel#banner {{
    background-color: #33290f; color: {WARNING}; border: 1px solid #6b5418; border-radius: 8px; padding: 10px 14px;
}}
QLabel#welcome {{ color: {TEXT}; padding: 30px; }}
QLabel#h1 {{ font-size: 15pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 700; }}
QLabel#statusDot {{ font-size: 14pt; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    role = QPalette.ColorRole
    palette = QPalette()
    palette.setColor(role.Window, QColor(BG))
    palette.setColor(role.WindowText, QColor(TEXT))
    palette.setColor(role.Base, QColor(SURFACE))
    palette.setColor(role.AlternateBase, QColor(BG))
    palette.setColor(role.Text, QColor(TEXT))
    palette.setColor(role.Button, QColor(SURFACE_2))
    palette.setColor(role.ButtonText, QColor(TEXT))
    palette.setColor(role.Highlight, QColor(ACCENT))
    palette.setColor(role.HighlightedText, QColor("#ffffff"))
    palette.setColor(role.ToolTipBase, QColor(SURFACE_2))
    palette.setColor(role.ToolTipText, QColor(TEXT))
    palette.setColor(role.PlaceholderText, QColor(MUTED))
    palette.setColor(role.Link, QColor(LINK))
    palette.setColor(role.LinkVisited, QColor(LINK))
    for disabled_role in (role.Text, role.ButtonText, role.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, disabled_role, QColor(MUTED))
    app.setPalette(palette)

    font = QFont("Segoe UI") if sys.platform == "win32" else QFont(app.font())
    font.setPointSizeF(10.5)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)


def _icon_pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor(ACCENT))
    gradient.setColorAt(1.0, QColor(ACCENT_2))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.24
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    font = QFont("Segoe UI" if sys.platform == "win32" else "Sans Serif")
    font.setBold(True)
    font.setPixelSize(max(8, int(size * 0.64)))
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(QRectF(0, -size * 0.02, size, size), Qt.AlignmentFlag.AlignCenter, "C")

    dot = size * 0.14
    painter.setBrush(QColor("white"))
    painter.drawEllipse(QRectF(size * 0.70, size * 0.16, dot, dot))
    painter.end()
    return pixmap


def make_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(_icon_pixmap(size))
    return icon
