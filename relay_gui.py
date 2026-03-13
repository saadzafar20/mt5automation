#!/usr/bin/env python3
"""PlatAlgo Relay — desktop app matching the PlatAlgo web design system."""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests
from tkinter import messagebox
import tkinter.font as tkfont

try:
    import customtkinter as ctk
except ImportError:
    import tkinter as ctk

try:
    import keyring
except ImportError:
    keyring = None

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

try:
    import winreg
except ImportError:
    winreg = None

try:
    import webview  # Optional; used to keep OAuth flows inside the app window
except ImportError:
    webview = None

from relay import Relay, RelayClient

# ── Platform ──────────────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"

# ── Glass Morphism Palette ────────────────────────────────────────────────────
BG            = "#080d0b"        # Near-black deep base
BG_ELEVATED   = "#0d1612"        # Slightly elevated background
BG_CARD       = "#111e17"        # Card surface
BG_INPUT      = "#0f1a13"        # Input fields
GLASS         = "#162b1e"        # Glass panel base
GLASS_EMERALD = "#1c3d2b"        # Green-tinted glass chip
GLASS_GOLD    = "#3d3218"        # Gold-tinted glass chip
GLASS_WHITE   = "#1e2e26"        # Near-white glass overlay

FG            = "#eef4ec"        # Primary text — warm white
FG_MUTED      = "#7fa68a"        # Secondary text
FG_SOFT       = "#b8ccbf"        # Tertiary text

PRIMARY       = "#1db368"        # Vivid emerald
PRIMARY_LT    = "#28d47e"        # Bright emerald hover
PRIMARY_DK    = "#127a48"        # Deep emerald
ACCENT        = "#e8c060"        # Warm gold
ACCENT_LT     = "#f5d878"        # Bright gold
ACCENT_DK     = "#a88530"        # Deep gold

BORDER        = "#1e3529"        # Subtle card border
BORDER_SOFT   = "#2d4d3c"        # Softer border / divider
BORDER_GLOW   = "#28664a"        # Emerald glow border (active states)
DANGER        = "#e85c5c"
DANGER_BG     = "#2e1414"

# ── App constants ─────────────────────────────────────────────────────────────
PRODUCTION_BRIDGE_URL = "https://app.platalgo.com"
KEYRING_SERVICE       = "platalgo-relay"
LAST_USER_FILE        = "relay_last_user.json"

WIN_TASK_NAME   = "PlatAlgoRelay"
MAC_PLIST_LABEL = "com.platalgo.relay"
MAC_PLIST_PATH  = Path.home() / "Library" / "LaunchAgents" / f"{MAC_PLIST_LABEL}.plist"

DISPLAY_FONT_CANDIDATES = [
    "SF Pro Display",
    "Segoe UI Variable Display",
    "Aptos Display",
    "Segoe UI",
]
TEXT_FONT_CANDIDATES = [
    "SF Pro Text",
    "Segoe UI Variable Text",
    "Aptos",
    "Segoe UI",
]
MONO_FONT_CANDIDATES = ["SF Mono", "Cascadia Mono", "Consolas", "Courier New"]

FONT_TITLE   = ("Segoe UI", 30, "bold")
FONT_HERO    = ("Segoe UI", 18, "bold")
FONT_LABEL   = ("Segoe UI", 15, "bold")
FONT_BODY    = ("Segoe UI", 13)
FONT_SMALL   = ("Segoe UI", 11)
FONT_CAPTION = ("Segoe UI", 10, "bold")
FONT_MONO    = ("Consolas", 11)


def _pick_font_family(candidates, fallback: str) -> str:
    try:
        families = set(tkfont.families())
    except Exception:
        families = set()
    for family in candidates:
        if family in families:
            return family
    return fallback


# ── MT5 path detection ────────────────────────────────────────────────────────
def detect_mt5_path() -> str:
    if winreg:
        keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key_path in keys:
                try:
                    with winreg.OpenKey(root, key_path) as base:
                        count = winreg.QueryInfoKey(base)[0]
                        for idx in range(count):
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
        r = subprocess.run(["schtasks", "/query", "/tn", WIN_TASK_NAME],
                           capture_output=True, text=True)
        return r.returncode == 0
    if IS_MAC:
        return MAC_PLIST_PATH.exists()
    return False

def _enable_startup():
    exe    = sys.executable
    script = os.path.abspath(sys.argv[0])
    if IS_WINDOWS:
        subprocess.run([
            "schtasks", "/create", "/tn", WIN_TASK_NAME,
            "/tr", f'"{exe}" "{script}"',
            "/sc", "ONSTART", "/ru", "SYSTEM", "/rl", "HIGHEST", "/f",
        ], check=True)
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
  <key>StandardOutPath</key>
  <string>{Path.home()}/Library/Logs/platalgo-relay.log</string>
  <key>StandardErrorPath</key>
  <string>{Path.home()}/Library/Logs/platalgo-relay-error.log</string>
