"""Single application stylesheet.

Applied once to the ``QApplication`` (not to a window) so widgets keep their
styling when a page is re-parented into a detached window.
"""

from __future__ import annotations

APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #101319;
    color: #e6eaf2;
    font-family: Segoe UI, Arial, sans-serif;
    font-size: 13px;
}
/* Labels and checkboxes inherit the surface they sit on instead of painting a
   darker rectangle over cards (visible in the 500x200 OBS window). */
QLabel, QCheckBox, QRadioButton {
    background: transparent;
}
QLabel#Header {
    font-size: 20px;
    font-weight: 700;
    padding: 4px 2px;
}
QLabel#SectionTitle {
    font-size: 14px;
    font-weight: 700;
}
QLabel#PlayerName {
    font-size: 11px;
    font-weight: 700;
}
QLabel#PlayerMeta {
    color: #9aa4b2;
    font-size: 11px;
}
QLabel#AbilityName {
    font-size: 11px;
}
QLabel#Muted {
    color: #9aa4b2;
}
QLabel#ComparisonAmount {
    color: #f2d27a;
    font-size: 11px;
    font-weight: 700;
}
QLabel#ComparisonArrowBlue {
    color: #5fa8ff;
    font-size: 8px;
}
QLabel#ComparisonArrowRed {
    color: #ff6b6b;
    font-size: 8px;
}
QLabel#DetailTitle {
    font-size: 16px;
    font-weight: 700;
}
QLabel#DetailDescription {
    border: 1px solid #2d3440;
    border-radius: 8px;
    padding: 8px;
    background: #141922;
}
QLabel#LargeIcon, QLabel#Portrait {
    border: 1px solid #343d4c;
    border-radius: 6px;
    background: #0d1016;
}
QFrame#TeamPanel, QFrame#PlayerCard, QFrame#DetailPanel, QFrame#GraphPanel {
    border: 1px solid #2d3440;
    border-radius: 8px;
    background: #171b24;
}
QFrame#RoleComparisonPanel {
    background: transparent;
}
QFrame#RoleComparisonMarker {
    border: 1px solid #30394a;
    border-radius: 9px;
    background: #11161f;
}
QFrame#PlayerCard {
    padding: 0px;
}
QPushButton {
    background: #232a35;
    border: 1px solid #394252;
    border-radius: 6px;
    padding: 4px 7px;
}
QPushButton:hover {
    background: #2d3644;
}
QPushButton:disabled {
    color: #6b7280;
    border-color: #2d3440;
}
QScrollArea {
    border: none;
}
QTextBrowser {
    background: #141922;
    border: 1px solid #2d3440;
    border-radius: 8px;
    padding: 4px;
}
QSplitter::handle {
    background: #232a35;
}

/* ── Tabs (Match / Music) ─────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #2d3440;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: #171b24;
    border: 1px solid #2d3440;
    border-bottom: none;
    padding: 6px 18px;
    margin-right: 2px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    color: #9aa4b2;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #232f4a;
    border-color: #40598a;
    color: #ffffff;
}
QTabBar::tab:hover:!selected {
    background: #1e2433;
    color: #cbd5e1;
}

/* ── Status pills ─────────────────────────────────────────────────────── */
QLabel#StatusPillIdle {
    background: #1b2130;
    border: 1px solid #2d3440;
    border-radius: 9px;
    padding: 3px 10px;
    color: #9aa4b2;
    font-size: 11px;
}
QLabel#StatusPillLive {
    background: #14281f;
    border: 1px solid #2f6b4a;
    border-radius: 9px;
    padding: 3px 10px;
    color: #6ee7a8;
    font-size: 11px;
    font-weight: 700;
}
QLabel#StatusPillMusic {
    background: #211a33;
    border: 1px solid #5b4a8a;
    border-radius: 9px;
    padding: 3px 10px;
    color: #c4b5fd;
    font-size: 11px;
}

/* ── Music page ───────────────────────────────────────────────────────── */
QFrame#NowPlayingCard, QFrame#SideCard, QFrame#MusicStrip {
    border: 1px solid #2d3440;
    border-radius: 8px;
    background: #171b24;
}
QLabel#NowPlayingTag {
    color: #a78bfa;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#TrackTitle {
    font-size: 17px;
    font-weight: 700;
    color: #ffffff;
}
QLabel#TrackArtist {
    font-size: 12px;
    color: #cbd5e1;
}
QLabel#StatValue {
    font-size: 18px;
    font-weight: 700;
    color: #ffffff;
}
QLabel#StatLabel {
    color: #9aa4b2;
    font-size: 10px;
}
QLabel#TrackArtwork {
    border: 1px solid #343d4c;
    border-radius: 6px;
    background: #0d1016;
    color: #5b6472;
    font-size: 10px;
}
QPushButton#TransportPrimary {
    background: #4c3a86;
    border: 1px solid #6f57bd;
}
QPushButton#TransportPrimary:hover {
    background: #5b47a0;
}
QPushButton#TransportStop {
    background: #3a2430;
    border: 1px solid #6b3a4c;
}
QPushButton#TransportStop:hover {
    background: #4b2d3c;
}
QPushButton#StripButton {
    padding: 2px 8px;
    font-size: 12px;
}
QPushButton#LinkButton {
    background: transparent;
    border: none;
    color: #7fb2ff;
    padding: 0px 2px;
    text-decoration: underline;
}
QPushButton#LinkButton:hover {
    color: #b9d4ff;
}
QPushButton#NavJump {
    background: #232f4a;
    border: 1px solid #40598a;
}
QLineEdit, QComboBox, QSpinBox, QTableWidget, QPlainTextEdit {
    background: #1c2539;
    border: 1px solid #3c4a64;
    padding: 5px;
    selection-background-color: #455a86;
}
QHeaderView::section {
    background: #222c3f;
    padding: 6px;
    border: none;
}
QTableWidget::item:selected {
    background: #455a86;
}
QTableWidget {
    gridline-color: #232a35;
}
QSlider::groove:horizontal {
    background: #2d3440;
    height: 5px;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #a78bfa;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #e6eaf2;
    width: 11px;
    margin: -4px 0;
    border-radius: 5px;
}

/* ── Settings dialog ──────────────────────────────────────────────────── */
QTabWidget#SettingsTabs::pane {
    border: 1px solid #2d3440;
    border-radius: 8px;
    background: #141922;
}
QGroupBox {
    border: 1px solid #2d3440;
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #9aa4b2;
    font-weight: 700;
}
"""
