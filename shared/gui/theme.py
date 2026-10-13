from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#4f8cff"

QSS = f"""
QWidget {{ font-size: 10pt; }}
QMainWindow, QDialog {{ background: #16181d; }}
QGroupBox {{
    border: 1px solid #2b2f38; border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #9aa3b2; }}
QTableWidget {{
    background: #1b1e24; alternate-background-color: #1f232a; gridline-color: #2b2f38;
    border: 1px solid #2b2f38; border-radius: 6px; selection-background-color: #2a3b5c;
}}
QHeaderView::section {{
    background: #20242c; color: #9aa3b2; border: none; border-bottom: 1px solid #2b2f38; padding: 6px;
}}
QPushButton {{
    background: #262a33; border: 1px solid #343944; border-radius: 5px; padding: 6px 12px; color: #e6e8ec;
}}
QPushButton:hover {{ background: #2e3340; }}
QPushButton:disabled {{ color: #6b7280; background: #1f2229; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; padding: 8px 22px; }}
QPushButton#primary:hover {{ background: #6a9dff; }}
QPushButton#primary:disabled {{ background: #2a3550; border-color: #2a3550; color: #8090b0; }}
QPushButton#danger {{ background: #3a2226; border-color: #5a2a31; color: #ffb4bd; }}
QLineEdit, QComboBox, QSpinBox {{
    background: #1b1e24; border: 1px solid #343944; border-radius: 4px; padding: 4px 6px; color: #e6e8ec;
}}
QComboBox QAbstractItemView {{ background: #1f232a; selection-background-color: #2a3b5c; }}
QPlainTextEdit {{
    background: #121418; border: 1px solid #2b2f38; border-radius: 6px; color: #b8c0cc;
    font-family: Consolas, "Cascadia Mono", "DejaVu Sans Mono", monospace; font-size: 9pt;
}}
QProgressBar {{
    background: #1b1e24; border: 1px solid #2b2f38; border-radius: 4px; text-align: center; color: #e6e8ec;
    min-height: 16px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px; border: 1px solid #5a6170; background: #1b1e24;
}}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolTip {{
    background: #262a33; color: #e6e8ec; border: 1px solid #3a4050; padding: 6px; border-radius: 4px;
}}
QLabel#title {{ font-size: 15pt; font-weight: 700; color: #f2f4f7; }}
QLabel#muted {{ color: #8a93a3; }}
QLabel#chip_ok {{ background: #1d3326; color: #8de0a6; border-radius: 9px; padding: 2px 10px; }}
QLabel#chip_ng {{ background: #3a2226; color: #ffb4bd; border-radius: 9px; padding: 2px 10px; }}
QLabel#drop {{ color: #6b7280; font-size: 11pt; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [
        (QPalette.Window, "#16181d"), (QPalette.WindowText, "#e6e8ec"), (QPalette.Base, "#1b1e24"),
        (QPalette.AlternateBase, "#1f232a"), (QPalette.Text, "#e6e8ec"), (QPalette.Button, "#262a33"),
        (QPalette.ButtonText, "#e6e8ec"), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"),
        (QPalette.ToolTipBase, "#262a33"), (QPalette.ToolTipText, "#e6e8ec"), (QPalette.PlaceholderText, "#6b7280"),
    ]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