</dict></plist>"""
        MAC_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MAC_PLIST_PATH.write_text(plist)
        subprocess.run(["launchctl", "load", str(MAC_PLIST_PATH)], check=True)

def _disable_startup():
    if IS_WINDOWS:
        subprocess.run(["schtasks", "/delete", "/tn", WIN_TASK_NAME, "/f"],
                       check=False)
    elif IS_MAC:
        if MAC_PLIST_PATH.exists():
            subprocess.run(["launchctl", "unload", str(MAC_PLIST_PATH)], check=False)
            MAC_PLIST_PATH.unlink(missing_ok=True)


# ── UI Helpers ────────────────────────────────────────────────────────────────
def _card(parent, glow=False, **kwargs) -> "ctk.CTkFrame":
    defaults = dict(
        fg_color=BG_CARD,
        corner_radius=18,
        border_width=1,
        border_color=BORDER_GLOW if glow else BORDER,
    )
    defaults.update(kwargs)
    return ctk.CTkFrame(parent, **defaults)

def _label(parent, text, color=FG, font=FONT_BODY, **kwargs):
    return ctk.CTkLabel(parent, text=text, text_color=color,
                        font=font, fg_color="transparent", **kwargs)

def _entry(parent, textvariable=None, placeholder="", show=None, **kwargs):
    e = ctk.CTkEntry(
        parent,
        textvariable=textvariable,
        placeholder_text=placeholder,
        placeholder_text_color=FG_MUTED,
        fg_color=BG_INPUT,
        border_color=BORDER_SOFT,
        text_color=FG,
        height=48,
        corner_radius=14,
        font=FONT_BODY,
        **kwargs,
    )
    if show:
        e.configure(show=show)
    return e

def _btn_gold(parent, text, command, **kwargs):
    kwargs.setdefault("height", 48)
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=GLASS_GOLD, hover_color=ACCENT_DK,
        text_color=ACCENT_LT, border_width=1, border_color=ACCENT_DK,
        font=FONT_LABEL, corner_radius=14, **kwargs
    )

def _btn_outline(parent, text, command, **kwargs):
    kwargs.setdefault("height", 48)
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color="transparent", hover_color=GLASS,
        text_color=FG_SOFT, border_color=BORDER_SOFT, border_width=1,
        font=FONT_BODY, corner_radius=14, **kwargs
    )

def _btn_danger(parent, text, command, **kwargs):
    kwargs.setdefault("height", 48)
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=DANGER_BG, hover_color="#3d1515",
        text_color="#ffd0d0", border_color="#7f1d1d", border_width=1,
        font=FONT_BODY, corner_radius=14, **kwargs
    )

def _chip(parent, text, fg_color, text_color=FG, font=FONT_CAPTION, **kwargs):
    return ctk.CTkLabel(
        parent,
        text=text,
        fg_color=fg_color,
        text_color=text_color,
        font=font,
        corner_radius=999,
        padx=12,
        pady=6,
        **kwargs,
    )

def _divider(parent):
    ctk.CTkFrame(parent, height=1, fg_color=BORDER,
                 corner_radius=0).pack(fill="x", padx=16, pady=6)


# ── Main app ──────────────────────────────────────────────────────────────────
class RelayGuiApp:
    def __init__(self, root):
        self.root      = root
        self.relay     = None
        self.tray_icon = None

        self._configure_fonts()

        if hasattr(ctk, "set_appearance_mode"):
            ctk.set_appearance_mode("dark")
        if hasattr(ctk, "set_default_color_theme"):
            ctk.set_default_color_theme("green")

        self.root.title("PlatAlgo Relay")
        self.root.geometry("1220x920")
        if hasattr(self.root, "minsize"):
            self.root.minsize(1080, 820)
        if hasattr(self.root, "configure"):
            self.root.configure(fg_color=BG)

        # StringVars
        self.user_id_var    = ctk.StringVar()
        self.password_var   = ctk.StringVar()
        self.remember_var   = ctk.BooleanVar(value=True)
        self.startup_var    = ctk.BooleanVar(value=False)
        self.bridge_url_var = ctk.StringVar(value=PRODUCTION_BRIDGE_URL)
        self.mt5_path_var   = ctk.StringVar(value=detect_mt5_path() if IS_WINDOWS else "")
        self.status_var     = ctk.StringVar(value="Idle")
        self.mt5_acct_var   = ctk.StringVar()
        self.mt5_pw_var     = ctk.StringVar()
        self.mt5_server_var = ctk.StringVar()
        self.api_key        = None

        self.log_visible     = False
        self.adv_visible     = False
        self.status_dots     = {}
        self.vps_active      = False
        self.vps_status_chip = None
        self.vps_card        = None

        self._build_ui()
        self._load_cached_credentials()
        self.startup_var.set(_startup_enabled())
        threading.Thread(target=self._check_updates, daemon=True).start()
        self._auto_connect_if_cached()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(300, self._apply_glass_effect)

    def _configure_fonts(self):
        global FONT_TITLE, FONT_HERO, FONT_LABEL, FONT_BODY, FONT_SMALL, FONT_CAPTION, FONT_MONO
        display_family = _pick_font_family(DISPLAY_FONT_CANDIDATES, "Segoe UI")
        text_family    = _pick_font_family(TEXT_FONT_CANDIDATES, "Segoe UI")
        mono_family    = _pick_font_family(MONO_FONT_CANDIDATES, "Consolas")
        FONT_TITLE   = (display_family, 30, "bold")
        FONT_HERO    = (display_family, 18, "bold")
        FONT_LABEL   = (text_family, 15, "bold")
        FONT_BODY    = (text_family, 13)
        FONT_SMALL   = (text_family, 11)
        FONT_CAPTION = (text_family, 10, "bold")
        FONT_MONO    = (mono_family, 11)

    def _apply_glass_effect(self):
        """Enable platform-level glass/transparency (Windows 11 Mica, macOS transparency)."""
        try:
            if IS_WINDOWS:
                self.root.update()
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                if not hwnd:
                    hwnd = self.root.winfo_id()
                # Force dark title bar (DWMWA_USE_IMMERSIVE_DARK_MODE = 20)
                dark = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark)
                )
                # Windows 11 Mica backdrop (DWMWA_SYSTEMBACKDROP_TYPE = 38, value 2 = Mica)
                mica = ctypes.c_int(2)
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 38, ctypes.byref(mica), ctypes.sizeof(mica)
                    )
                except Exception:
                    # Windows 10 fallback — slight alpha transparency
                    self.root.wm_attributes("-alpha", 0.97)
            elif IS_MAC:
                try:
                    self.root.wm_attributes("-transparent", True)
                    self.root.configure(background="systemTransparent")
                except Exception:
                    self.root.wm_attributes("-alpha", 0.97)
        except Exception:
            pass  # Glass unavailable — fall back silently

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        outer.pack(fill="both", expand=True)

        # Dual accent stripe at the top
        ctk.CTkFrame(outer, fg_color=ACCENT_DK, height=2, corner_radius=0).pack(fill="x")
        ctk.CTkFrame(outer, fg_color=PRIMARY_DK, height=1, corner_radius=0).pack(fill="x")

        self._build_header(outer)
        self._build_status_bar(outer)

        body = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(8, 18))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=4)

        self._build_left(body)
        self._build_right(body)

    # ── Header ────────────────────────────────────────────────────────────────
    def _build_header(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color=BG_ELEVATED, corner_radius=0, border_width=0)
        hdr.pack(fill="x")

        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=(20, 18))

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        _chip(left, "PRIVATE EXECUTION CONSOLE", GLASS_GOLD, text_color=ACCENT_LT).pack(anchor="w", pady=(0, 10))

        logo_frame = ctk.CTkFrame(left, fg_color="transparent")
        logo_frame.pack(anchor="w")
        _label(logo_frame, "Plat", color=ACCENT, font=FONT_TITLE).pack(side="left")
        _label(logo_frame, "Algo", color=FG, font=FONT_TITLE).pack(side="left")
        _label(logo_frame, "  Relay", color=FG_SOFT, font=(FONT_BODY[0], 18)).pack(side="left", padx=(8, 0), pady=(8, 0))

        _label(
            left,
            "Emerald execution routing with managed VPS switching and live bridge state.",
            color=FG_MUTED,
            font=FONT_BODY,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="right", padx=(16, 0))

        self._status_pill = ctk.CTkLabel(
            right, textvariable=self.status_var,
            text_color=FG, font=FONT_SMALL,
            fg_color=GLASS_EMERALD, corner_radius=999,
            padx=16, pady=8
        )
        self._status_pill.pack(side="left", padx=(0, 12), pady=(18, 0))

        self._avatar = ctk.CTkLabel(
            right, text="--",
            fg_color=GLASS_GOLD, text_color=FG,
            font=FONT_SMALL,
            width=42, height=42, corner_radius=21
        )
        self._avatar.pack(side="left", pady=(14, 0))

        lines = ctk.CTkFrame(parent, fg_color="transparent")
        lines.pack(fill="x")
        ctk.CTkFrame(lines, height=2, fg_color=ACCENT, corner_radius=0).pack(fill="x")
        ctk.CTkFrame(lines, height=1, fg_color=PRIMARY, corner_radius=0).pack(fill="x")

    # ── Status bar ────────────────────────────────────────────────────────────
    def _build_status_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color=BG_ELEVATED, corner_radius=0, border_width=0)
        bar.pack(fill="x", padx=0, pady=0)

        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(padx=24, pady=12, anchor="w")

        for name in ["Bridge", "MT5", "Broker"]:
            f = ctk.CTkFrame(inner, fg_color=BG_CARD, corner_radius=18,
                             border_width=1, border_color=BORDER)
            f.pack(side="left", padx=(0, 28))

            dot = ctk.CTkLabel(f, text="●", text_color=DANGER,
                               font=(FONT_BODY[0], 16), fg_color="transparent")
            dot.pack(side="left", padx=(14, 0), pady=8)

            lbl = _label(f, f"{name}: Offline", color=FG_MUTED, font=FONT_SMALL)
            lbl.pack(side="left", padx=(8, 14), pady=8)

            self.status_dots[name] = (dot, lbl)

        ctk.CTkFrame(parent, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x")

    # ── Left panel ────────────────────────────────────────────────────────────
    def _build_left(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=8)
        frame.columnconfigure(0, weight=1)

        # ── Dashboard Login ───────────────────────────────────────────────────
        login_card = _card(frame)
        login_card.pack(fill="x", pady=(0, 8))

        _chip(login_card, "ACCESS", GLASS_EMERALD, text_color=PRIMARY_LT).pack(anchor="w", padx=18, pady=(18, 8))
        _label(login_card, "Dashboard Login", font=FONT_LABEL, color=FG).pack(anchor="w", padx=18)
        _label(login_card, "Sign in once, then switch between local relay and managed cloud execution.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=18, pady=(4, 12))

        self.user_entry = _entry(login_card, self.user_id_var, "Username")
        self.user_entry.pack(fill="x", padx=18, pady=(0, 8))

        self.pass_entry = _entry(login_card, self.password_var, "Password", show="*")
        self.pass_entry.pack(fill="x", padx=18, pady=(0, 8))

        opts = ctk.CTkFrame(login_card, fg_color="transparent")
        opts.pack(fill="x", padx=18, pady=(2, 12))
        ctk.CTkCheckBox(opts, text="Remember me", variable=self.remember_var,
                        text_color=FG_MUTED, font=FONT_SMALL,
                        fg_color=PRIMARY, hover_color=PRIMARY_LT,
                        checkmark_color=BG).pack(side="left", padx=(0, 16))
        ctk.CTkCheckBox(opts, text="Start on boot", variable=self.startup_var,
                        command=self._toggle_startup,
                        text_color=FG_MUTED, font=FONT_SMALL,
                        fg_color=PRIMARY, hover_color=PRIMARY_LT,
                        checkmark_color=BG).pack(side="left")

        _btn_gold(login_card, "Sign In", self._sign_in, height=44).pack(fill="x", padx=18, pady=(0, 8))

        oauth_row = ctk.CTkFrame(login_card, fg_color="transparent")
        oauth_row.pack(fill="x", padx=18, pady=(0, 18))
        oauth_row.columnconfigure(0, weight=1)
        oauth_row.columnconfigure(1, weight=1)
        _btn_outline(oauth_row, "G  Continue with Google",
                     lambda: self._open_oauth("google"),
                     height=36).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        _btn_outline(oauth_row, "f  Continue with Facebook",
                     lambda: self._open_oauth("facebook"),
                     height=36).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # ── MT5 Broker Credentials ────────────────────────────────────────────
        mt5_card = _card(frame)
        mt5_card.pack(fill="x", pady=(0, 8))

        hdr = ctk.CTkFrame(mt5_card, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(18, 4))
        _label(hdr, "MT5 Broker Credentials", font=FONT_LABEL, color=FG).pack(side="left")
        _chip(hdr, "VPS 24/7", GLASS_GOLD, text_color=ACCENT_LT).pack(side="right")

        _label(mt5_card, "Cloud server executes trades on your behalf",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=18, pady=(0, 10))

        self.mt5_acct_entry = _entry(mt5_card, self.mt5_acct_var, "MT5 Account Number")
        self.mt5_acct_entry.pack(fill="x", padx=18, pady=(0, 8))

        self.mt5_pw_entry = _entry(mt5_card, self.mt5_pw_var, "MT5 Password", show="*")
        self.mt5_pw_entry.pack(fill="x", padx=18, pady=(0, 8))

        self.mt5_server_entry = _entry(mt5_card, self.mt5_server_var,
                                       "MT5 Server  (e.g. ICMarkets-Live01)")
        self.mt5_server_entry.pack(fill="x", padx=18, pady=(0, 10))

        self.mt5_login_btn = _btn_gold(mt5_card, "Login to MT5 on VPS",
                                       self.enable_managed_mode, height=44)
        self.mt5_login_btn.pack(fill="x", padx=18, pady=(0, 18))

        # ── 24/7 VPS Mode ────────────────────────────────────────────────────
        self.vps_card = _card(frame)
        self.vps_card.pack(fill="x", pady=(0, 8))

        _chip(self.vps_card, "24/7 VPS MODE", GLASS_GOLD, text_color=ACCENT_LT).pack(
            anchor="w", padx=18, pady=(18, 8))
        _label(self.vps_card, "Cloud-Managed Execution", font=FONT_LABEL, color=FG).pack(
            anchor="w", padx=18)
        _label(self.vps_card,
               "Your MT5 runs on our server — no need to keep your computer on.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=18, pady=(4, 12))

        _divider(self.vps_card)

        vps_status_row = ctk.CTkFrame(self.vps_card, fg_color="transparent")
        vps_status_row.pack(fill="x", padx=18, pady=(10, 4))
        self.vps_status_chip = _chip(vps_status_row, "● VPS INACTIVE", GLASS, text_color=FG_MUTED)
        self.vps_status_chip.pack(side="left")

        self.vps_btn = _btn_gold(self.vps_card, "Enable 24/7 VPS Mode",
                                 self.enable_managed_mode, height=44)
        self.vps_btn.pack(fill="x", padx=18, pady=(8, 6))

        self.vps_disable_btn = _btn_outline(self.vps_card, "Disable VPS Mode",
                                            self.disable_managed_mode, height=36)
        self.vps_disable_btn.pack(fill="x", padx=18, pady=(0, 18))

        # ── Local Execution ───────────────────────────────────────────────────
        actions_card = _card(frame)
        actions_card.pack(fill="x", pady=(0, 8))
        _chip(actions_card, "LOCAL EXECUTION", GLASS_EMERALD, text_color=PRIMARY_LT).pack(
            anchor="w", padx=18, pady=(18, 8))
        _label(actions_card, "Direct MT5 Connection", font=FONT_LABEL, color=FG).pack(anchor="w", padx=18)
        _label(actions_card, "Connect to MT5 running on this computer for direct low-latency execution.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=18, pady=(4, 12))

        self.connect_btn = _btn_outline(actions_card,
                                        "Connect Local MT5" if IS_WINDOWS else "Connect via Bridge",
                                        self.start_relay, height=38)
        self.connect_btn.pack(fill="x", padx=18, pady=(0, 8))

        _btn_danger(actions_card, "Stop / Disconnect",
                    self.stop_relay, height=38).pack(fill="x", padx=18, pady=(0, 18))

        # ── Advanced ─────────────────────────────────────────────────────────
        adv_card = _card(frame)
        adv_card.pack(fill="x", pady=(0, 8))

        adv_toggle = ctk.CTkFrame(adv_card, fg_color="transparent", cursor="hand2")
        adv_toggle.pack(fill="x", padx=18, pady=12)
        _label(adv_toggle, "Advanced Routing", color=FG_SOFT, font=FONT_SMALL).pack(side="left")
        self._adv_arrow = _label(adv_toggle, "▸", color=FG_MUTED, font=FONT_SMALL)
        self._adv_arrow.pack(side="left", padx=4)
        adv_toggle.bind("<Button-1>", lambda _: self.toggle_advanced())
        for child in adv_toggle.winfo_children():
            child.bind("<Button-1>", lambda _: self.toggle_advanced())

        self.adv_frame = ctk.CTkFrame(adv_card, fg_color="transparent")

        _label(self.adv_frame, "Bridge URL", color=FG_MUTED,
               font=FONT_SMALL).pack(anchor="w", padx=18, pady=(4, 2))
        _entry(self.adv_frame, self.bridge_url_var,
               "Bridge URL").pack(fill="x", padx=18, pady=(0, 8))

        if IS_WINDOWS:
            _label(self.adv_frame, "MT5 Terminal Path", color=FG_MUTED,
                   font=FONT_SMALL).pack(anchor="w", padx=18, pady=(0, 2))
            _entry(self.adv_frame, self.mt5_path_var,
                   "terminal64.exe path").pack(fill="x", padx=18, pady=(0, 18))

        # ── Logs ──────────────────────────────────────────────────────────────
        log_card = _card(frame)
        log_card.pack(fill="x", pady=(0, 0))

        log_toggle = ctk.CTkFrame(log_card, fg_color="transparent", cursor="hand2")
        log_toggle.pack(fill="x", padx=18, pady=12)
        _label(log_toggle, "Execution Logs", color=FG_SOFT, font=FONT_SMALL).pack(side="left")
        self._log_arrow = _label(log_toggle, "▸", color=FG_MUTED, font=FONT_SMALL)
        self._log_arrow.pack(side="left", padx=4)
        log_toggle.bind("<Button-1>", lambda _: self.toggle_logs())
        for child in log_toggle.winfo_children():
            child.bind("<Button-1>", lambda _: self.toggle_logs())

        self.log_box = ctk.CTkTextbox(
            log_card, height=110,
            fg_color=BG_INPUT, text_color=FG_SOFT,
            border_color=BORDER_SOFT, border_width=1,
            font=FONT_MONO, corner_radius=14
        )

    # ── Right panel ───────────────────────────────────────────────────────────
    def _build_right(self, parent):
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=8)

        overview_card = _card(right, glow=True)
        overview_card.pack(fill="x", pady=(0, 10))

        top = ctk.CTkFrame(overview_card, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(18, 10))
        text_col = ctk.CTkFrame(top, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)
        _chip(text_col, "PREMIUM ROUTING", GLASS_GOLD, text_color=ACCENT_LT).pack(anchor="w", pady=(0, 10))
        _label(text_col, "Execution Overview", font=FONT_HERO, color=FG).pack(anchor="w")
        _label(text_col, "Bridge state, dashboard mirror, and managed execution context in one view.",
               color=FG_MUTED, font=FONT_BODY, justify="left").pack(anchor="w", pady=(6, 0))

        chip_row = ctk.CTkFrame(top, fg_color="transparent")
        chip_row.pack(side="right", padx=(12, 0))
        _chip(chip_row, "Emerald", GLASS_EMERALD, text_color=PRIMARY_LT).pack(anchor="e", pady=(0, 8))
        _chip(chip_row, "Gold", GLASS_GOLD, text_color=ACCENT_LT).pack(anchor="e")

        mirror_card = _card(right)
        mirror_card.pack(fill="both", expand=True)

        hdr = ctk.CTkFrame(mirror_card, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(18, 0))
        _label(hdr, "Dashboard Mirror", font=FONT_LABEL, color=FG).pack(side="left")

        self._live_dot = ctk.CTkLabel(hdr, text="●", text_color=DANGER,
                                      font=(FONT_BODY[0], 12), fg_color="transparent")
        self._live_dot.pack(side="left", padx=(8, 0))

        _chip(hdr, "Live Summary", GLASS_EMERALD, text_color=PRIMARY_LT).pack(side="right")

        ctk.CTkFrame(mirror_card, height=1, fg_color=BORDER,
                     corner_radius=0).pack(fill="x", padx=0, pady=(12, 0))

        self.summary_text = ctk.CTkTextbox(
            mirror_card,
            fg_color=BG_INPUT, text_color=FG,
            border_color=BORDER_SOFT, border_width=1,
            font=FONT_MONO, corner_radius=14,
        )
        self.summary_text.pack(fill="both", expand=True, padx=18, pady=(14, 12))
        self.summary_text.insert("end", "Sign in to load the dashboard summary and current routing footprint.")
        self.summary_text.configure(state="disabled")

        btns = ctk.CTkFrame(mirror_card, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(0, 18))

        _btn_outline(btns, "Refresh", self._do_refresh,
                     width=100, height=38).pack(side="left", padx=(0, 8))
        _btn_gold(btns, "Open Web Dashboard",
                  lambda: webbrowser.open(
                      self.bridge_url_var.get().rstrip("/") + "/dashboard"
                  ), height=38).pack(side="left")

    # ── UI helpers ────────────────────────────────────────────────────────────
    def toggle_advanced(self):
        self.adv_visible = not self.adv_visible
        if self.adv_visible:
            self.adv_frame.pack(fill="x")
            self._adv_arrow.configure(text="▾")
        else:
            self.adv_frame.pack_forget()
            self._adv_arrow.configure(text="▸")

    def toggle_logs(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_box.pack(fill="x", padx=18, pady=(0, 18))
            self._log_arrow.configure(text="▾")
        else:
            self.log_box.pack_forget()
            self._log_arrow.configure(text="▸")

    def _set_dot(self, name: str, online: bool):
        dot, lbl = self.status_dots[name]
        dot.configure(text_color=PRIMARY if online else DANGER)
        lbl.configure(text_color=FG if online else FG_MUTED)
        lbl.configure(text=f"{name}: {'Online' if online else 'Offline'}")

    def _set_status(self, bridge=None, mt5=None, broker=None):
        if bridge is not None: self._set_dot("Bridge", bridge)
        if mt5    is not None: self._set_dot("MT5",    mt5)
        if broker is not None: self._set_dot("Broker", broker)
        any_on = any(x is True for x in (bridge, mt5, broker))
        if any_on:
            self._live_dot.configure(text_color=PRIMARY)
        elif all(x is False for x in (bridge, mt5, broker)):
            self._live_dot.configure(text_color=DANGER)

    def _set_state_callback(self, state: dict):
        self.root.after(0, lambda: self._set_status(
            bridge=bool(state.get("cloud_connected")),
            mt5=bool(state.get("mt5_connected")),
            broker=bool(state.get("broker_connected")),
        ))

    def append_log(self, text):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def update_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))
        self.root.after(0, lambda: self.append_log(text))

    # ── Credentials ───────────────────────────────────────────────────────────
    def _save_cached_credentials(self, user_id: str, password: str):
        if not self.remember_var.get():
            return
        if keyring:
            keyring.set_password(KEYRING_SERVICE, user_id, password)
        data = {"user_id": user_id}
        if self.mt5_acct_var.get():
            data["mt5_acct"]   = self.mt5_acct_var.get()
            data["mt5_server"] = self.mt5_server_var.get()
        try:
            with open(LAST_USER_FILE, "w") as f:
                json.dump(data, f)
        except OSError as exc:
            import logging
            logging.getLogger(__name__).warning(f"Could not save credentials cache: {exc}")

    def _load_cached_credentials(self):
        if not os.path.exists(LAST_USER_FILE):
            return
        try:
            with open(LAST_USER_FILE) as f:
                data = json.load(f) or {}
            uid = data.get("user_id", "")
            if uid:
                self.user_id_var.set(uid)
                self._avatar.configure(text=uid[:2].upper())
            if keyring and uid:
                pw = keyring.get_password(KEYRING_SERVICE, uid)
                if pw:
                    self.password_var.set(pw)
            if data.get("mt5_acct"):
                self.mt5_acct_var.set(data["mt5_acct"])
            if data.get("mt5_server"):
                self.mt5_server_var.set(data["mt5_server"])
        except Exception:
            pass

    def _auto_connect_if_cached(self):
        if self.user_id_var.get().strip() and self.password_var.get():
            if IS_WINDOWS:
                self.root.after(600, self.start_relay)
            else:
                self.root.after(600, self._do_refresh)

    # ── Startup ───────────────────────────────────────────────────────────────
    def _toggle_startup(self):
        try:
            if self.startup_var.get():
                _enable_startup()
                self.update_status("Start-on-boot enabled (headless, runs without login)")
            else:
                _disable_startup()
                self.update_status("Start-on-boot disabled")
        except Exception as e:
            self.update_status(f"Startup error: {e}")
            self.startup_var.set(_startup_enabled())

    # ── MT5 credentials ───────────────────────────────────────────────────────
    def _get_mt5_creds(self) -> dict:
        acct   = self.mt5_acct_var.get().strip()
        pw     = self.mt5_pw_var.get()
        server = self.mt5_server_var.get().strip()
        path   = self.mt5_path_var.get().strip() if IS_WINDOWS else ""
        if not (acct and pw and server):
            cfg_path = os.path.join(os.getcwd(), "config.json")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path) as f:
                        mt5 = (json.load(f) or {}).get("mt5", {})
                    acct   = acct   or str(mt5.get("login", ""))
                    pw     = pw     or mt5.get("password", "")
                    server = server or mt5.get("server", "")
                    path   = path   or mt5.get("path", "")
                except Exception:
                    pass
        return {"login": acct, "password": pw, "server": server, "path": path}

    # ── Actions ───────────────────────────────────────────────────────────────
    def _sign_in(self):
        uid = self.user_id_var.get().strip()
        pw  = self.password_var.get()
        if not uid or not pw:
            messagebox.showerror("Missing fields", "Username and password are required.")
            return
        if IS_WINDOWS:
            self.start_relay()
        else:
            self._save_cached_credentials(uid, pw)
            self._avatar.configure(text=uid[:2].upper())
            self.update_status("Signed in — fetching dashboard…")
            self._do_refresh()

    def _open_oauth(self, provider: str):
        base = self.bridge_url_var.get().rstrip("/")
        try:
            resp = requests.post(
                f"{base}/auth/desktop/start",
                json={"provider": provider},
                timeout=8,
            )
            if resp.status_code != 200:
                messagebox.showerror("OAuth error", resp.text or "Could not start OAuth")
                return
            data     = resp.json()
            auth_url = data.get("auth_url")
            state    = data.get("state")
            if not (auth_url and state):
                messagebox.showerror("OAuth error", "Missing auth URL or state")
                return
        except Exception as exc:
            messagebox.showerror("OAuth error", str(exc))
            return

        self.update_status(f"Login with {provider.title()}…")
        threading.Thread(target=self._poll_desktop_token, args=(state,), daemon=True).start()

        if webview:
            def launch_webview():
                window = webview.create_window("PlatAlgo Login", auth_url, width=1024, height=760, resizable=True)
                if hasattr(window, "events"):
                    window.events.closed += lambda: self.root.after(0, lambda: self.update_status("Login window closed"))
                webview.start()
            threading.Thread(target=launch_webview, daemon=True).start()
        else:
            messagebox.showinfo(
                "Opening browser",
                "Install 'pywebview' to keep Google/Facebook login inside the app."
            )
            webbrowser.open(auth_url)

    def _poll_desktop_token(self, state: str):
        base = self.bridge_url_var.get().rstrip("/")
        for i in range(180):  # up to 3 minutes
            try:
                resp = requests.get(f"{base}/auth/desktop/consume/{state}", timeout=6)
                if resp.status_code == 200:
                    data    = resp.json()
                    uid     = data.get("user_id", "")
                    api_key = data.get("api_key", "")
                    if uid and api_key:
                        self.api_key = api_key
                        self.password_var.set("")
                        self.user_id_var.set(uid)
                        self._avatar.configure(text=uid[:2].upper())
                        self.update_status("OAuth linked — ready to connect")
                        self._do_refresh()
                        if IS_WINDOWS:
                            self.root.after(0, self.start_relay)
                        return
                elif resp.status_code == 410:
                    self.update_status("OAuth flow expired — start again")
                    return
                elif resp.status_code in (202, 404):
                    if i % 10 == 0:
                        self.update_status("Waiting for OAuth confirmation…")
                else:
                    if i % 10 == 0:
                        self.update_status(f"OAuth waiting ({resp.status_code})…")
            except Exception as exc:
                if i % 10 == 0:
                    self.update_status(f"Waiting for OAuth… ({exc})")
            time.sleep(1)
        self.update_status("OAuth login timed out — try again")

    def start_relay(self):
        user_id  = self.user_id_var.get().strip()
        password = self.password_var.get()
        if not user_id or not (password or self.api_key):
            messagebox.showerror("Missing fields", "Provide password or complete OAuth login.")
            return
        if password:
            self._save_cached_credentials(user_id, password)
        self._avatar.configure(text=user_id[:2].upper())
        bridge     = self.bridge_url_var.get().strip() or PRODUCTION_BRIDGE_URL
        self.relay = Relay(bridge, user_id, password, config_path="config.json", api_key=self.api_key)
        if not self.relay.executor.get_connection_state().get("mt5_connected"):
            self.update_status("Warning: MT5 not connected — check terminal is open")
        self.connect_btn.configure(state="disabled")
        self.update_status("Connecting to bridge…")
        def run():
            ok = self.relay.start(on_status=self.update_status,
                                  on_state=self._set_state_callback)
            if ok is False:
                self.update_status("Auth failed — check username / password")
            elif ok is None:
                self.update_status("Relay stopped")
            self.root.after(0, lambda: self.connect_btn.configure(state="normal"))
        threading.Thread(target=run, daemon=True).start()
        threading.Thread(target=self._refresh_dashboard_summary, daemon=True).start()

    def enable_managed_mode(self):
        user_id  = self.user_id_var.get().strip()
        password = self.password_var.get()
        api_key  = self.api_key
        if not user_id or not (password or api_key):
            messagebox.showerror("Missing fields", "Sign in first (username/password or Google/Facebook).")
            return
        mt5 = self._get_mt5_creds()
        if not mt5.get("login") or not mt5.get("password") or not mt5.get("server"):
            messagebox.showerror(
                "MT5 credentials required",
                "Fill in MT5 Account Number, MT5 Password, and MT5 Server.\n\n"
                "The cloud server will execute trades 24/7 on your behalf."
            )
            return
        if password:
            self._save_cached_credentials(user_id, password)
        self._avatar.configure(text=user_id[:2].upper())
        self.vps_btn.configure(state="disabled", text="Connecting…")
        self.update_status("Enabling VPS 24/7 mode…")
        def run_setup():
            bridge = self.bridge_url_var.get().strip() or PRODUCTION_BRIDGE_URL
            client = RelayClient(bridge, user_id)
            if api_key:
                ok = client.setup_managed_execution(
                    api_key, mt5, mt5_path_override=mt5.get("path") or None
                )
            else:
                ok = client.setup_managed_execution_with_login(
                    password, mt5, mt5_path_override=mt5.get("path") or None
                )
            if ok is True:
                self.update_status("VPS 24/7 mode active — cloud is trading on your behalf")
                self._set_status(bridge=True)
                self.vps_active = True
                def _activate():
                    self.vps_btn.configure(
                        text="✓  VPS 24/7 Active",
                        fg_color=GLASS_EMERALD, hover_color=PRIMARY_DK,
                        border_color=PRIMARY_LT, text_color=FG, state="normal"
                    )
                    if self.vps_status_chip:
                        self.vps_status_chip.configure(
                            text="● VPS ACTIVE",
                            fg_color=GLASS_EMERALD, text_color=PRIMARY_LT
                        )
                    if self.vps_card:
                        self.vps_card.configure(border_color=BORDER_GLOW)
                self.root.after(0, _activate)
                threading.Thread(target=self._refresh_dashboard_summary, daemon=True).start()
            else:
                err_detail = ok if isinstance(ok, str) else "unknown error"
                self.update_status(f"VPS setup failed: {err_detail}")
                self.root.after(0, lambda: messagebox.showerror(
                    "VPS Setup Failed", err_detail
                ))
                self.root.after(0, lambda: self.vps_btn.configure(
                    text="Enable 24/7 VPS Mode",
                    fg_color=GLASS_GOLD, hover_color=ACCENT_DK,
                    border_color=ACCENT_DK, text_color=ACCENT_LT, state="normal"
                ))
        threading.Thread(target=run_setup, daemon=True).start()

    def disable_managed_mode(self):
        user_id = self.user_id_var.get().strip()
        def run():
            ok = False
            try:
                resp = requests.post(
                    f"{self.bridge_url_var.get().rstrip('/')}/relay/managed/disable",
                    json={"user_id": user_id},
                    headers={"X-User-ID": user_id},
                    timeout=10
                )
                ok = resp.status_code == 200
            except Exception:
                pass
            def update():
                if ok:
                    if self.vps_status_chip:
                        self.vps_status_chip.configure(
                            text="● VPS INACTIVE", fg_color=GLASS, text_color=FG_MUTED)
                    self.vps_btn.configure(
                        text="Enable 24/7 VPS Mode",
                        fg_color=GLASS_GOLD, hover_color=ACCENT_DK,
                        border_color=ACCENT_DK, text_color=ACCENT_LT, state="normal"
                    )
                    if self.vps_card:
                        self.vps_card.configure(border_color=BORDER)
                    self.vps_active = False
                    self.update_status("VPS mode disabled")
                else:
                    self.update_status("Failed to disable VPS mode — check connection")
            self.root.after(0, update)
        threading.Thread(target=run, daemon=True).start()

    def stop_relay(self):
        if self.relay:
            self.relay.stop()
        self._set_status(bridge=False, mt5=False, broker=False)
        self.update_status("Stopped")
        if hasattr(self, "connect_btn"):
            self.connect_btn.configure(state="normal")
        self.vps_btn.configure(
            text="Enable 24/7 VPS Mode",
            fg_color=GLASS_GOLD, hover_color=ACCENT_DK,
            border_color=ACCENT_DK, text_color=ACCENT_LT
        )
        if self.vps_status_chip:
            self.vps_status_chip.configure(
                text="● VPS INACTIVE", fg_color=GLASS, text_color=FG_MUTED)
        if self.vps_card:
            self.vps_card.configure(border_color=BORDER)
        self.vps_active = False

    # ── Dashboard mirror ──────────────────────────────────────────────────────
    def _do_refresh(self):
        threading.Thread(target=self._refresh_dashboard_summary, daemon=True).start()

    def _refresh_dashboard_summary(self):
        uid     = self.user_id_var.get().strip()
        pw      = self.password_var.get()
        payload = {"user_id": uid}
        if pw:
            payload["password"] = pw
        elif self.api_key:
            payload["api_key"] = self.api_key
        else:
            return
        try:
            resp = requests.post(
                f"{self.bridge_url_var.get().rstrip('/')}/dashboard/summary/login",
                json=payload,
                timeout=8,
            )
            if resp.status_code != 200:
                return
            d       = resp.json()
            dash    = d.get("dashboard", {})
            scripts = dash.get("scripts", [])
            lines   = [
                f"Webhook URL : {d.get('webhook_url', '')}",
                f"Relays      : {dash.get('relay_online', 0)}/{dash.get('relay_total', 0)} online",
                f"Scripts     : {len(scripts)}",
                "",
            ]
            for s in scripts:
                lines.append(
                    f"  {s.get('script_name')}  —  "
                    f"{s.get('executed_count')} executed  /  "
                    f"{s.get('signals_count')} signals"
                )
            txt = "\n".join(lines)
            self.root.after(0, lambda: self._update_summary_text(txt))
        except Exception:
            pass

    def _update_summary_text(self, text: str):
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("end", text)
        self.summary_text.configure(state="disabled")

    def _check_updates(self):
        try:
            resp = requests.get(
                f"{self.bridge_url_var.get().rstrip('/')}/version", timeout=5
            )
            if resp.status_code != 200:
                return
            info   = resp.json()
            latest = info.get("app_version", "")
            url    = info.get("relay_download_url", "")
            if latest and latest != os.getenv("RELAY_APP_VERSION", "1.0.0") and url:
                def prompt():
                    if messagebox.askyesno(
                        "Update available",
                        f"New version {latest} is available. Open download page?"
                    ):
                        webbrowser.open(url)
                self.root.after(0, prompt)
        except Exception:
            pass

    # ── Tray ──────────────────────────────────────────────────────────────────
    def _create_tray_icon(self):
        if not pystray or not Image or not ImageDraw:
            return None
        img  = Image.new("RGB", (64, 64), color=(8, 13, 11))
        draw = ImageDraw.Draw(img)
        draw.ellipse((8, 8, 56, 56), fill=(232, 192, 96))
        menu = pystray.Menu(
            pystray.MenuItem("Open", lambda: self._restore_window()),
            pystray.MenuItem("Exit", lambda: self._quit_from_tray()),
        )
        return pystray.Icon("platalgo-relay", img, "PlatAlgo Relay", menu)

    def _restore_window(self):
        self.root.after(0, self.root.deiconify)
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None

    def _quit_from_tray(self):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.after(0, self.root.destroy)

    def on_close(self):
        icon = self._create_tray_icon()
        if icon:
            self.root.withdraw()
            self.tray_icon = icon
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            self.update_status("Running in system tray")
        else:
            self.root.destroy()


# ── Entry ─────────────────────────────────────────────────────────────────────
def main():
    root = ctk.CTk() if hasattr(ctk, "CTk") else ctk.Tk()
    RelayGuiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
