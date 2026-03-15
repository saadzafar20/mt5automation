#!/usr/bin/env python3
"""PlatAlgo Relay — PyQt6 premium execution console."""

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests

from typing import Optional
from PyQt6.QtCore import (Qt, QObject, pyqtSignal, QTimer, QSize)
from PyQt6.QtGui import (QFont, QIcon, QPixmap, QPainter, QColor, QCursor)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QLineEdit, QCheckBox, QTextEdit, QScrollArea, QProgressBar,
    QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget,
    QSystemTrayIcon, QMenu, QDialog, QMessageBox, QFileDialog,
    QSizePolicy, QSpacerItem,
)

try:
    import keyring
except ImportError:
    keyring = None

try:
    import winreg
except ImportError:
    winreg = None

try:
    import webview
except ImportError:
    webview = None

try:
    from PIL import Image as _PILImage
except ImportError:
    _PILImage = None

from relay import Relay, RelayClient

# ── Platform ──────────────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"

try:
    from _version import APP_VERSION
except ImportError:
    APP_VERSION = os.getenv("RELAY_APP_VERSION", "1.0.0")

PRODUCTION_BRIDGE_URL = "http://app.platalgo.com"
KEYRING_SERVICE       = "platalgo-relay"
LAST_USER_FILE        = "relay_last_user.json"
WIN_TASK_NAME         = "PlatAlgoRelay"
MAC_PLIST_LABEL       = "com.platalgo.relay"
MAC_PLIST_PATH        = Path.home() / "Library" / "LaunchAgents" / f"{MAC_PLIST_LABEL}.plist"

# ── Colors ────────────────────────────────────────────────────────────────────
BG          = "#0A0F0C"
BG_SIDE     = "#0D1610"
BG_CARD     = "#112018"
BG_INPUT    = "#162B1E"
FG          = "#ECFDF5"
FG_MUTED    = "#86EFAC"
FG_SOFT     = "#4ADE80"
FG_FAINT    = "#166534"
GOLD        = "#D97706"
GOLD_LT     = "#F59E0B"
GOLD_DK     = "#78350F"
GOLD_BORDER = "#451A03"
GREEN       = "#22C55E"
GREEN_LT    = "#4ADE80"
GREEN_BG    = "#14402A"
GREEN_HOVER = "#0F2318"
BORDER      = "#166534"
BORDER_SOFT = "#15803D"
DANGER      = "#EF4444"
DANGER_BG   = "#1C0000"
DANGER_BDR  = "#7F1D1D"
GLASS       = "#162B1E"
GLASS_GOLD  = "#1A0E00"
GLASS_EM    = "#0D2318"
SUCCESS_BG  = "#052E16"

# ── QSS ──────────────────────────────────────────────────────────────────────
QSS = f"""
* {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
    color: {FG};
}}
QMainWindow, QWidget#root {{
    background-color: {BG};
}}
QWidget {{
    background-color: transparent;
}}
QWidget#bgRoot {{
    background-color: {BG};
}}
QFrame#sidebar {{
    background-color: {BG_SIDE};
    border-right: 1px solid {BORDER};
}}
QFrame#header {{
    background-color: {BG_SIDE};
    border-bottom: 1px solid {BORDER};
}}
QFrame#card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#goldCard {{
    background-color: {BG_CARD};
    border: 1px solid {GOLD_BORDER};
    border-radius: 14px;
}}
QFrame#contentArea {{
    background-color: {BG};
}}
QPushButton {{
    background-color: {GLASS};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 8px 16px;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {GREEN_HOVER};
    border-color: {GREEN};
}}
QPushButton:disabled {{
    color: {FG_FAINT};
    background-color: {GLASS};
    border-color: {BORDER};
}}
QPushButton#goldBtn {{
    background-color: {GOLD};
    color: #0A0600;
    border: none;
    border-radius: 11px;
    font-size: 14px;
    font-weight: bold;
    padding: 14px 24px;
}}
QPushButton#goldBtn:hover {{
    background-color: {GOLD_LT};
}}
QPushButton#goldBtn:disabled {{
    background-color: {GOLD_DK};
    color: #92400E;
}}
QPushButton#outlineBtn {{
    background-color: transparent;
    color: {FG_MUTED};
    border: 1px solid {BORDER};
    border-radius: 11px;
    padding: 14px 16px;
    font-size: 13px;
}}
QPushButton#outlineBtn:hover {{
    background-color: {GREEN_HOVER};
    border-color: {GREEN};
}}
QPushButton#oauthBtn {{
    background-color: {GLASS};
    color: {FG};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: bold;
}}
QPushButton#oauthBtn:hover {{
    background-color: #1F3828;
    border-color: {GREEN};
}}
QPushButton#navBtn {{
    background-color: transparent;
    color: {FG_MUTED};
    border: none;
    border-radius: 8px;
    text-align: left;
    padding: 10px 10px 10px 14px;
    font-size: 13px;
}}
QPushButton#navBtn:hover {{
    background-color: {GREEN_HOVER};
}}
QPushButton#navBtnActive {{
    background-color: {GREEN_BG};
    color: {GREEN_LT};
    border: none;
    border-left: 3px solid {GREEN};
    border-radius: 8px;
    text-align: left;
    padding: 10px 10px 10px 11px;
    font-size: 13px;
    font-weight: bold;
}}
QPushButton#buyBtn {{
    background-color: {SUCCESS_BG};
    color: {GREEN};
    border: 1px solid {BORDER};
    border-radius: 8px;
    font-weight: bold;
    padding: 8px 24px;
}}
QPushButton#buyBtnActive {{
    background-color: #166534;
    color: {FG};
    border: 1px solid {GREEN};
    border-radius: 8px;
    font-weight: bold;
    padding: 8px 24px;
}}
QPushButton#sellBtn {{
    background-color: {DANGER_BG};
    color: {DANGER};
    border: 1px solid {DANGER_BDR};
    border-radius: 8px;
    font-weight: bold;
    padding: 8px 24px;
}}
QPushButton#sellBtnActive {{
    background-color: #7F1D1D;
    color: {FG};
    border: 1px solid {DANGER};
    border-radius: 8px;
    font-weight: bold;
    padding: 8px 24px;
}}
QPushButton#dangerBtn {{
    background-color: {DANGER_BG};
    color: #FCA5A5;
    border: 1px solid {DANGER_BDR};
    border-radius: 8px;
    padding: 8px 16px;
}}
QPushButton#dangerBtn:hover {{
    background-color: #2A0000;
}}
QPushButton#smallBtn {{
    background-color: {GLASS};
    color: {FG_MUTED};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 12px;
    font-size: 12px;
}}
QPushButton#smallBtn:hover {{
    background-color: {GREEN_HOVER};
    border-color: {GREEN};
}}
QLineEdit {{
    background-color: {BG_INPUT};
    color: {FG};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 13px;
    selection-background-color: {GREEN};
}}
QLineEdit:focus {{
    border-color: {GREEN_LT};
}}
QLineEdit:read-only {{
    color: {GOLD_LT};
    border-color: {GOLD_BORDER};
}}
QTextEdit {{
    background-color: {BG};
    color: {FG_MUTED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    selection-background-color: {GREEN};
}}
QCheckBox {{
    color: {FG_MUTED};
    spacing: 8px;
    font-size: 12px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER_SOFT};
    background-color: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background-color: {GREEN};
    border-color: {GREEN};
}}
QScrollBar:vertical {{
    background: {BG};
    width: 7px;
    border-radius: 3px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QProgressBar {{
    background-color: {GLASS};
    border: 1px solid {BORDER};
    border-radius: 5px;
    text-align: center;
    color: {FG};
    font-size: 12px;
}}
QProgressBar::chunk {{
    background-color: {GREEN};
    border-radius: 5px;
}}
QMenu {{
    background-color: {BG_CARD};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 8px 20px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {GREEN_BG};
    color: {GREEN_LT};
}}
QDialog {{
    background-color: {BG_SIDE};
    color: {FG};
}}
"""

# ── MT5 path detection ────────────────────────────────────────────────────────
def detect_mt5_path() -> str:
    if winreg:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key_path in [r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"]:
                try:
                    with winreg.OpenKey(root, key_path) as base:
                        for idx in range(winreg.QueryInfoKey(base)[0]):
                            sub_name = winreg.EnumKey(base, idx)
                            with winreg.OpenKey(base, sub_name) as sub:
                                try:
                                    dn = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                                except OSError:
                                    continue
                                if "MetaTrader" not in dn:
                                    continue
                                try:
                                    loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                                    c = os.path.join(loc, "terminal64.exe")
                                    if os.path.exists(c):
                                        return c
                                except OSError:
                                    continue
                except OSError:
                    continue
    for c in [r"C:\Program Files\MetaTrader 5\terminal64.exe",
              r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe"]:
        if os.path.exists(c):
            return c
    return ""


# ── Startup registration ──────────────────────────────────────────────────────
def _startup_enabled() -> bool:
    if IS_WINDOWS:
        return subprocess.run(["schtasks", "/query", "/tn", WIN_TASK_NAME],
                              capture_output=True, text=True).returncode == 0
    if IS_MAC:
        return MAC_PLIST_PATH.exists()
    return False


def _enable_startup():
    exe    = sys.executable
    script = os.path.abspath(sys.argv[0])
    if IS_WINDOWS:
        subprocess.run(["schtasks", "/create", "/tn", WIN_TASK_NAME,
                        "/tr", f'"{exe}" "{script}"',
                        "/sc", "ONSTART", "/ru", "SYSTEM", "/rl", "HIGHEST", "/f"], check=True)
    elif IS_MAC:
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{MAC_PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{exe}</string><string>{script}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>"""
        MAC_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MAC_PLIST_PATH.write_text(plist)
        subprocess.run(["launchctl", "load", str(MAC_PLIST_PATH)], check=True)


def _disable_startup():
    if IS_WINDOWS:
        subprocess.run(["schtasks", "/delete", "/tn", WIN_TASK_NAME, "/f"], check=False)
    elif IS_MAC:
        if MAC_PLIST_PATH.exists():
            subprocess.run(["launchctl", "unload", str(MAC_PLIST_PATH)], check=False)
            MAC_PLIST_PATH.unlink(missing_ok=True)


# ── Signals (for cross-thread UI updates) ────────────────────────────────────
class AppSignals(QObject):
    status_changed   = pyqtSignal(str)
    log_appended     = pyqtSignal(str)
    dot_changed      = pyqtSignal(str, bool)
    summary_updated  = pyqtSignal(str)
    oauth_success    = pyqtSignal(str, str, bool)
    update_prompt    = pyqtSignal(str, str)
    webhook_updated  = pyqtSignal(str)
    apikey_updated   = pyqtSignal(str)
    vps_activated    = pyqtSignal()
    vps_failed       = pyqtSignal(str)
    vps_btn_reset    = pyqtSignal()
    connect_enabled  = pyqtSignal(bool)
    avatar_updated   = pyqtSignal(str)
    progress_changed = pyqtSignal(float, str)


# ── Helper widget builders ────────────────────────────────────────────────────
def _lbl(parent: QWidget, text: str, color: str = FG,
         size: int = 13, bold: bool = False, wrap: bool = False) -> QLabel:
    l = QLabel(text, parent)
    style = f"color: {color}; font-size: {size}px;"
    if bold:
        style += " font-weight: bold;"
    style += " background: transparent; border: none;"
    l.setStyleSheet(style)
    if wrap:
        l.setWordWrap(True)
    return l


def _entry(placeholder: str = "", password: bool = False) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFixedHeight(44)
    if password:
        e.setEchoMode(QLineEdit.EchoMode.Password)
    return e


def _card(gold: bool = False) -> QFrame:
    f = QFrame()
    f.setObjectName("goldCard" if gold else "card")
    return f


def _hline(color: str = BORDER) -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {color}; background-color: {color}; border: none; max-height: 1px;")
    return line


def _spacer(w: int = 0, h: int = 0, hpol=QSizePolicy.Policy.Minimum,
            vpol=QSizePolicy.Policy.Minimum) -> QSpacerItem:
    return QSpacerItem(w, h, hpol, vpol)


def _chip_label(text: str, bg: str, color: str, parent: QWidget = None) -> QLabel:
    l = QLabel(text, parent)
    l.setStyleSheet(
        f"background-color: {bg}; color: {color}; border-radius: 6px; "
        f"padding: 3px 8px; font-size: 10px; font-weight: bold; border: none;"
    )
    l.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return l


# ── Main App ──────────────────────────────────────────────────────────────────
class RelayGuiApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sig = AppSignals()
        self.relay        = None
        self.tray_icon    = None
        self.api_key      = None
        self.vps_active   = False
        self._oauth_provider = None
        self._current_panel  = "connect"
        self._tv_action      = "BUY"

        # Widget refs
        self._header_dots   : dict = {}  # name -> (dot_lbl, text_lbl)
        self._status_rings  : dict = {}  # name -> QFrame
        self._ring_letters  : dict = {}  # name -> QLabel
        self._ring_status_lbl: dict = {} # name -> QLabel ("Online"/"Offline")
        self._nav_btns      : dict = {}  # key -> QPushButton
        self._panels        : dict = {}  # key -> QWidget
        self._stacked       = None
        self._panel_idx     : dict = {}

        # Form widget refs
        self.user_entry    = None
        self.pass_entry    = None
        self.remember_cb   = None
        self.startup_cb    = None
        self.bridge_entry  = None
        self.mt5_path_edit = None
        self.mt5_acct_edit = None
        self.mt5_pw_edit   = None
        self.mt5_server_edit = None
        self.webhook_entry = None
        self.api_key_edit  = None
        self.summary_text  = None
        self.log_box       = None
        self.tv_preview    = None
        self.vps_btn       = None
        self.vps_disable_btn = None
        self.connect_btn   = None
        self._avatar       = None
        self._status_pill  = None
        self._login_form   = None
        self._oauth_frame  = None
        self._oauth_prov_lbl = None
        self._oauth_user_lbl = None
        self._buy_btn      = None
        self._sell_btn     = None
        self.tv_symbol_edit = None
        self.tv_size_edit  = None
        self.tv_sl_edit    = None
        self.tv_tp_edit    = None
        self.tv_script_edit = None
        self.vps_status_lbl = None
        self._api_key_visible = False
        self._webhook_copy_btn = None

        self._connect_signals()
        self._build_ui()
        self._load_cached_credentials()
        self.startup_cb.setChecked(_startup_enabled())

        threading.Thread(target=self._check_updates, daemon=True).start()
        self._auto_connect_if_cached()

    def _connect_signals(self):
        s = self.sig
        s.status_changed.connect(self._on_status_changed)
        s.log_appended.connect(self._on_log_appended)
        s.dot_changed.connect(self._on_dot_changed)
        s.summary_updated.connect(self._on_summary_updated)
        s.oauth_success.connect(self._on_oauth_success)
        s.update_prompt.connect(self._prompt_update)
        s.webhook_updated.connect(self._on_webhook_updated)
        s.apikey_updated.connect(self._on_apikey_updated)
        s.vps_activated.connect(self._on_vps_activated)
        s.vps_failed.connect(self._on_vps_failed)
        s.vps_btn_reset.connect(self._on_vps_btn_reset)
        s.connect_enabled.connect(self._on_connect_enabled)
        s.avatar_updated.connect(self._on_avatar_updated)

    # =========================================================================
    # UI BUILD
    # =========================================================================
    def _build_ui(self):
        self.setWindowTitle("PlatAlgo Relay")
        self.resize(1300, 860)
        self.setMinimumSize(1100, 720)

        # Icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        root = QWidget()
        root.setObjectName("bgRoot")
        root.setStyleSheet(f"QWidget#bgRoot {{ background-color: {BG}; }}")
        self.setCentralWidget(root)

        vlay = QVBoxLayout(root)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        self._build_header(vlay)

        body = QWidget()
        body.setStyleSheet(f"background-color: {BG};")
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        vlay.addWidget(body, 1)

        self._build_sidebar(body_lay)
        self._build_content(body_lay)

    # ── Header ────────────────────────────────────────────────────────────────
    def _build_header(self, parent_layout: QVBoxLayout):
        hdr = QFrame()
        hdr.setObjectName("header")
        hdr.setFixedHeight(62)
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(20, 0, 20, 0)

        # Logo
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        if os.path.exists(icon_path):
            px = QPixmap(icon_path).scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
            ico = QLabel()
            ico.setPixmap(px)
            ico.setStyleSheet("border: none; background: transparent;")
            lay.addWidget(ico)
            lay.addSpacing(8)

        name_lbl = _lbl(None, "PlatAlgo", FG, 15, True)
        relay_lbl = _lbl(None, " Relay", FG_SOFT, 13)
        lay.addWidget(name_lbl)
        lay.addWidget(relay_lbl)
        lay.addStretch(1)

        # Status pills
        for name in ["Bridge", "MT5", "Broker"]:
            pill = QFrame()
            pill.setStyleSheet(
                f"QFrame {{ background-color: {GLASS}; border-radius: 14px; "
                f"border: 1px solid {BORDER}; }}"
            )
            p_lay = QHBoxLayout(pill)
            p_lay.setContentsMargins(10, 4, 12, 4)
            p_lay.setSpacing(4)

            dot = QLabel("●")
            dot.setStyleSheet(f"color: {DANGER}; font-size: 9px; border: none; background: transparent;")
            txt = QLabel(f" {name}: Offline")
            txt.setStyleSheet(f"color: {FG_SOFT}; font-size: 10px; border: none; background: transparent;")

            p_lay.addWidget(dot)
            p_lay.addWidget(txt)
            lay.addWidget(pill)
            lay.addSpacing(6)

            self._header_dots[name] = (dot, txt)

        lay.addSpacing(8)

        # OFFLINE pill
        self._status_pill = QLabel("● OFFLINE / Idle")
        self._status_pill.setStyleSheet(
            f"background-color: {GLASS}; color: {FG_SOFT}; border-radius: 12px; "
            f"padding: 5px 12px; font-size: 10px; font-weight: bold; "
            f"border: 1px solid {BORDER};"
        )
        lay.addWidget(self._status_pill)
        lay.addSpacing(12)

        # Avatar
        self._avatar = QLabel("--")
        self._avatar.setFixedSize(34, 34)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setStyleSheet(
            f"background-color: {GLASS_GOLD}; color: {GOLD_LT}; border-radius: 17px; "
            f"font-size: 11px; font-weight: bold; border: none;"
        )
        lay.addWidget(self._avatar)

        parent_layout.addWidget(hdr)

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent_layout: QHBoxLayout):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        s_lay = QVBoxLayout(sidebar)
        s_lay.setContentsMargins(10, 14, 10, 14)
        s_lay.setSpacing(2)

        nav_items = [
            ("connect",      "⊕  Connect"),
            ("dashboard",    "⊞  Dashboard"),
            ("tradingview",  "◎  TradingView"),
            ("instructions", "◑  Guide"),
            ("settings",     "◈  Settings"),
        ]

        for key, label in nav_items:
            btn = QPushButton(label)
            btn.setObjectName("navBtnActive" if key == "connect" else "navBtn")
            btn.setFixedHeight(44)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _, k=key: self._switch_panel(k))
            s_lay.addWidget(btn)
            self._nav_btns[key] = btn

        # Separator
        s_lay.addSpacing(12)
        s_lay.addWidget(_hline())
        s_lay.addSpacing(10)

        # VPS status
        self.vps_status_lbl = _lbl(None, "● VPS INACTIVE", FG_FAINT, 10)
        s_lay.addWidget(self.vps_status_lbl)

        s_lay.addStretch(1)

        # Version
        s_lay.addWidget(_lbl(None, f"v{APP_VERSION}", FG_FAINT, 10))
        s_lay.addWidget(_lbl(None, "PlatAlgo Relay", FG_FAINT, 9))

        parent_layout.addWidget(sidebar)

    # ── Content (stacked) ─────────────────────────────────────────────────────
    def _build_content(self, parent_layout: QHBoxLayout):
        self._stacked = QStackedWidget()
        self._stacked.setStyleSheet(f"background-color: {BG};")

        panels = [
            ("connect",      self._build_connect_panel),
            ("dashboard",    self._build_dashboard_panel),
            ("tradingview",  self._build_tradingview_panel),
            ("instructions", self._build_guide_panel),
            ("settings",     self._build_settings_panel),
        ]
        for key, builder in panels:
            w = builder()
            idx = self._stacked.addWidget(w)
            self._panels[key]    = w
            self._panel_idx[key] = idx

        parent_layout.addWidget(self._stacked, 1)

    def _switch_panel(self, key: str):
        if key == self._current_panel:
            return
        self._current_panel = key
        self._stacked.setCurrentIndex(self._panel_idx[key])
        for k, btn in self._nav_btns.items():
            btn.setObjectName("navBtnActive" if k == key else "navBtn")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if key == "tradingview":
            QTimer.singleShot(10, self._update_tv_preview)

    # =========================================================================
    # CONNECT PANEL
    # =========================================================================
    def _build_connect_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background-color: {BG};")

        inner = QWidget()
        inner.setStyleSheet(f"background-color: {BG};")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(40, 32, 40, 32)
        lay.setSpacing(0)
        scroll.setWidget(inner)

        # Page title
        lay.addWidget(_lbl(None, "Connect", FG, 28, True))
        lay.addSpacing(6)
        lay.addWidget(_lbl(None, "Sign in and configure your trading bridge execution method.",
                           FG_MUTED, 12))
        lay.addSpacing(24)

        # Two-column
        cols = QWidget()
        cols.setStyleSheet(f"background-color: {BG};")
        c_lay = QHBoxLayout(cols)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(20)
        lay.addWidget(cols)

        # LEFT col
        left_w = QWidget()
        left_w.setStyleSheet(f"background-color: {BG};")
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(16)
        c_lay.addWidget(left_w, 1)

        # Sign In card
        self._build_signin_card(left_lay)

        # MT5 card
        self._build_mt5_card(left_lay)
        left_lay.addStretch(1)

        # RIGHT col
        right_w = QWidget()
        right_w.setStyleSheet(f"background-color: {BG};")
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(16)
        c_lay.addWidget(right_w, 1)

        right_lay.addWidget(_lbl(None, "Execution Mode", FG, 28, True))
        right_lay.addSpacing(4)
        right_lay.addWidget(_lbl(None, "Choose how your signals reach the broker.", FG_MUTED, 12))
        right_lay.addSpacing(16)

        # VPS card
        self._build_vps_card(right_lay)

        # Local card
        self._build_local_card(right_lay)
        right_lay.addStretch(1)

        return scroll

    def _build_signin_card(self, parent_lay: QVBoxLayout):
        card = _card()
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(28, 28, 28, 28)
        c_lay.setSpacing(0)

        c_lay.addWidget(_lbl(None, "Sign In", FG, 24, True))
        c_lay.addSpacing(4)
        c_lay.addWidget(_lbl(None, "Access your PlatAlgo dashboard", FG_MUTED, 11))
        c_lay.addSpacing(20)

        # Login form (hideable)
        self._login_form = QWidget()
        self._login_form.setStyleSheet("background: transparent;")
        lf_lay = QVBoxLayout(self._login_form)
        lf_lay.setContentsMargins(0, 0, 0, 0)
        lf_lay.setSpacing(0)

        # OAuth buttons row
        oauth_row = QWidget()
        oauth_row.setStyleSheet("background: transparent;")
        or_lay = QHBoxLayout(oauth_row)
        or_lay.setContentsMargins(0, 0, 0, 16)
        or_lay.setSpacing(10)

        g_btn = QPushButton("🌐  Google")
        g_btn.setObjectName("oauthBtn")
        g_btn.setFixedHeight(44)
        g_btn.clicked.connect(lambda: self._open_oauth("google"))

        f_btn = QPushButton("f  Facebook")
        f_btn.setObjectName("oauthBtn")
        f_btn.setFixedHeight(44)
        f_btn.clicked.connect(lambda: self._open_oauth("facebook"))

        or_lay.addWidget(g_btn)
        or_lay.addWidget(f_btn)
        lf_lay.addWidget(oauth_row)

        # OR divider
        div_w = QWidget()
        div_w.setStyleSheet("background: transparent;")
        div_lay = QHBoxLayout(div_w)
        div_lay.setContentsMargins(0, 0, 0, 16)
        div_lay.setSpacing(8)
        div_lay.addWidget(_hline())
        div_lay.addWidget(_lbl(None, "OR", FG_SOFT, 11, True))
        div_lay.addWidget(_hline())
        lf_lay.addWidget(div_w)

        # Email
        lf_lay.addWidget(_lbl(None, "EMAIL", FG_SOFT, 10, True))
        lf_lay.addSpacing(5)
        self.user_entry = _entry("you@example.com")
        lf_lay.addWidget(self.user_entry)
        lf_lay.addSpacing(12)

        # Password
        lf_lay.addWidget(_lbl(None, "PASSWORD", FG_SOFT, 10, True))
        lf_lay.addSpacing(5)
        self.pass_entry = _entry("••••••••", password=True)
        lf_lay.addWidget(self.pass_entry)
        lf_lay.addSpacing(14)

        # Checkboxes
        cb_row = QWidget()
        cb_row.setStyleSheet("background: transparent;")
        cb_lay = QHBoxLayout(cb_row)
        cb_lay.setContentsMargins(0, 0, 0, 16)
        cb_lay.setSpacing(16)

        self.remember_cb = QCheckBox("Remember me")
        self.startup_cb  = QCheckBox("Launch on startup")
        self.startup_cb.toggled.connect(self._toggle_startup)
        cb_lay.addWidget(self.remember_cb)
        cb_lay.addWidget(self.startup_cb)
        cb_lay.addStretch()
        lf_lay.addWidget(cb_row)

        # Sign In button
        sign_in_btn = QPushButton("Sign In  →")
        sign_in_btn.setObjectName("goldBtn")
        sign_in_btn.setFixedHeight(52)
        sign_in_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sign_in_btn.clicked.connect(self._sign_in)
        lf_lay.addWidget(sign_in_btn)

        c_lay.addWidget(self._login_form)

        # OAuth logged-in frame (hidden initially)
        self._oauth_frame = QWidget()
        self._oauth_frame.setStyleSheet("background: transparent;")
        self._oauth_frame.hide()
        of_lay = QVBoxLayout(self._oauth_frame)
        of_lay.setContentsMargins(0, 0, 0, 0)
        of_lay.setSpacing(0)

        olf_inner = QFrame()
        olf_inner.setStyleSheet(
            f"QFrame {{ background-color: {GLASS}; border-radius: 12px; "
            f"border: 1px solid {BORDER_SOFT}; }}"
        )
        olf_lay = QHBoxLayout(olf_inner)
        olf_lay.setContentsMargins(16, 16, 16, 16)
        olf_lay.setSpacing(12)

        check_lbl = QLabel("✓")
        check_lbl.setFixedSize(40, 40)
        check_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        check_lbl.setStyleSheet(
            f"background-color: {SUCCESS_BG}; color: {GREEN}; "
            f"border-radius: 20px; font-size: 18px; font-weight: bold; border: none;"
        )
        olf_lay.addWidget(check_lbl)

        olf_text = QWidget()
        olf_text.setStyleSheet("background: transparent;")
        olt_lay = QVBoxLayout(olf_text)
        olt_lay.setContentsMargins(0, 0, 0, 0)
        olt_lay.setSpacing(2)

        self._oauth_prov_lbl = _lbl(None, "Signed in via Google", FG, 13, True)
        self._oauth_user_lbl = _lbl(None, "—", FG_MUTED, 11)
        olt_lay.addWidget(self._oauth_prov_lbl)
        olt_lay.addWidget(self._oauth_user_lbl)
        olf_lay.addWidget(olf_text, 1)
        of_lay.addWidget(olf_inner)
        of_lay.addSpacing(12)

        sign_out_btn = QPushButton("Sign out / Switch account")
        sign_out_btn.setObjectName("outlineBtn")
        sign_out_btn.setFixedHeight(40)
        sign_out_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sign_out_btn.clicked.connect(self._sign_out_oauth)
        of_lay.addWidget(sign_out_btn)

        c_lay.addWidget(self._oauth_frame)
        parent_lay.addWidget(card)

    def _build_mt5_card(self, parent_lay: QVBoxLayout):
        card = _card()
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(28, 24, 28, 24)
        c_lay.setSpacing(0)

        # Header row
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        h_lay = QHBoxLayout(hdr)
        h_lay.setContentsMargins(0, 0, 0, 0)
        h_lay.setSpacing(10)

        lock = QLabel("🔒")
        lock.setStyleSheet("font-size: 16px; border: none; background: transparent;")
        h_lay.addWidget(lock)

        title_col = QWidget()
        title_col.setStyleSheet("background: transparent;")
        tc_lay = QVBoxLayout(title_col)
        tc_lay.setContentsMargins(0, 0, 0, 0)
        tc_lay.setSpacing(1)
        tc_lay.addWidget(_lbl(None, "MT5 Broker Login", FG, 16, True))
        tc_lay.addWidget(_lbl(None, "Credentials encrypted at rest", FG_MUTED, 10))
        h_lay.addWidget(title_col, 1)

        optional = _chip_label("OPTIONAL", GLASS, FG_SOFT)
        h_lay.addWidget(optional)
        c_lay.addWidget(hdr)
        c_lay.addSpacing(16)
        c_lay.addWidget(_hline())
        c_lay.addSpacing(16)

        self.mt5_acct_edit   = _entry("Account Number")
        self.mt5_pw_edit     = _entry("MT5 Password", password=True)
        self.mt5_server_edit = _entry("Broker Server  (e.g. ICMarkets-Live01)")

        c_lay.addWidget(self.mt5_acct_edit)
        c_lay.addSpacing(10)
        c_lay.addWidget(self.mt5_pw_edit)
        c_lay.addSpacing(10)
        c_lay.addWidget(self.mt5_server_edit)
        c_lay.addSpacing(16)

        mt5_connect_btn = QPushButton("Login to MT5 on VPS  →")
        mt5_connect_btn.setFixedHeight(48)
        mt5_connect_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        mt5_connect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {GOLD};
                color: #0A0600;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: bold;
                padding: 0px 20px;
            }}
            QPushButton:hover {{ background-color: {GOLD_LT}; }}
            QPushButton:disabled {{ background-color: {GOLD_DK}; color: #92400E; }}
        """)
        mt5_connect_btn.clicked.connect(self.enable_managed_mode)
        c_lay.addWidget(mt5_connect_btn)

        parent_lay.addWidget(card)

    def _build_vps_card(self, parent_lay: QVBoxLayout):
        card = _card(gold=True)
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(28, 24, 28, 24)
        c_lay.setSpacing(0)
        self.vps_card_frame = card

        # Title row
        tr = QWidget()
        tr.setStyleSheet("background: transparent;")
        tr_lay = QHBoxLayout(tr)
        tr_lay.setContentsMargins(0, 0, 0, 0)
        tr_lay.setSpacing(12)

        icon_sq = QFrame()
        icon_sq.setFixedSize(40, 40)
        icon_sq.setStyleSheet(
            f"QFrame {{ background-color: {GLASS_GOLD}; border-radius: 10px; border: none; }}"
        )
        isq_lay = QVBoxLayout(icon_sq)
        isq_lay.setContentsMargins(0, 0, 0, 0)
        i_lbl = QLabel("⊟")
        i_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        i_lbl.setStyleSheet(f"color: {GOLD_LT}; font-size: 18px; border: none; background: transparent;")
        isq_lay.addWidget(i_lbl)
        tr_lay.addWidget(icon_sq)

        tc = QWidget()
        tc.setStyleSheet("background: transparent;")
        tc_lay = QVBoxLayout(tc)
        tc_lay.setContentsMargins(0, 0, 0, 0)
        tc_lay.setSpacing(2)
        tc_lay.addWidget(_lbl(None, "VPS Execution", FG, 15, True))
        tc_lay.addWidget(_lbl(None, "Cloud-hosted, always-on bridge", FG_MUTED, 11))
        tr_lay.addWidget(tc, 1)

        rec = _chip_label("★  RECOMMENDED", GOLD, "#0A0600")
        tr_lay.addWidget(rec)
        c_lay.addWidget(tr)
        c_lay.addSpacing(18)

        # Bullets grid
        bg = QWidget()
        bg.setStyleSheet("background: transparent;")
        bg_lay = QGridLayout(bg)
        bg_lay.setContentsMargins(0, 0, 0, 0)
        bg_lay.setSpacing(6)

        bullets = [
            "Trades 24 hrs, 7 days a week",
            "No MT5 required on this machine",
            "Works on Mac, Windows, any device",
            "DDoS protection & auto updates",
            "Sub-millisecond execution",
            "Dedicated server resources",
        ]
        for i, txt in enumerate(bullets):
            r, col = divmod(i, 2)
            bw = QWidget()
            bw.setStyleSheet("background: transparent;")
            bw_lay = QHBoxLayout(bw)
            bw_lay.setContentsMargins(0, 0, 0, 0)
            bw_lay.setSpacing(6)
            bw_lay.addWidget(_lbl(None, "✓", GREEN, 11, True))
            bw_lay.addWidget(_lbl(None, txt, FG_MUTED, 11))
            bw_lay.addStretch()
            bg_lay.addWidget(bw, r, col)
        c_lay.addWidget(bg)
        c_lay.addSpacing(18)

        # Buttons
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(10)

        self.vps_btn = QPushButton("Login to MT5 on VPS  →")
        self.vps_btn.setMinimumHeight(48)
        self.vps_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.vps_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {GOLD};
                color: #0A0600;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: bold;
                padding: 0px 20px;
            }}
            QPushButton:hover {{ background-color: {GOLD_LT}; }}
            QPushButton:disabled {{ background-color: {GOLD_DK}; color: #92400E; }}
        """)
        self.vps_btn.clicked.connect(self.enable_managed_mode)

        self.vps_disable_btn = QPushButton("Disconnect")
        self.vps_disable_btn.setMinimumHeight(48)
        self.vps_disable_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {FG_MUTED};
                border: 1px solid {BORDER};
                border-radius: 10px;
                font-size: 13px;
                padding: 0px 16px;
            }}
            QPushButton:hover {{ background-color: {GREEN_HOVER}; border-color: {GREEN}; }}
        """)
        self.vps_disable_btn.clicked.connect(self.disable_managed_mode)

        br_lay.addWidget(self.vps_btn, 1)
        br_lay.addWidget(self.vps_disable_btn)
        c_lay.addWidget(btn_row)

        parent_lay.addWidget(card)

    def _build_local_card(self, parent_lay: QVBoxLayout):
        card = _card()
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(28, 24, 28, 24)
        c_lay.setSpacing(0)

        tr = QWidget()
        tr.setStyleSheet("background: transparent;")
        tr_lay = QHBoxLayout(tr)
        tr_lay.setContentsMargins(0, 0, 0, 0)
        tr_lay.setSpacing(12)

        icon_sq = QFrame()
        icon_sq.setFixedSize(40, 40)
        icon_sq.setStyleSheet(
            f"QFrame {{ background-color: {GLASS}; border-radius: 10px; border: none; }}"
        )
        isq_lay = QVBoxLayout(icon_sq)
        isq_lay.setContentsMargins(0, 0, 0, 0)
        il = QLabel("⬡")
        il.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.setStyleSheet(f"color: {FG_MUTED}; font-size: 18px; border: none; background: transparent;")
        isq_lay.addWidget(il)
        tr_lay.addWidget(icon_sq)

        tc = QWidget()
        tc.setStyleSheet("background: transparent;")
        tc_lay = QVBoxLayout(tc)
        tc_lay.setContentsMargins(0, 0, 0, 0)
        tc_lay.setSpacing(2)
        tc_lay.addWidget(_lbl(None, "Local Mode", FG, 15, True))
        tc_lay.addWidget(_lbl(None, "Connect directly to your machine", FG_MUTED, 11))
        tr_lay.addWidget(tc, 1)

        win_badge = _chip_label("WINDOWS ONLY", GLASS, FG_SOFT)
        tr_lay.addWidget(win_badge)
        c_lay.addWidget(tr)
        c_lay.addSpacing(18)

        # Bullets
        bg = QWidget()
        bg.setStyleSheet("background: transparent;")
        bg_lay = QGridLayout(bg)
        bg_lay.setContentsMargins(0, 0, 0, 0)
        bg_lay.setSpacing(6)

        loc_bullets = [
            (GREEN,  "Low latency — direct connection"),
            (GREEN,  "Full environment control"),
            (DANGER, "Requires MT5 open on this machine"),
            (DANGER, "Stops when computer sleeps or closes"),
        ]
        icons = ["✓", "✓", "✗", "✗"]
        for i, (color, txt) in enumerate(loc_bullets):
            r, col = divmod(i, 2)
            bw = QWidget()
            bw.setStyleSheet("background: transparent;")
            bw_lay = QHBoxLayout(bw)
            bw_lay.setContentsMargins(0, 0, 0, 0)
            bw_lay.setSpacing(6)
            bw_lay.addWidget(_lbl(None, icons[i], color, 11, True))
            bw_lay.addWidget(_lbl(None, txt, FG_MUTED, 11))
            bw_lay.addStretch()
            bg_lay.addWidget(bw, r, col)
        c_lay.addWidget(bg)
        c_lay.addSpacing(16)
        c_lay.addWidget(_hline())
        c_lay.addSpacing(16)

        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(10)

        self.connect_btn = QPushButton("Select Local Mode" if IS_WINDOWS else "Windows Only")
        self.connect_btn.setObjectName("outlineBtn")
        self.connect_btn.setFixedHeight(44)
        self.connect_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if IS_WINDOWS:
            self.connect_btn.clicked.connect(self.start_relay)
        else:
            self.connect_btn.setEnabled(False)

        stop_btn = QPushButton("Stop")
        stop_btn.setObjectName("dangerBtn")
        stop_btn.setFixedHeight(44)
        stop_btn.clicked.connect(self.stop_relay)

        br_lay.addWidget(self.connect_btn, 1)
        br_lay.addWidget(stop_btn)
        c_lay.addWidget(btn_row)

        parent_lay.addWidget(card)

    # =========================================================================
    # DASHBOARD PANEL
    # =========================================================================
    def _build_dashboard_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background-color: {BG};")

        inner = QWidget()
        inner.setStyleSheet(f"background-color: {BG};")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(0)
        scroll.setWidget(inner)

        # Header
        hdr_row = QWidget()
        hdr_row.setStyleSheet("background: transparent;")
        hr_lay = QHBoxLayout(hdr_row)
        hr_lay.setContentsMargins(0, 0, 0, 0)
        hr_lay.addWidget(_lbl(None, "Dashboard", FG, 28, True))
        hr_lay.addStretch()

        ref_btn = QPushButton("↺  Refresh")
        ref_btn.setObjectName("smallBtn")
        ref_btn.setFixedHeight(36)
        ref_btn.clicked.connect(self._do_refresh)
        hr_lay.addWidget(ref_btn)
        hr_lay.addSpacing(8)

        web_btn = QPushButton("Open Web Dashboard")
        web_btn.setObjectName("smallBtn")
        web_btn.setFixedHeight(36)
        web_btn.clicked.connect(lambda: webbrowser.open(
            self.bridge_entry.text().rstrip("/") + "/dashboard" if self.bridge_entry else "#"
        ))
        hr_lay.addWidget(web_btn)
        lay.addWidget(hdr_row)
        lay.addSpacing(6)
        lay.addWidget(_lbl(None, "Live connection state, webhook URL, and API key.", FG_MUTED, 12))
        lay.addSpacing(24)

        # Status ring cards
        rings_row = QWidget()
        rings_row.setStyleSheet("background: transparent;")
        rr_lay = QHBoxLayout(rings_row)
        rr_lay.setContentsMargins(0, 0, 0, 0)
        rr_lay.setSpacing(12)

        conn_meta = {
            "Bridge": ("Cloud server",  "Routes signals to MT5"),
            "MT5":    ("MT5 terminal",  "Executes trade orders"),
            "Broker": ("Broker server", "Confirms fills & balance"),
        }
        for name, (subtitle, desc) in conn_meta.items():
            ring_card = QFrame()
            ring_card.setObjectName("card")
            rc_lay = QVBoxLayout(ring_card)
            rc_lay.setContentsMargins(20, 28, 20, 24)
            rc_lay.setSpacing(0)
            rc_lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            # Ring
            ring = QFrame()
            ring.setFixedSize(84, 84)
            ring.setStyleSheet(
                f"QFrame {{ border-radius: 42px; border: 7px solid {DANGER}; "
                f"background-color: {DANGER_BG}; }}"
            )
            rc_lay.addWidget(ring, 0, Qt.AlignmentFlag.AlignHCenter)

            letter = QLabel("—")
            letter.setAlignment(Qt.AlignmentFlag.AlignCenter)
            letter.setGeometry(0, 0, 84, 84)
            letter.setParent(ring)
            letter.setStyleSheet(
                f"color: {DANGER}; font-size: 16px; font-weight: bold; "
                f"background: transparent; border: none;"
            )
            letter.setGeometry(0, 0, 84, 84)

            rc_lay.addSpacing(12)
            rc_lay.addWidget(_lbl(None, name, FG, 13, True), 0, Qt.AlignmentFlag.AlignHCenter)
            rc_lay.addSpacing(2)
            rc_lay.addWidget(_lbl(None, subtitle, FG_SOFT, 10), 0, Qt.AlignmentFlag.AlignHCenter)
            rc_lay.addSpacing(4)

            status_lbl = _lbl(None, "Offline", DANGER, 10, True)
            rc_lay.addWidget(status_lbl, 0, Qt.AlignmentFlag.AlignHCenter)
            rc_lay.addSpacing(2)
            rc_lay.addWidget(_lbl(None, desc, FG_FAINT, 9, wrap=True), 0, Qt.AlignmentFlag.AlignHCenter)

            rr_lay.addWidget(ring_card, 1)

            self._status_rings[name]   = ring
            self._ring_letters[name]   = letter
            self._ring_status_lbl[name] = status_lbl

        lay.addWidget(rings_row)
        lay.addSpacing(24)

        # Webhook URL card
        wh_card = QFrame()
        wh_card.setObjectName("goldCard")
        wh_lay = QVBoxLayout(wh_card)
        wh_lay.setContentsMargins(20, 20, 20, 20)
        wh_lay.setSpacing(6)

        wh_hdr = QWidget()
        wh_hdr.setStyleSheet("background: transparent;")
        whh_lay = QHBoxLayout(wh_hdr)
        whh_lay.setContentsMargins(0, 0, 0, 0)
        whh_lay.addWidget(_lbl(None, "Webhook URL", FG, 13, True))
        whh_lay.addStretch()
        whh_lay.addWidget(_chip_label("PASTE INTO TRADINGVIEW", GLASS_EM, GREEN_LT))
        wh_lay.addWidget(wh_hdr)
        wh_lay.addWidget(_lbl(None,
            "Paste this URL into TradingView alert → Notifications → Webhook URL",
            FG_MUTED, 11))

        url_row = QWidget()
        url_row.setStyleSheet("background: transparent;")
        url_lay = QHBoxLayout(url_row)
        url_lay.setContentsMargins(0, 0, 0, 0)
        url_lay.setSpacing(10)

        self.webhook_entry = QLineEdit()
        self.webhook_entry.setPlaceholderText("Sign in to view your webhook URL")
        self.webhook_entry.setReadOnly(True)
        self.webhook_entry.setFixedHeight(46)

        self._webhook_copy_btn = QPushButton("Copy")
        self._webhook_copy_btn.setObjectName("smallBtn")
        self._webhook_copy_btn.setFixedSize(80, 46)
        self._webhook_copy_btn.clicked.connect(lambda: self._copy_to_clipboard(
            self.webhook_entry.text(), self._webhook_copy_btn))

        url_lay.addWidget(self.webhook_entry, 1)
        url_lay.addWidget(self._webhook_copy_btn)
        wh_lay.addWidget(url_row)
        lay.addWidget(wh_card)
        lay.addSpacing(16)

        # API key card
        ak_card = QFrame()
        ak_card.setObjectName("card")
        ak_lay = QVBoxLayout(ak_card)
        ak_lay.setContentsMargins(20, 20, 20, 20)
        ak_lay.setSpacing(6)

        ak_hdr = QWidget()
        ak_hdr.setStyleSheet("background: transparent;")
        akh_lay = QHBoxLayout(ak_hdr)
        akh_lay.setContentsMargins(0, 0, 0, 0)
        akh_lay.addWidget(_lbl(None, "API Key", FG, 13, True))
        akh_lay.addStretch()
        akh_lay.addWidget(_chip_label("KEEP SECRET", DANGER_BG, DANGER))
        ak_lay.addWidget(ak_hdr)
        ak_lay.addWidget(_lbl(None, "Use this key in webhooks instead of your password.", FG_MUTED, 11))

        ak_row = QWidget()
        ak_row.setStyleSheet("background: transparent;")
        akr_lay = QHBoxLayout(ak_row)
        akr_lay.setContentsMargins(0, 0, 0, 0)
        akr_lay.setSpacing(8)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("—")
        self.api_key_edit.setReadOnly(True)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setFixedHeight(46)

        show_btn = QPushButton("Show")
        show_btn.setObjectName("smallBtn")
        show_btn.setFixedHeight(46)
        show_btn.clicked.connect(self._toggle_api_key_reveal)

        copy_ak_btn = QPushButton("Copy")
        copy_ak_btn.setObjectName("smallBtn")
        copy_ak_btn.setFixedHeight(46)
        copy_ak_btn.clicked.connect(lambda: self._copy_to_clipboard(self.api_key_edit.text()))

        akr_lay.addWidget(self.api_key_edit, 1)
        akr_lay.addWidget(show_btn)
        akr_lay.addWidget(copy_ak_btn)
        ak_lay.addWidget(ak_row)
        lay.addWidget(ak_card)
        lay.addSpacing(16)

        # Summary card
        sum_card = QFrame()
        sum_card.setObjectName("card")
        sc_lay = QVBoxLayout(sum_card)
        sc_lay.setContentsMargins(20, 20, 20, 20)
        sc_lay.setSpacing(8)
        sc_lay.addWidget(_lbl(None, "Account Summary", FG, 13, True))

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFixedHeight(180)
        self.summary_text.setPlaceholderText("Sign in and refresh to load summary…")
        sc_lay.addWidget(self.summary_text)
        lay.addWidget(sum_card)
        lay.addStretch()

        return scroll

    # =========================================================================
    # TRADINGVIEW PANEL
    # =========================================================================
    def _build_tradingview_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background-color: {BG};")

        inner = QWidget()
        inner.setStyleSheet(f"background-color: {BG};")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(40, 32, 40, 32)
        lay.setSpacing(0)
        scroll.setWidget(inner)

        lay.addWidget(_lbl(None, "TradingView", FG, 28, True))
        lay.addSpacing(6)
        lay.addWidget(_lbl(None, "Generate alert message JSON for your TradingView webhooks.", FG_MUTED, 12))
        lay.addSpacing(24)

        card = _card()
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(28, 28, 28, 28)
        c_lay.setSpacing(0)

        # BUY / SELL toggle
        act_row = QWidget()
        act_row.setStyleSheet("background: transparent;")
        ar_lay = QHBoxLayout(act_row)
        ar_lay.setContentsMargins(0, 0, 0, 20)
        ar_lay.setSpacing(10)
        ar_lay.addWidget(_lbl(None, "Action", FG, 13, True))
        ar_lay.addStretch()

        self._buy_btn = QPushButton("BUY")
        self._buy_btn.setObjectName("buyBtnActive")
        self._buy_btn.setFixedHeight(36)
        self._buy_btn.clicked.connect(lambda: self._set_tv_action("BUY"))

        self._sell_btn = QPushButton("SELL")
        self._sell_btn.setObjectName("sellBtn")
        self._sell_btn.setFixedHeight(36)
        self._sell_btn.clicked.connect(lambda: self._set_tv_action("SELL"))

        ar_lay.addWidget(self._buy_btn)
        ar_lay.addWidget(self._sell_btn)
        c_lay.addWidget(act_row)

        # Fields grid
        grid_w = QWidget()
        grid_w.setStyleSheet("background: transparent;")
        grid_lay = QGridLayout(grid_w)
        grid_lay.setContentsMargins(0, 0, 0, 20)
        grid_lay.setSpacing(12)

        fields = [
            ("Symbol",      "{{ticker}}",  0, 0),
            ("Size (lots)", "0.1",         0, 1),
            ("Stop Loss",   "0.0",         1, 0),
            ("Take Profit", "0.0",         1, 1),
            ("Script Name", "",            2, 0),
        ]
        self.tv_symbol_edit = _entry("{{ticker}}")
        self.tv_symbol_edit.setText("{{ticker}}")
        self.tv_size_edit   = _entry("0.1")
        self.tv_size_edit.setText("0.1")
        self.tv_sl_edit     = _entry("0.0")
        self.tv_tp_edit     = _entry("0.0")
        self.tv_script_edit = _entry("")

        widgets = [self.tv_symbol_edit, self.tv_size_edit, self.tv_sl_edit,
                   self.tv_tp_edit, self.tv_script_edit]

        for (label, placeholder, row, col), widget in zip(fields, widgets):
            wrap = QWidget()
            wrap.setStyleSheet("background: transparent;")
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(5)
            wl.addWidget(_lbl(None, label.upper(), FG_SOFT, 10, True))
            wl.addWidget(widget)
            grid_lay.addWidget(wrap, row, col)

        c_lay.addWidget(grid_w)

        # Preview
        c_lay.addWidget(_lbl(None, "JSON PREVIEW", FG_SOFT, 10, True))
        c_lay.addSpacing(8)

        self.tv_preview = QTextEdit()
        self.tv_preview.setReadOnly(True)
        self.tv_preview.setFixedHeight(160)
        c_lay.addWidget(self.tv_preview)
        c_lay.addSpacing(14)

        # Buttons
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(10)

        copy_btn = QPushButton("Copy Message")
        copy_btn.setObjectName("goldBtn")
        copy_btn.setFixedHeight(44)
        copy_btn.clicked.connect(self._copy_tv_message)

        reset_btn = QPushButton("Reset")
        reset_btn.setObjectName("outlineBtn")
        reset_btn.setFixedHeight(44)
        reset_btn.clicked.connect(self._reset_tv_fields)

        br_lay.addWidget(copy_btn, 1)
        br_lay.addWidget(reset_btn)
        c_lay.addWidget(btn_row)

        lay.addWidget(card)
        lay.addStretch()

        # Connect live preview
        for w in [self.tv_symbol_edit, self.tv_size_edit, self.tv_sl_edit,
                  self.tv_tp_edit, self.tv_script_edit]:
            w.textChanged.connect(lambda _: self._update_tv_preview())

        return scroll

    # =========================================================================
    # GUIDE PANEL
    # =========================================================================
    def _build_guide_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background-color: {BG};")

        inner = QWidget()
        inner.setStyleSheet(f"background-color: {BG};")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(40, 32, 40, 40)
        lay.setSpacing(16)
        scroll.setWidget(inner)

        lay.addWidget(_lbl(None, "Guide", FG, 28, True))
        lay.addWidget(_lbl(None, "Step-by-step instructions to get trading in minutes.", FG_MUTED, 12))

        steps = [
            ("1", "Sign In", [
                "Click 'Connect' in the sidebar.",
                "Use Google or Facebook OAuth (recommended), or enter email + password.",
                "Your session is stored securely — you won't need to sign in again.",
            ]),
            ("2", "Choose Execution Mode", [
                "VPS Mode: The cloud server executes trades 24/7, even when your PC is off.",
                "Local Mode: PlatAlgo connects to MT5 on this machine directly (Windows only).",
                "VPS mode is recommended for reliability.",
            ]),
            ("3", "Enter MT5 Credentials (VPS Mode)", [
                "Fill in Account Number, MT5 Password, and Broker Server in the MT5 card.",
                "Click 'Login to MT5 on VPS' — credentials are encrypted before being sent.",
                "The VPS will initialize and begin monitoring for TradingView signals.",
            ]),
            ("4", "Configure TradingView Alert", [
                "Go to the Dashboard panel and copy your Webhook URL.",
                "In TradingView, create an alert → Notifications → Webhook URL.",
                "Use the TradingView panel to generate the correct JSON message format.",
                "Paste the generated JSON into the TradingView alert message box.",
            ]),
            ("5", "Monitor on Dashboard", [
                "The Dashboard shows live Bridge, MT5, and Broker connection status.",
                "Green ring = connected and healthy.  Red ring = disconnected.",
                "Check the Account Summary for signal and execution counts.",
            ]),
        ]

        for num, title, sub_steps in steps:
            step_card = QFrame()
            step_card.setObjectName("card")
            sc_lay = QVBoxLayout(step_card)
            sc_lay.setContentsMargins(24, 20, 24, 20)
            sc_lay.setSpacing(8)

            hdr_row = QWidget()
            hdr_row.setStyleSheet("background: transparent;")
            hr_lay = QHBoxLayout(hdr_row)
            hr_lay.setContentsMargins(0, 0, 0, 0)
            hr_lay.setSpacing(12)

            num_lbl = QLabel(num)
            num_lbl.setFixedSize(32, 32)
            num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num_lbl.setStyleSheet(
                f"background-color: {GREEN_BG}; color: {GREEN_LT}; "
                f"border-radius: 16px; font-size: 14px; font-weight: bold; border: none;"
            )
            hr_lay.addWidget(num_lbl)
            hr_lay.addWidget(_lbl(None, title, FG, 14, True))
            sc_lay.addWidget(hdr_row)

            for step_txt in sub_steps:
                step_row = QWidget()
                step_row.setStyleSheet("background: transparent;")
                sr_lay = QHBoxLayout(step_row)
                sr_lay.setContentsMargins(44, 0, 0, 0)
                sr_lay.setSpacing(8)
                sr_lay.addWidget(_lbl(None, "›", GREEN, 12))
                lbl = _lbl(None, step_txt, FG_MUTED, 12, wrap=True)
                sr_lay.addWidget(lbl, 1)
                sc_lay.addWidget(step_row)

            lay.addWidget(step_card)

        lay.addStretch()
        return scroll

    # =========================================================================
    # SETTINGS PANEL
    # =========================================================================
    def _build_settings_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background-color: {BG};")

        inner = QWidget()
        inner.setStyleSheet(f"background-color: {BG};")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(40, 32, 40, 32)
        lay.setSpacing(16)
        scroll.setWidget(inner)

        lay.addWidget(_lbl(None, "Settings", FG, 28, True))
        lay.addWidget(_lbl(None, "Configure bridge connection and local MT5 path.", FG_MUTED, 12))

        # Connection card
        conn_card = QFrame()
        conn_card.setObjectName("card")
        cc_lay = QVBoxLayout(conn_card)
        cc_lay.setContentsMargins(24, 24, 24, 24)
        cc_lay.setSpacing(12)
        cc_lay.addWidget(_lbl(None, "Bridge Connection", FG, 14, True))
        cc_lay.addWidget(_lbl(None, "BRIDGE URL", FG_SOFT, 10, True))

        self.bridge_entry = QLineEdit(PRODUCTION_BRIDGE_URL)
        self.bridge_entry.setFixedHeight(44)
        cc_lay.addWidget(self.bridge_entry)
        lay.addWidget(conn_card)

        # MT5 path card (Windows)
        if IS_WINDOWS:
            mt5_card = QFrame()
            mt5_card.setObjectName("card")
            mc_lay = QVBoxLayout(mt5_card)
            mc_lay.setContentsMargins(24, 24, 24, 24)
            mc_lay.setSpacing(12)
            mc_lay.addWidget(_lbl(None, "MT5 Terminal Path", FG, 14, True))
            mc_lay.addWidget(_lbl(None, "MT5 PATH", FG_SOFT, 10, True))

            path_row = QWidget()
            path_row.setStyleSheet("background: transparent;")
            pr_lay = QHBoxLayout(path_row)
            pr_lay.setContentsMargins(0, 0, 0, 0)
            pr_lay.setSpacing(8)

            self.mt5_path_edit = QLineEdit(detect_mt5_path())
            self.mt5_path_edit.setFixedHeight(44)
            pr_lay.addWidget(self.mt5_path_edit, 1)

            browse_btn = QPushButton("Browse")
            browse_btn.setObjectName("smallBtn")
            browse_btn.setFixedHeight(44)
            browse_btn.clicked.connect(self._browse_mt5_path)
            pr_lay.addWidget(browse_btn)
            mc_lay.addWidget(path_row)
            lay.addWidget(mt5_card)

        # Log card
        log_card = QFrame()
        log_card.setObjectName("card")
        lc_lay = QVBoxLayout(log_card)
        lc_lay.setContentsMargins(24, 24, 24, 24)
        lc_lay.setSpacing(10)

        log_hdr = QWidget()
        log_hdr.setStyleSheet("background: transparent;")
        lh_lay = QHBoxLayout(log_hdr)
        lh_lay.setContentsMargins(0, 0, 0, 0)
        lh_lay.addWidget(_lbl(None, "Relay Log", FG, 14, True))
        lh_lay.addStretch()

        clr_btn = QPushButton("Clear")
        clr_btn.setObjectName("smallBtn")
        clr_btn.setFixedHeight(32)
        clr_btn.clicked.connect(self._clear_logs)
        lh_lay.addWidget(clr_btn)
        lc_lay.addWidget(log_hdr)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(240)
        lc_lay.addWidget(self.log_box)
        lay.addWidget(log_card)
        lay.addStretch()

        return scroll

    # =========================================================================
    # SIGNAL SLOTS (main thread)
    # =========================================================================
    def _on_status_changed(self, text: str):
        if self._status_pill:
            self._status_pill.setText(f"● {text}")

    def _on_log_appended(self, text: str):
        if self.log_box:
            self.log_box.append(text)

    def _on_dot_changed(self, name: str, online: bool):
        color    = GREEN    if online else DANGER
        bg_color = SUCCESS_BG if online else DANGER_BG
        text     = "Online" if online else "Offline"
        letter   = "●"     if online else "—"

        if name in self._status_rings:
            self._status_rings[name].setStyleSheet(
                f"QFrame {{ border-radius: 42px; border: 7px solid {color}; "
                f"background-color: {bg_color}; }}"
            )
        if name in self._ring_letters:
            self._ring_letters[name].setText(letter)
            self._ring_letters[name].setStyleSheet(
                f"color: {color}; font-size: 16px; font-weight: bold; "
                f"background: transparent; border: none;"
            )
        if name in self._ring_status_lbl:
            self._ring_status_lbl[name].setText(text)
            self._ring_status_lbl[name].setStyleSheet(
                f"color: {color}; font-size: 10px; font-weight: bold; "
                f"background: transparent; border: none;"
            )
        if name in self._header_dots:
            dot_lbl, text_lbl = self._header_dots[name]
            dot_lbl.setStyleSheet(f"color: {color}; font-size: 9px; border: none; background: transparent;")
            text_lbl.setText(f" {name}: {text}")

    def _on_summary_updated(self, text: str):
        if self.summary_text:
            self.summary_text.setPlainText(text)

    def _on_oauth_success(self, provider: str, uid: str, from_cache: bool):
        self._oauth_provider = provider
        if not from_cache:
            self._save_oauth_credentials(uid, provider)
        if self._oauth_prov_lbl:
            self._oauth_prov_lbl.setText(f"Signed in via {provider.title()}")
        if self._oauth_user_lbl:
            self._oauth_user_lbl.setText(uid)
        if self._login_form:
            self._login_form.hide()
        if self._oauth_frame:
            self._oauth_frame.show()

    def _on_webhook_updated(self, url: str):
        if self.webhook_entry:
            self.webhook_entry.setText(url)

    def _on_apikey_updated(self, key: str):
        if self.api_key_edit:
            self.api_key_edit.setText(key)

    def _on_vps_activated(self):
        self.vps_active = True
        if self.vps_btn:
            self.vps_btn.setText("✓  VPS Active — 24/7")
            self.vps_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {GLASS_EM};
                    color: {GREEN_LT};
                    border: 1px solid {BORDER_SOFT};
                    border-radius: 10px;
                    font-size: 13px;
                    font-weight: bold;
                    padding: 0px 20px;
                }}
                QPushButton:hover {{ background-color: {GREEN_BG}; }}
            """)
        if self.vps_status_lbl:
            self.vps_status_lbl.setText("● VPS ACTIVE")
            self.vps_status_lbl.setStyleSheet(
                f"color: {GREEN}; font-size: 10px; background: transparent; border: none;"
            )

    def _on_vps_failed(self, err: str):
        self._reset_vps_btn()
        QMessageBox.critical(self, "VPS Setup Failed", err)

    def _on_vps_btn_reset(self):
        self._reset_vps_btn()

    def _reset_vps_btn(self):
        if self.vps_btn:
            self.vps_btn.setText("Login to MT5 on VPS  →")
            self.vps_btn.setEnabled(True)
            self.vps_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {GOLD};
                    color: #0A0600;
                    border: none;
                    border-radius: 10px;
                    font-size: 13px;
                    font-weight: bold;
                    padding: 0px 20px;
                }}
                QPushButton:hover {{ background-color: {GOLD_LT}; }}
                QPushButton:disabled {{ background-color: {GOLD_DK}; color: #92400E; }}
            """)

    def _on_connect_enabled(self, enabled: bool):
        if self.connect_btn:
            self.connect_btn.setEnabled(enabled)

    def _on_avatar_updated(self, text: str):
        if self._avatar:
            self._avatar.setText(text)

    # =========================================================================
    # BUSINESS LOGIC (unchanged from original)
    # =========================================================================
    def update_status(self, text: str):
        self.sig.status_changed.emit(text)

    def append_log(self, text: str):
        self.sig.log_appended.emit(text)

    def _set_dot(self, name: str, online: bool):
        self.sig.dot_changed.emit(name, online)

    def _set_status(self, bridge=None, mt5=None, broker=None):
        if bridge  is not None: self._set_dot("Bridge", bridge)
        if mt5     is not None: self._set_dot("MT5",    mt5)
        if broker  is not None: self._set_dot("Broker", broker)

    def _set_state_callback(self, state: dict):
        self._set_status(
            bridge=state.get("bridge_connected"),
            mt5=state.get("mt5_connected"),
            broker=state.get("broker_connected"),
        )

    def _get_bridge_url(self) -> str:
        if self.bridge_entry:
            return self.bridge_entry.text().strip() or PRODUCTION_BRIDGE_URL
        return PRODUCTION_BRIDGE_URL

    def _get_mt5_creds(self) -> dict:
        return {
            "login":    self.mt5_acct_edit.text().strip()   if self.mt5_acct_edit   else "",
            "password": self.mt5_pw_edit.text()             if self.mt5_pw_edit     else "",
            "server":   self.mt5_server_edit.text().strip() if self.mt5_server_edit else "",
            "path":     self.mt5_path_edit.text().strip()   if self.mt5_path_edit   else detect_mt5_path(),
        }

    def _copy_to_clipboard(self, text: str, btn: QPushButton = None):
        QApplication.clipboard().setText(text)
        if btn:
            orig = btn.text()
            btn.setText("Copied!")
            QTimer.singleShot(1500, lambda: btn.setText(orig))

    def _toggle_api_key_reveal(self):
        if self.api_key_edit:
            if self._api_key_visible:
                self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            else:
                self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._api_key_visible = not self._api_key_visible

    def _toggle_startup(self, checked: bool):
        try:
            if checked:
                _enable_startup()
            else:
                _disable_startup()
        except Exception as e:
            QMessageBox.warning(self, "Startup error", str(e))

    def _browse_mt5_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select MT5 terminal64.exe", "C:\\Program Files\\MetaTrader 5",
            "Executables (*.exe)")
        if path and self.mt5_path_edit:
            self.mt5_path_edit.setText(path)

    def _clear_logs(self):
        if self.log_box:
            self.log_box.clear()

    def _set_tv_action(self, action: str):
        self._tv_action = action
        if self._buy_btn and self._sell_btn:
            self._buy_btn.setObjectName("buyBtnActive" if action == "BUY" else "buyBtn")
            self._sell_btn.setObjectName("sellBtnActive" if action == "SELL" else "sellBtn")
            for btn in (self._buy_btn, self._sell_btn):
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        self._update_tv_preview()

    def _update_tv_preview(self):
        if not self.tv_preview:
            return
        uid = self.user_entry.text().strip() if self.user_entry else ""
        api_key = self.api_key or (self.api_key_edit.text() if self.api_key_edit else "")
        msg = {
            "action":      self._tv_action,
            "symbol":      self.tv_symbol_edit.text() if self.tv_symbol_edit else "{{ticker}}",
            "size":        self.tv_size_edit.text()   if self.tv_size_edit   else "0.1",
            "user_id":     uid,
            "api_key":     api_key or "YOUR_API_KEY",
        }
        sl = self.tv_sl_edit.text()   if self.tv_sl_edit   else ""
        tp = self.tv_tp_edit.text()   if self.tv_tp_edit   else ""
        sc = self.tv_script_edit.text() if self.tv_script_edit else ""
        if sl: msg["sl"] = sl
        if tp: msg["tp"] = tp
        if sc: msg["script_name"] = sc
        self.tv_preview.setPlainText(json.dumps(msg, indent=2))

    def _copy_tv_message(self):
        if self.tv_preview:
            QApplication.clipboard().setText(self.tv_preview.toPlainText())
            self.update_status("JSON copied to clipboard")

    def _reset_tv_fields(self):
        if self.tv_symbol_edit: self.tv_symbol_edit.setText("{{ticker}}")
        if self.tv_size_edit:   self.tv_size_edit.setText("0.1")
        if self.tv_sl_edit:     self.tv_sl_edit.clear()
        if self.tv_tp_edit:     self.tv_tp_edit.clear()
        if self.tv_script_edit: self.tv_script_edit.clear()
        self._set_tv_action("BUY")

    # ── Credential persistence ─────────────────────────────────────────────────
    def _save_cached_credentials(self, user_id: str, password: str):
        try:
            if keyring:
                keyring.set_password(KEYRING_SERVICE, user_id, password)
        except Exception:
            pass
        try:
            data = {}
            if os.path.exists(LAST_USER_FILE):
                with open(LAST_USER_FILE) as f:
                    data = json.load(f) or {}
            data["user_id"] = user_id
            with open(LAST_USER_FILE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _save_oauth_credentials(self, user_id: str, provider: str):
        try:
            data = {}
            if os.path.exists(LAST_USER_FILE):
                with open(LAST_USER_FILE) as f:
                    data = json.load(f) or {}
            data["user_id"]        = user_id
            data["oauth_provider"] = provider
            with open(LAST_USER_FILE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _load_cached_credentials(self):
        try:
            if not os.path.exists(LAST_USER_FILE):
                return
            with open(LAST_USER_FILE) as f:
                data = json.load(f) or {}
            uid      = data.get("user_id", "")
            provider = data.get("oauth_provider", "")
            if uid and self.user_entry:
                self.user_entry.setText(uid)
            if provider:
                self.api_key = data.get("api_key", "")
                return  # will auto-connect via oauth
            if uid and keyring:
                pw = keyring.get_password(KEYRING_SERVICE, uid)
                if pw and self.pass_entry:
                    self.pass_entry.setText(pw)
                    if self.remember_cb:
                        self.remember_cb.setChecked(True)
        except Exception:
            pass

    def _auto_connect_if_cached(self):
        try:
            if not os.path.exists(LAST_USER_FILE):
                return
            with open(LAST_USER_FILE) as f:
                data = json.load(f) or {}
            provider = data.get("oauth_provider", "")
            uid      = data.get("user_id", "")
            api_key  = data.get("api_key", "")
            if provider and uid:
                self.api_key = api_key
                if self.api_key_edit:
                    self.api_key_edit.setText(api_key)
                self.sig.avatar_updated.emit(uid[:2].upper())
                self.sig.oauth_success.emit(provider, uid, True)
                threading.Thread(
                    target=self._refresh_dashboard_summary, daemon=True).start()
                if IS_WINDOWS:
                    QTimer.singleShot(200, self.start_relay)
        except Exception:
            pass

    # ── Sign in / OAuth ───────────────────────────────────────────────────────
    def _sign_in(self):
        user_id  = self.user_entry.text().strip()  if self.user_entry  else ""
        password = self.pass_entry.text()          if self.pass_entry  else ""
        if not user_id or not password:
            QMessageBox.warning(self, "Missing fields", "Enter email and password.")
            return
        self._save_cached_credentials(user_id, password)
        self.sig.avatar_updated.emit(user_id[:2].upper())
        self.start_relay()

    def _open_oauth(self, provider: str):
        base = self._get_bridge_url()
        try:
            resp = requests.post(f"{base}/auth/desktop/start",
                                 json={"provider": provider}, timeout=8)
            if resp.status_code != 200:
                QMessageBox.critical(self, "OAuth error", resp.text or "Could not start OAuth")
                return
            data     = resp.json()
            auth_url = data.get("auth_url")
            state    = data.get("state")
            if not (auth_url and state):
                QMessageBox.critical(self, "OAuth error", "Missing auth URL or state")
                return
        except Exception as exc:
            QMessageBox.critical(self, "Cannot connect",
                f"Could not reach the PlatAlgo server.\n\nBridge URL: {base}\n\nDetails: {exc}")
            return

        self.update_status(f"Login with {provider.title()}…")
        threading.Thread(
            target=self._poll_desktop_token, args=(state, provider), daemon=True).start()

        if webview:
            def launch_webview():
                window = webview.create_window("PlatAlgo Login", auth_url, width=1024, height=760)
                webview.start()
            threading.Thread(target=launch_webview, daemon=True).start()
        else:
            webbrowser.open(auth_url)

    def _poll_desktop_token(self, state: str, provider: str = ""):
        base = self._get_bridge_url()
        for i in range(180):
            try:
                resp = requests.get(f"{base}/auth/desktop/consume/{state}", timeout=6)
                if resp.status_code == 200:
                    data    = resp.json()
                    uid     = data.get("user_id", "")
                    api_key = data.get("api_key", "")
                    if uid and api_key:
                        self.api_key = api_key
                        self.sig.apikey_updated.emit(api_key)
                        self.sig.avatar_updated.emit(uid[:2].upper())
                        if self.user_entry:
                            QTimer.singleShot(0, lambda u=uid: self.user_entry.setText(u))
                        self.sig.oauth_success.emit(provider, uid, False)
                        self.update_status("OAuth linked — ready to connect")
                        threading.Thread(
                            target=self._refresh_dashboard_summary, daemon=True).start()
                        if IS_WINDOWS:
                            QTimer.singleShot(100, self.start_relay)
                        return
                elif resp.status_code == 410:
                    self.update_status("OAuth flow expired — start again")
                    return
            except Exception as exc:
                if i % 10 == 0:
                    self.update_status(f"Waiting for OAuth… ({exc})")
            time.sleep(1)
        self.update_status("OAuth login timed out — try again")

    def _sign_out_oauth(self):
        self._oauth_provider = None
        self.api_key = None
        if self.api_key_edit:
            self.api_key_edit.clear()
        try:
            if os.path.exists(LAST_USER_FILE):
                with open(LAST_USER_FILE) as f:
                    data = json.load(f) or {}
                data.pop("oauth_provider", None)
                data.pop("api_key", None)
                with open(LAST_USER_FILE, "w") as f:
                    json.dump(data, f)
        except Exception:
            pass
        if self._oauth_frame:
            self._oauth_frame.hide()
        if self._login_form:
            self._login_form.show()
        self.update_status("Signed out")

    # ── Relay control ─────────────────────────────────────────────────────────
    def start_relay(self):
        user_id  = self.user_entry.text().strip() if self.user_entry else ""
        password = self.pass_entry.text()         if self.pass_entry else ""
        if not user_id or not (password or self.api_key):
            QMessageBox.warning(self, "Missing fields",
                                "Provide password or complete OAuth login.")
            return
        if password:
            self._save_cached_credentials(user_id, password)
        self.sig.avatar_updated.emit(user_id[:2].upper())
        bridge = self._get_bridge_url()
        mt5    = self._get_mt5_creds()
        self.relay = Relay(
            bridge, user_id, password,
            api_key=self.api_key,
            mt5_login=mt5.get("login") or None,
            mt5_password=mt5.get("password") or None,
            mt5_server=mt5.get("server") or None,
            mt5_path=mt5.get("path") or None,
        )
        if self.connect_btn:
            self.connect_btn.setEnabled(False)
        self.update_status("Connecting to bridge…")

        def run():
            ok = self.relay.start(on_status=self.update_status,
                                  on_state=self._set_state_callback)
            if ok is False:
                self.update_status("Auth failed — check username / password")
            elif ok is None:
                self.update_status("Relay stopped")
            self.sig.connect_enabled.emit(True)

        threading.Thread(target=run, daemon=True).start()
        threading.Thread(target=self._refresh_dashboard_summary, daemon=True).start()

    def enable_managed_mode(self):
        user_id  = self.user_entry.text().strip() if self.user_entry else ""
        password = self.pass_entry.text()         if self.pass_entry else ""
        api_key  = self.api_key
        if not user_id or not (password or api_key):
            QMessageBox.warning(self, "Missing fields",
                                "Sign in first (username/password or Google/Facebook).")
            return
        mt5 = self._get_mt5_creds()
        if not mt5.get("login") or not mt5.get("password") or not mt5.get("server"):
            QMessageBox.warning(self, "MT5 credentials required",
                "Fill in MT5 Account Number, MT5 Password, and MT5 Server.\n\n"
                "The cloud server will execute trades 24/7 on your behalf.")
            return
        if password:
            self._save_cached_credentials(user_id, password)
        self.sig.avatar_updated.emit(user_id[:2].upper())
        if self.vps_btn:
            self.vps_btn.setText("Connecting…")
            self.vps_btn.setEnabled(False)
        self.update_status("Enabling VPS 24/7 mode…")

        def run_setup():
            bridge = self._get_bridge_url()
            client = RelayClient(bridge, user_id)
            if api_key:
                ok = client.setup_managed_execution(
                    api_key, mt5, mt5_path_override=mt5.get("path") or None)
            else:
                ok = client.setup_managed_execution_with_login(
                    password, mt5, mt5_path_override=mt5.get("path") or None)
            if ok is True:
                self.update_status("VPS 24/7 mode active")
                self._set_status(bridge=True, mt5=True, broker=True)
                self.sig.vps_activated.emit()
                threading.Thread(
                    target=self._refresh_dashboard_summary, daemon=True).start()
            else:
                err = ok if isinstance(ok, str) else "Unknown error"
                self.update_status(f"VPS setup failed: {err}")
                self.sig.vps_failed.emit(err)

        threading.Thread(target=run_setup, daemon=True).start()

    def disable_managed_mode(self):
        user_id = self.user_entry.text().strip() if self.user_entry else ""

        def run():
            ok = False
            try:
                resp = requests.post(
                    f"{self._get_bridge_url()}/relay/managed/disable",
                    json={"user_id": user_id},
                    headers={"X-User-ID": user_id},
                    timeout=10,
                )
                ok = resp.status_code == 200
            except Exception:
                pass
            if ok:
                self.sig.vps_btn_reset.emit()
                self.vps_active = False
                self.update_status("VPS mode disabled")
                if self.vps_status_lbl:
                    QTimer.singleShot(0, lambda: (
                        self.vps_status_lbl.setText("● VPS INACTIVE"),
                        self.vps_status_lbl.setStyleSheet(
                            f"color: {FG_FAINT}; font-size: 10px; background: transparent; border: none;")
                    ))
            else:
                self.update_status("Failed to disable VPS mode — check connection")

        threading.Thread(target=run, daemon=True).start()

    def stop_relay(self):
        if self.relay:
            self.relay.stop()
        self._set_status(bridge=False, mt5=False, broker=False)
        self.update_status("Stopped")
        self.sig.connect_enabled.emit(True)
        self.sig.vps_btn_reset.emit()
        self.vps_active = False

    # ── Dashboard refresh ─────────────────────────────────────────────────────
    def _do_refresh(self):
        threading.Thread(target=self._refresh_dashboard_summary, daemon=True).start()

    def _refresh_dashboard_summary(self):
        uid = self.user_entry.text().strip() if self.user_entry else ""
        pw  = self.pass_entry.text()         if self.pass_entry else ""
        payload = {"user_id": uid}
        if pw:
            payload["password"] = pw
        elif self.api_key:
            payload["api_key"] = self.api_key
        else:
            return
        try:
            resp = requests.post(
                f"{self._get_bridge_url()}/dashboard/summary/login",
                json=payload, timeout=8,
            )
            if resp.status_code != 200:
                return
            self._set_dot("Bridge", True)
            d    = resp.json()
            dash = d.get("dashboard", {})

            wh_url = d.get("webhook_url", "")
            if wh_url:
                self.sig.webhook_updated.emit(wh_url)

            ak = d.get("api_key", "")
            if ak and not self.api_key:
                self.api_key = ak
                self.sig.apikey_updated.emit(ak)

            scripts = dash.get("scripts", [])
            lines   = [
                f"Account      : {uid}",
                f"Webhook URL  : {wh_url}",
                f"Relays       : {dash.get('relay_online', 0)}/{dash.get('relay_total', 0)} online",
                f"Scripts      : {len(scripts)}",
            ]
            if scripts:
                lines += ["", "── Script Performance ──"]
                for s in scripts:
                    lines.append(
                        f"  {s.get('script_name', '—'):<24} "
                        f"{s.get('executed_count', 0)} executed  /  "
                        f"{s.get('signals_count', 0)} signals"
                    )
            self.sig.summary_updated.emit("\n".join(lines))
        except Exception:
            pass

    # ── Auto-updater ──────────────────────────────────────────────────────────
    def _check_updates(self):
        try:
            resp = requests.get(f"{self._get_bridge_url()}/version", timeout=5)
            if resp.status_code != 200:
                return
            info   = resp.json()
            latest = info.get("version") or info.get("app_version", "")
            url    = info.get("windows_url" if IS_WINDOWS else "mac_url") or \
                     info.get("relay_download_url", "")
            if latest and latest != APP_VERSION and url:
                self.sig.update_prompt.emit(latest, url)
        except Exception:
            pass

    def _prompt_update(self, version: str, url: str):
        dlg = QDialog(self)
        dlg.setWindowTitle("Update Available")
        dlg.setFixedSize(420, 200)
        dlg.setStyleSheet(f"background-color: {BG_SIDE};")

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(32, 32, 32, 24)
        lay.setSpacing(10)
        lay.addWidget(_lbl(None, "Update Available", FG, 16, True))
        lay.addWidget(_lbl(None, f"v{version} is ready — you're on v{APP_VERSION}", FG_MUTED, 12))
        lay.addStretch()

        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(10)

        update_btn = QPushButton("Update Now  →")
        update_btn.setObjectName("goldBtn")
        update_btn.setFixedHeight(44)
        update_btn.clicked.connect(lambda: (dlg.accept(), self._download_and_install(url, version)))

        later_btn = QPushButton("Later")
        later_btn.setObjectName("outlineBtn")
        later_btn.setFixedHeight(44)
        later_btn.clicked.connect(dlg.reject)

        br_lay.addWidget(update_btn, 1)
        br_lay.addWidget(later_btn)
        lay.addWidget(btn_row)
        dlg.exec()

    def _download_and_install(self, url: str, version: str):
        import tempfile

        dlg = QDialog(self)
        dlg.setWindowTitle("Downloading Update")
        dlg.setFixedSize(420, 160)
        dlg.setStyleSheet(f"background-color: {BG_SIDE};")

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(28, 28, 28, 24)
        lay.setSpacing(10)

        status_lbl = _lbl(None, f"Downloading v{version}…", FG_MUTED, 12)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setFixedHeight(12)
        pct_lbl = _lbl(None, "0%", FG_FAINT, 11)

        lay.addWidget(status_lbl)
        lay.addWidget(bar)
        lay.addWidget(pct_lbl)

        self.sig.progress_changed.connect(lambda p, t: (
            bar.setValue(int(p * 100)),
            pct_lbl.setText(f"{int(p * 100)}%"),
            status_lbl.setText(t),
        ))
        dlg.show()

        def _run():
            try:
                ext  = ".exe" if IS_WINDOWS else ".dmg"
                dest = os.path.join(tempfile.gettempdir(), f"PlatAlgoRelay_update{ext}")
                r    = requests.get(url, stream=True, timeout=60)
                total = int(r.headers.get("Content-Length", 0))
                done  = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if total:
                                self.sig.progress_changed.emit(done / total, f"Downloading v{version}…")
                self.sig.progress_changed.emit(1.0, "Installing…")
                QTimer.singleShot(500, lambda: (dlg.accept(), self._apply_update(dest)))
            except Exception as exc:
                QTimer.singleShot(0, lambda: (
                    dlg.reject(),
                    QMessageBox.critical(self, "Update failed", f"Could not download:\n{exc}")
                ))

        threading.Thread(target=_run, daemon=True).start()

    def _apply_update(self, dest: str):
        if IS_WINDOWS:
            import tempfile
            current_exe = sys.executable if getattr(sys, "frozen", False) else ""
            if not current_exe:
                webbrowser.open(os.path.dirname(dest))
                return
            bat = os.path.join(tempfile.gettempdir(), "platalgo_update.bat")
            with open(bat, "w") as f:
                f.write(
                    f"@echo off\r\n"
                    f"timeout /t 2 /nobreak >nul\r\n"
                    f"move /y \"{dest}\" \"{current_exe}\"\r\n"
                    f"start \"\" \"{current_exe}\"\r\n"
                    f"del \"%~f0\"\r\n"
                )
            subprocess.Popen(
                ["cmd", "/c", bat],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            sys.exit(0)
        else:
            subprocess.run(["open", dest], check=False)
            QMessageBox.information(self, "Update downloaded",
                "Drag PlatAlgoRelay to Applications to complete the update.")

    # ── System Tray ───────────────────────────────────────────────────────────
    def _create_tray(self) -> Optional[QSystemTrayIcon]:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QApplication.style().standardIcon(
            QApplication.style().StandardPixmap.SP_ComputerIcon)

        tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        open_act = menu.addAction("Open PlatAlgo Relay")
        open_act.triggered.connect(self._restore_window)
        menu.addSeparator()
        quit_act = menu.addAction("Exit")
        quit_act.triggered.connect(self._quit_from_tray)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: (
            self._restore_window()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
        ))
        return tray

    def _restore_window(self):
        self.showNormal()
        self.activateWindow()
        if self.tray_icon:
            self.tray_icon.hide()
            self.tray_icon = None

    def _quit_from_tray(self):
        if self.tray_icon:
            self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event):
        tray = self._create_tray()
        if tray:
            self.tray_icon = tray
            tray.show()
            tray.showMessage("PlatAlgo Relay", "Running in system tray. Double-click to open.",
                             QSystemTrayIcon.MessageIcon.Information, 2000)
            self.hide()
            event.ignore()
        else:
            event.accept()


# ── Entry ─────────────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PlatAlgo Relay")
    app.setStyleSheet(QSS)

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = RelayGuiApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
