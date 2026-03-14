#!/usr/bin/env python3
"""PlatAlgo Relay — premium execution console."""

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
import tkinter as tk
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
    import webview
except ImportError:
    webview = None

from relay import Relay, RelayClient

# ── Platform ──────────────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"

# ── Color Palette — Obsidian Gold (Premium Fintech) ──────────────────────────
# Zinc neutrals + Amber gold + Violet interactive + Emerald success
# Exact shades from Tailwind v3 color system, used by Stripe/Linear/Mercury
BG            = "#09090B"   # Zinc-950  — true near-black, premium depth
BG_ELEVATED   = "#111113"   # Zinc-900+ — sidebar, header surfaces
BG_CARD       = "#18181B"   # Zinc-900  — card surfaces, clear lift
BG_INPUT      = "#27272A"   # Zinc-800  — inputs, well-defined fields
BG_PANEL      = "#0C0C0E"   # Zinc-950+ — main content background

GLASS         = "#1C1C1F"   # Glass overlay tile
GLASS_GOLD    = "#1A1000"   # Amber-tinted dark glass
GLASS_EMERALD = "#0A1A10"   # Emerald-tinted dark glass
GLASS_DARK    = "#050507"   # Deepest overlay

FG            = "#F4F4F5"   # Zinc-100  — near-pure white, not harsh
FG_MUTED      = "#A1A1AA"   # Zinc-400  — comfortable secondary text
FG_SOFT       = "#71717A"   # Zinc-500  — tertiary, labels
FG_FAINT      = "#52525B"   # Zinc-600  — near-invisible dividers

GOLD          = "#D97706"   # Amber-600 — rich warm gold, brand anchor
GOLD_LT       = "#F59E0B"   # Amber-500 — lighter gold, headings
GOLD_DK       = "#92400E"   # Amber-800 — dark gold depth
GOLD_GLOW     = "#0D0700"   # Amber deep shadow
GOLD_BORDER   = "#78350F"   # Amber-900 — gold border tint
GOLD_SHINE    = "#FCD34D"   # Amber-300 — highlight shimmer

PRIMARY       = "#7C3AED"   # Violet-600 — premium interactive, pairs with gold
PRIMARY_LT    = "#8B5CF6"   # Violet-500 — lighter interactive states
PRIMARY_DK    = "#5B21B6"   # Violet-800 — pressed / deep
PRIMARY_GLOW  = "#0A0520"   # Violet deep shadow

ACCENT        = "#10B981"   # Emerald-500 — connected / live
ACCENT_LT     = "#34D399"   # Emerald-400 — lighter success
ACCENT_DK     = "#065F46"   # Emerald-900 — deep success
ACCENT_GLOW   = "#010A05"   # Emerald deep shadow

SUCCESS       = "#10B981"   # Emerald-500
SUCCESS_BG    = "#022C22"   # Emerald-950 background
DANGER        = "#F43F5E"   # Rose-500    — warm refined red
DANGER_BG     = "#1C0008"   # Rose deep background
DANGER_BORDER = "#881337"   # Rose-900

BORDER        = "#27272A"   # Zinc-800  — crisp, clean separator
BORDER_SOFT   = "#3F3F46"   # Zinc-700  — softer dividers
BORDER_GLOW   = "#D97706"   # Amber-600 — gold accent border
BORDER_GOLD   = "#78350F"   # Amber-900 — deep gold border

NAV_ACTIVE_BG = "#18181B"   # Zinc-900  — active nav item fill
NAV_HOVER_BG  = "#141417"   # Between 950-900 — hover state

# ── Typography ────────────────────────────────────────────────────────────────
DISPLAY_FONT_CANDIDATES = ["SF Pro Display", "Segoe UI Variable Display", "Aptos Display", "Segoe UI"]
TEXT_FONT_CANDIDATES    = ["SF Pro Text",    "Segoe UI Variable Text",    "Aptos",         "Segoe UI"]
MONO_FONT_CANDIDATES    = ["SF Mono", "Cascadia Mono", "JetBrains Mono", "Consolas", "Courier New"]

FONT_DISPLAY = ("Segoe UI", 28, "bold")   # Page titles
FONT_TITLE   = ("Segoe UI", 20, "bold")   # Section headers
FONT_HERO    = ("Segoe UI", 15, "bold")   # Card titles
FONT_LABEL   = ("Segoe UI", 13, "bold")   # Field labels
FONT_BODY    = ("Segoe UI", 12)            # Body text
FONT_SMALL   = ("Segoe UI", 11)            # Captions
FONT_CAPTION = ("Segoe UI", 10, "bold")   # Chips/badges
FONT_MONO    = ("Consolas", 11)
FONT_MONO_SM = ("Consolas", 10)

# ── App constants ─────────────────────────────────────────────────────────────
PRODUCTION_BRIDGE_URL = "https://app.platalgo.com"
KEYRING_SERVICE       = "platalgo-relay"
LAST_USER_FILE        = "relay_last_user.json"
WIN_TASK_NAME         = "PlatAlgoRelay"
MAC_PLIST_LABEL       = "com.platalgo.relay"
MAC_PLIST_PATH        = Path.home() / "Library" / "LaunchAgents" / f"{MAC_PLIST_LABEL}.plist"

try:
    from _version import APP_VERSION  # baked in at build time by CI
except ImportError:
    APP_VERSION = os.getenv("RELAY_APP_VERSION", "1.0.0")


def _pick_font_family(candidates, fallback: str) -> str:
    try:
        families = set(tkfont.families())
    except Exception:
        families = set()
    for f in candidates:
        if f in families:
            return f
    return fallback


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
    exe = sys.executable
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
  <key>StandardOutPath</key><string>{Path.home()}/Library/Logs/platalgo-relay.log</string>
  <key>StandardErrorPath</key><string>{Path.home()}/Library/Logs/platalgo-relay-error.log</string>
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


# ── UI Primitives ─────────────────────────────────────────────────────────────
def _card(parent, glow=False, gold=False, **kwargs):
    defaults = dict(
        fg_color=BG_CARD,
        corner_radius=16,
        border_width=1,
        border_color=BORDER_GOLD if gold else (BORDER_GLOW if glow else BORDER),
    )
    defaults.update(kwargs)
    return ctk.CTkFrame(parent, **defaults)


def _label(parent, text, color=FG, font=FONT_BODY, **kwargs):
    return ctk.CTkLabel(parent, text=text, text_color=color,
                        font=font, fg_color="transparent", **kwargs)


def _entry(parent, textvariable=None, placeholder="", show=None, **kwargs):
    kwargs.setdefault("height", 44)
    e = ctk.CTkEntry(
        parent,
        textvariable=textvariable,
        placeholder_text=placeholder,
        placeholder_text_color=FG_SOFT,
        fg_color=BG_INPUT,
        border_color=BORDER_SOFT,
        text_color=FG,
        corner_radius=10,
        font=FONT_BODY,
        **kwargs,
    )
    if show:
        e.configure(show=show)
    return e


def _btn_primary(parent, text, command, **kwargs):
    kwargs.setdefault("height", 44)
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=PRIMARY_DK, hover_color=PRIMARY,
        text_color=FG, font=FONT_LABEL,
        corner_radius=10, **kwargs
    )


def _btn_gold(parent, text, command, **kwargs):
    kwargs.setdefault("height", 44)
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=GLASS_GOLD, hover_color=GOLD_DK,
        text_color=GOLD_LT, border_width=1, border_color=GOLD_BORDER,
        font=FONT_LABEL, corner_radius=10, **kwargs
    )


def _btn_outline(parent, text, command, **kwargs):
    kwargs.setdefault("height", 44)
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color="transparent", hover_color=GLASS,
        text_color=FG_SOFT, border_color=BORDER_SOFT, border_width=1,
        font=FONT_BODY, corner_radius=10, **kwargs
    )


def _btn_danger(parent, text, command, **kwargs):
    kwargs.setdefault("height", 44)
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=DANGER_BG, hover_color="#220A06",
        text_color="#FFBBBB", border_color=DANGER_BORDER, border_width=1,
        font=FONT_BODY, corner_radius=10, **kwargs
    )


def _chip(parent, text, fg_color, text_color=FG, font=FONT_CAPTION, **kwargs):
    return ctk.CTkLabel(parent, text=text, fg_color=fg_color, text_color=text_color,
                        font=font, corner_radius=999, padx=10, pady=4, **kwargs)


def _divider(parent, color=BORDER):
    ctk.CTkFrame(parent, height=1, fg_color=color, corner_radius=0).pack(fill="x", padx=0, pady=8)


def _section_header(parent, title, subtitle=None, chip=None, chip_color=None):
    """Consistent section header with optional chip badge."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=20, pady=(18, 4))
    _label(row, title, color=FG, font=FONT_LABEL).pack(side="left")
    if chip and chip_color:
        _chip(row, chip, chip_color, text_color=ACCENT_LT).pack(side="right")
    if subtitle:
        _label(parent, subtitle, color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=20, pady=(0, 10))


# ── Main App ──────────────────────────────────────────────────────────────────
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
        self.root.geometry("1300x860")
        if hasattr(self.root, "minsize"):
            self.root.minsize(1100, 760)
        if hasattr(self.root, "configure"):
            self.root.configure(fg_color=BG)

        # ── App icon ──────────────────────────────────────────────────────────
        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.exists(_icon_path):
            try:
                if IS_WINDOWS:
                    self.root.iconbitmap(_icon_path)
                else:
                    from PIL import Image as _PILImage, ImageTk as _PILImageTk
                    _img = _PILImageTk.PhotoImage(file=_icon_path)
                    self.root.iconphoto(True, _img)
            except Exception:
                pass

        # ── StringVars ────────────────────────────────────────────────────────
        self.user_id_var     = ctk.StringVar()
        self.password_var    = ctk.StringVar()
        self.remember_var    = ctk.BooleanVar(value=True)
        self.startup_var     = ctk.BooleanVar(value=False)
        self.bridge_url_var  = ctk.StringVar(value=PRODUCTION_BRIDGE_URL)
        self.mt5_path_var    = ctk.StringVar(value=detect_mt5_path() if IS_WINDOWS else "")
        self.status_var      = ctk.StringVar(value="Idle")
        self.mt5_acct_var    = ctk.StringVar()
        self.mt5_pw_var      = ctk.StringVar()
        self.mt5_server_var  = ctk.StringVar()

        # Dashboard data vars
        self.webhook_url_var = ctk.StringVar(value="Sign in to view your webhook URL")
        self.api_key_var     = ctk.StringVar(value="")

        # TradingView message generator vars
        self.tv_action_var   = ctk.StringVar(value="BUY")
        self.tv_symbol_var   = ctk.StringVar(value="{{ticker}}")
        self.tv_size_var     = ctk.StringVar(value="0.1")
        self.tv_sl_var       = ctk.StringVar(value="")
        self.tv_tp_var       = ctk.StringVar(value="")
        self.tv_script_var   = ctk.StringVar(value="")
        self.tv_dynamic_var  = ctk.BooleanVar(value=True)

        # State
        self.api_key          = None
        self.api_key_visible  = False
        self.vps_active       = False
        self.current_panel    = "connect"

        # Required widget refs (set during build)
        self.status_dots      = {}   # dashboard (large) dots — updated when on Dashboard
        self._header_dots     = {}   # header (small) dots — always visible
        self.vps_card         = None
        self.vps_btn          = None
        self.vps_disable_btn  = None
        self.vps_status_chip  = None
        self.connect_btn      = None
        self.summary_text     = None
        self._live_dot        = None
        self._avatar          = None
        self._status_pill     = None
        self._nav_btns        = {}
        self._panels          = {}
        self._webhook_copy_btn = None
        self._apikey_entry    = None
        self._tv_preview      = None
        self._adv_frame       = None  # kept for compat
        self._oauth_provider      = None
        self._login_form_inner    = None
        self._oauth_logged_in_frame = None
        self.adv_visible      = False
        self.log_box          = None
        self._nav_items_data  = {}

        self._build_ui()
        self._load_cached_credentials()
        self.startup_var.set(_startup_enabled())

        # Trace TV vars for live preview
        for var in (self.tv_action_var, self.tv_symbol_var, self.tv_size_var,
                    self.tv_sl_var, self.tv_tp_var, self.tv_script_var,
                    self.tv_dynamic_var, self.user_id_var, self.api_key_var):
            var.trace_add("write", lambda *_: self.root.after(10, self._update_tv_preview))

        threading.Thread(target=self._check_updates, daemon=True).start()
        self._auto_connect_if_cached()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(300, self._apply_glass_effect)

    # ── Font config ───────────────────────────────────────────────────────────
    def _configure_fonts(self):
        global FONT_DISPLAY, FONT_TITLE, FONT_HERO, FONT_LABEL, FONT_BODY, FONT_SMALL
        global FONT_CAPTION, FONT_MONO, FONT_MONO_SM
        d = _pick_font_family(DISPLAY_FONT_CANDIDATES, "Segoe UI")
        t = _pick_font_family(TEXT_FONT_CANDIDATES, "Segoe UI")
        m = _pick_font_family(MONO_FONT_CANDIDATES, "Consolas")
        FONT_DISPLAY = (d, 28, "bold")
        FONT_TITLE   = (d, 20, "bold")
        FONT_HERO    = (d, 15, "bold")
        FONT_LABEL   = (t, 13, "bold")
        FONT_BODY    = (t, 12)
        FONT_SMALL   = (t, 11)
        FONT_CAPTION = (t, 10, "bold")
        FONT_MONO    = (m, 11)
        FONT_MONO_SM = (m, 10)

    # ── Glass effect ──────────────────────────────────────────────────────────
    def _apply_glass_effect(self):
        try:
            if IS_WINDOWS:
                self.root.update()
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
                dark = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
                acrylic = ctypes.c_int(3)
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(acrylic), ctypes.sizeof(acrylic))
                except Exception:
                    self.root.wm_attributes("-alpha", 0.93)
            elif IS_MAC:
                try:
                    self.root.wm_attributes("-transparent", True)
                    self.root.configure(background="systemTransparent")
                except Exception:
                    self.root.wm_attributes("-alpha", 0.97)
        except Exception:
            pass

    # =========================================================================
    # UI Build
    # =========================================================================
    def _build_ui(self):
        outer = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        outer.pack(fill="both", expand=True)

        # Top accent stripe
        stripe = ctk.CTkFrame(outer, fg_color="transparent", corner_radius=0, height=3)
        stripe.pack(fill="x")
        ctk.CTkFrame(stripe, fg_color=GOLD, height=2, corner_radius=0).pack(fill="x")
        ctk.CTkFrame(stripe, fg_color=GOLD_DK, height=1, corner_radius=0).pack(fill="x")

        self._build_header(outer)

        # Body: sidebar + content
        body = ctk.CTkFrame(outer, fg_color="transparent", corner_radius=0)
        body.pack(fill="both", expand=True)

        # Sidebar separator
        ctk.CTkFrame(body, fg_color=BORDER, width=1, corner_radius=0).pack(side="left", fill="y")

        self._build_sidebar(body)

        ctk.CTkFrame(body, fg_color=BORDER, width=1, corner_radius=0).pack(side="left", fill="y")

        self._build_content(body)

    # ── Header ────────────────────────────────────────────────────────────────
    def _build_header(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color=BG_ELEVATED, corner_radius=0, height=62)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=24, pady=0)

        # ── Left: Logo ────────────────────────────────────────────────────────
        logo_row = ctk.CTkFrame(inner, fg_color="transparent")
        logo_row.pack(side="left", fill="y")

        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        if Image and os.path.exists(_icon_path):
            try:
                _logo_img = ctk.CTkImage(
                    light_image=Image.open(_icon_path),
                    dark_image=Image.open(_icon_path),
                    size=(32, 32)
                )
                ctk.CTkLabel(logo_row, image=_logo_img, text="",
                             fg_color="transparent").pack(side="left", padx=(0, 10), pady=15)
            except Exception:
                pass
        _label(logo_row, "PlatAlgo", color=GOLD_LT,
               font=(FONT_HERO[0], 16, "bold")).pack(side="left", pady=20)
        _label(logo_row, "  Relay", color=FG_SOFT,
               font=(FONT_BODY[0], 12)).pack(side="left", pady=(22, 18))

        # ── Center: Status pills ──────────────────────────────────────────────
        dots_frame = ctk.CTkFrame(inner, fg_color="transparent")
        dots_frame.pack(side="left", fill="y", padx=(40, 0))

        for name in ["Bridge", "MT5", "Broker"]:
            pill = ctk.CTkFrame(dots_frame, fg_color=GLASS, corner_radius=20,
                                border_width=1, border_color=BORDER)
            pill.pack(side="left", padx=(0, 8), pady=16)

            dot = ctk.CTkLabel(pill, text="●", text_color=DANGER,
                               font=(FONT_BODY[0], 10), fg_color="transparent")
            dot.pack(side="left", padx=(10, 0), pady=6)

            lbl = _label(pill, f"{name}: Offline", color=FG_MUTED, font=(FONT_SMALL[0], 10))
            lbl.pack(side="left", padx=(4, 12), pady=6)

            self.status_dots[name]  = (dot, lbl)
            self._header_dots[name] = (dot, lbl)

        # ── Right ─────────────────────────────────────────────────────────────
        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="right", fill="y")

        self._live_dot = ctk.CTkLabel(right, text="●", text_color=DANGER,
                                      font=(FONT_BODY[0], 10), fg_color="transparent")
        self._live_dot.pack(side="left", padx=(0, 4), pady=24)

        self._latency_badge = ctk.CTkLabel(
            right, text="○ OFFLINE",
            text_color=FG_FAINT, font=(FONT_CAPTION[0], 10, "bold"),
            fg_color=GLASS, corner_radius=999, padx=10, pady=5
        )
        self._latency_badge.pack(side="left", padx=(0, 10), pady=24)

        self._status_pill = ctk.CTkLabel(
            right, textvariable=self.status_var,
            text_color=FG_MUTED, font=FONT_SMALL,
            fg_color=GLASS, corner_radius=999, padx=14, pady=6
        )
        self._status_pill.pack(side="left", padx=(0, 14), pady=22)

        self._avatar = ctk.CTkLabel(
            right, text="--",
            fg_color=GOLD_GLOW, text_color=GOLD_LT,
            font=(FONT_LABEL[0], 11, "bold"),
            width=36, height=36, corner_radius=18
        )
        self._avatar.pack(side="left", pady=13)

        ctk.CTkFrame(parent, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        sidebar = ctk.CTkFrame(parent, fg_color=BG_ELEVATED, width=230, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # ── App logo section ──────────────────────────────────────────────────
        logo_sec = ctk.CTkFrame(sidebar, fg_color="transparent", height=76)
        logo_sec.pack(fill="x")
        logo_sec.pack_propagate(False)

        logo_row = ctk.CTkFrame(logo_sec, fg_color="transparent")
        logo_row.pack(fill="both", expand=True, padx=18, pady=0)

        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        if Image and os.path.exists(_icon_path):
            try:
                _sbar_img = ctk.CTkImage(
                    light_image=Image.open(_icon_path),
                    dark_image=Image.open(_icon_path),
                    size=(34, 34)
                )
                ctk.CTkLabel(logo_row, image=_sbar_img, text="",
                             fg_color="transparent").pack(side="left", padx=(0, 10), pady=21)
            except Exception:
                pass

        name_col = ctk.CTkFrame(logo_row, fg_color="transparent")
        name_col.pack(side="left", fill="y")
        _label(name_col, "PlatAlgo", color=GOLD_LT,
               font=(FONT_LABEL[0], 14, "bold")).pack(anchor="w", pady=(20, 0))
        _label(name_col, "Relay", color=FG_SOFT,
               font=(FONT_SMALL[0], 10)).pack(anchor="w")

        # Gold accent line under logo
        ctk.CTkFrame(sidebar, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x")

        # ── Nav items ─────────────────────────────────────────────────────────
        nav_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_frame.pack(fill="x", padx=10, pady=(16, 0))

        nav_items = [
            ("connect",      "Connect",      "⊕"),
            ("dashboard",    "Dashboard",    "⊞"),
            ("tradingview",  "TradingView",  "◎"),
            ("instructions", "Guide",        "◑"),
            ("settings",     "Settings",     "◈"),
        ]

        for key, label, icon in nav_items:
            is_active = key == "connect"

            container = ctk.CTkFrame(
                nav_frame,
                fg_color=NAV_ACTIVE_BG if is_active else "transparent",
                corner_radius=10,
                height=50
            )
            container.pack(fill="x", pady=2)
            container.pack_propagate(False)

            bar = ctk.CTkFrame(
                container,
                width=3, corner_radius=2,
                fg_color=GOLD if is_active else "transparent"
            )
            bar.pack(side="left", fill="y", pady=10)

            icon_lbl = ctk.CTkLabel(
                container, text=icon,
                text_color=GOLD_LT if is_active else FG_MUTED,
                font=(FONT_BODY[0], 16),
                fg_color="transparent",
                width=32
            )
            icon_lbl.pack(side="left", padx=(6, 0))

            text_lbl = ctk.CTkLabel(
                container, text=label,
                text_color=GOLD_LT if is_active else FG_MUTED,
                font=(FONT_BODY[0], 13),
                fg_color="transparent",
                anchor="w"
            )
            text_lbl.pack(side="left", fill="x", expand=True, padx=(8, 8))

            # Store in new data dict
            self._nav_items_data[key] = (container, bar, icon_lbl, text_lbl)
            # Backward compat
            self._nav_btns[key] = container

            # Bindings
            def _on_click(e, k=key): self._switch_panel(k)
            def _on_enter(e, k=key):
                f, b, il, tl = self._nav_items_data[k]
                if k != self.current_panel:
                    f.configure(fg_color=NAV_HOVER_BG)
            def _on_leave(e, k=key):
                f, b, il, tl = self._nav_items_data[k]
                if k != self.current_panel:
                    f.configure(fg_color="transparent")

            for widget in (container, icon_lbl, text_lbl):
                widget.bind("<Button-1>", _on_click)
                widget.bind("<Enter>",    _on_enter)
                widget.bind("<Leave>",    _on_leave)

        # Divider
        ctk.CTkFrame(sidebar, height=1, fg_color=BORDER, corner_radius=0).pack(
            fill="x", padx=16, pady=(24, 16))

        # VPS status chip in sidebar
        self.vps_status_chip = _chip(sidebar, "● VPS INACTIVE", GLASS, text_color=FG_FAINT)
        self.vps_status_chip.pack(padx=16, anchor="w")

        # Bottom: version label
        ver_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        ver_frame.pack(side="bottom", fill="x", padx=16, pady=16)
        _label(ver_frame, f"v{APP_VERSION}", color=FG_FAINT, font=(FONT_SMALL[0], 10)).pack(anchor="w")
        _label(ver_frame, "PlatAlgo Relay", color=FG_FAINT, font=(FONT_SMALL[0], 9)).pack(anchor="w")

    # ── Content area ──────────────────────────────────────────────────────────
    def _build_content(self, parent):
        self._content_host = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=0)
        self._content_host.pack(side="left", fill="both", expand=True)

        self._panels["connect"]      = self._build_connect_panel(self._content_host)
        self._panels["dashboard"]    = self._build_dashboard_panel(self._content_host)
        self._panels["tradingview"]  = self._build_tradingview_panel(self._content_host)
        self._panels["instructions"] = self._build_instructions_panel(self._content_host)
        self._panels["settings"]     = self._build_settings_panel(self._content_host)

        # Show default panel
        self._panels["connect"].pack(fill="both", expand=True)

    def _switch_panel(self, key: str):
        if key == self.current_panel:
            return
        self._panels[self.current_panel].pack_forget()
        self._panels[key].pack(fill="both", expand=True)
        self.current_panel = key

        for k, (f, bar, il, tl) in self._nav_items_data.items():
            active = k == key
            f.configure(fg_color=NAV_ACTIVE_BG if active else "transparent")
            bar.configure(fg_color=GOLD if active else "transparent")
            il.configure(text_color=GOLD_LT if active else FG_MUTED)
            tl.configure(text_color=GOLD_LT if active else FG_MUTED)

        if key == "tradingview":
            self._update_tv_preview()

    # =========================================================================
    # CONNECT PANEL
    # =========================================================================
    def _build_connect_panel(self, parent) -> ctk.CTkFrame:
        outer = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                       scrollbar_button_color=BORDER_SOFT,
                                       scrollbar_button_hover_color=BORDER_GLOW)

        # ── Centered container ────────────────────────────────────────────────
        # We use a frame that centers content by using pack with expand
        center = ctk.CTkFrame(outer, fg_color="transparent")
        center.pack(fill="both", expand=True, padx=40, pady=32)
        center.columnconfigure(0, weight=1)
        center.columnconfigure(1, weight=1)
        center.rowconfigure(0, weight=0)

        # ── LEFT COLUMN: Sign In + MT5 Credentials ────────────────────────────
        left = ctk.CTkFrame(center, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))

        # ── Sign In Card ──────────────────────────────────────────────────────
        login_card = ctk.CTkFrame(left, fg_color=BG_CARD, corner_radius=20,
                                  border_width=1, border_color=BORDER)
        login_card.pack(fill="x", pady=(0, 16))

        # Card top accent
        ctk.CTkFrame(login_card, height=3, fg_color=GOLD, corner_radius=0).pack(
            fill="x", pady=(0, 0))
        ctk.CTkFrame(login_card, height=3, corner_radius=20,
                     fg_color="transparent").pack(fill="x")

        lc_inner = ctk.CTkFrame(login_card, fg_color="transparent")
        lc_inner.pack(fill="x", padx=28, pady=(8, 0))

        _label(lc_inner, "Sign In", font=FONT_DISPLAY, color=FG).pack(anchor="w", pady=(4, 0))
        _label(lc_inner, "Access your PlatAlgo dashboard",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", pady=(4, 20))

        # OAuth buttons — large, prominent
        self._login_form_inner = ctk.CTkFrame(login_card, fg_color="transparent")
        self._login_form_inner.pack(fill="x")

        oauth_inner = ctk.CTkFrame(self._login_form_inner, fg_color="transparent")
        oauth_inner.pack(fill="x", padx=28, pady=(0, 4))

        # Google button
        ctk.CTkButton(
            oauth_inner,
            text="  Continue with Google",
            command=lambda: self._open_oauth("google"),
            fg_color="#1A2340", hover_color="#1F2D55",
            text_color=FG,
            border_width=1, border_color=BORDER_SOFT,
            font=(FONT_BODY[0], 13, "bold"),
            height=48, corner_radius=12,
        ).pack(fill="x", pady=(0, 10))

        # Facebook button
        ctk.CTkButton(
            oauth_inner,
            text="  Continue with Facebook",
            command=lambda: self._open_oauth("facebook"),
            fg_color="#0F1B3D", hover_color="#142250",
            text_color=FG,
            border_width=1, border_color=BORDER_SOFT,
            font=(FONT_BODY[0], 13, "bold"),
            height=48, corner_radius=12,
        ).pack(fill="x", pady=(0, 16))

        # Divider with "or"
        div_row = ctk.CTkFrame(oauth_inner, fg_color="transparent")
        div_row.pack(fill="x", pady=(0, 16))
        ctk.CTkFrame(div_row, height=1, fg_color=BORDER_SOFT, corner_radius=0).pack(
            side="left", fill="x", expand=True, pady=8)
        _label(div_row, "  or  ", color=FG_SOFT, font=FONT_SMALL).pack(side="left")
        ctk.CTkFrame(div_row, height=1, fg_color=BORDER_SOFT, corner_radius=0).pack(
            side="left", fill="x", expand=True, pady=8)

        # Username / Password
        self.user_entry = _entry(self._login_form_inner, self.user_id_var, "Username")
        self.user_entry.pack(fill="x", padx=28, pady=(0, 10))

        self.pass_entry = _entry(self._login_form_inner, self.password_var, "Password", show="*")
        self.pass_entry.pack(fill="x", padx=28, pady=(0, 12))

        opts = ctk.CTkFrame(self._login_form_inner, fg_color="transparent")
        opts.pack(fill="x", padx=28, pady=(0, 16))
        ctk.CTkCheckBox(opts, text="Remember me", variable=self.remember_var,
                        text_color=FG_MUTED, font=FONT_SMALL,
                        fg_color=PRIMARY, hover_color=PRIMARY_LT,
                        checkmark_color=BG).pack(side="left")
        ctk.CTkCheckBox(opts, text="Launch on startup", variable=self.startup_var,
                        command=self._toggle_startup,
                        text_color=FG_MUTED, font=FONT_SMALL,
                        fg_color=PRIMARY, hover_color=PRIMARY_LT,
                        checkmark_color=BG).pack(side="left", padx=(16, 0))

        # Sign In button — full gold
        ctk.CTkButton(
            self._login_form_inner,
            text="Sign In  →",
            command=self._sign_in,
            fg_color=GOLD_DK, hover_color=GOLD,
            text_color=FG, font=(FONT_LABEL[0], 13, "bold"),
            height=52, corner_radius=12,
        ).pack(fill="x", padx=28, pady=(0, 28))

        # ── OAuth logged-in banner ────────────────────────────────────────────
        self._oauth_logged_in_frame = ctk.CTkFrame(login_card, fg_color="transparent")

        _olf = self._oauth_logged_in_frame
        olf_inner = ctk.CTkFrame(_olf, fg_color=GLASS, corner_radius=14,
                                 border_width=1, border_color=BORDER_GLOW)
        olf_inner.pack(fill="x", padx=28, pady=(0, 12))

        olf_top = ctk.CTkFrame(olf_inner, fg_color="transparent")
        olf_top.pack(fill="x", padx=16, pady=(16, 8))

        self._oauth_provider_icon = ctk.CTkLabel(
            olf_top, text="✓", text_color=SUCCESS,
            font=(FONT_LABEL[0], 20, "bold"),
            fg_color=SUCCESS_BG, corner_radius=20,
            width=40, height=40
        )
        self._oauth_provider_icon.pack(side="left", padx=(0, 14))

        olf_text = ctk.CTkFrame(olf_top, fg_color="transparent")
        olf_text.pack(side="left", fill="x", expand=True)
        self._oauth_provider_lbl = _label(
            olf_text, "Signed in via Google",
            color=FG, font=(FONT_LABEL[0], 13, "bold"))
        self._oauth_provider_lbl.pack(anchor="w")
        self._oauth_user_lbl = _label(olf_text, "—", color=FG_MUTED, font=FONT_SMALL)
        self._oauth_user_lbl.pack(anchor="w")

        _btn_outline(_olf, "Sign out / Switch account",
                     self._sign_out_oauth, height=36).pack(
            fill="x", padx=28, pady=(0, 20))

        # ── MT5 Credentials Card ──────────────────────────────────────────────
        mt5_card = ctk.CTkFrame(left, fg_color=BG_CARD, corner_radius=20,
                                border_width=1, border_color=BORDER)
        mt5_card.pack(fill="x", pady=(0, 16))

        mt5_inner = ctk.CTkFrame(mt5_card, fg_color="transparent")
        mt5_inner.pack(fill="x", padx=28, pady=28)

        # Header row with lock icon
        mt5_hdr = ctk.CTkFrame(mt5_inner, fg_color="transparent")
        mt5_hdr.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(mt5_hdr, text="🔒", font=(FONT_BODY[0], 16),
                     fg_color="transparent").pack(side="left", padx=(0, 10))
        title_col = ctk.CTkFrame(mt5_hdr, fg_color="transparent")
        title_col.pack(side="left")
        _label(title_col, "MT5 Broker Login", font=FONT_TITLE, color=FG).pack(anchor="w")
        _label(title_col, "Credentials are encrypted and never stored in plain text",
               color=FG_MUTED, font=(FONT_SMALL[0], 10)).pack(anchor="w")

        ctk.CTkFrame(mt5_inner, height=1, fg_color=BORDER, corner_radius=0).pack(
            fill="x", pady=(16, 16))

        self.mt5_acct_entry = _entry(mt5_inner, self.mt5_acct_var,
                                     "Account Number  (e.g. 12345678)")
        self.mt5_acct_entry.pack(fill="x", pady=(0, 10))

        self.mt5_pw_entry = _entry(mt5_inner, self.mt5_pw_var, "MT5 Password", show="*")
        self.mt5_pw_entry.pack(fill="x", pady=(0, 10))

        self.mt5_server_entry = _entry(mt5_inner, self.mt5_server_var,
                                       "Broker Server  (e.g. ICMarkets-Live01)")
        self.mt5_server_entry.pack(fill="x")

        # ── RIGHT COLUMN: Execution Mode ──────────────────────────────────────
        right = ctk.CTkFrame(center, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(16, 0))

        # Section title
        _label(right, "Execution Mode", font=FONT_DISPLAY, color=FG).pack(
            anchor="w", pady=(0, 4))
        _label(right,
               "Choose how your signals reach the broker.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", pady=(0, 24))

        # ── VPS Card ──────────────────────────────────────────────────────────
        self.vps_card = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=20,
                                     border_width=1, border_color=GOLD_BORDER)
        self.vps_card.pack(fill="x", pady=(0, 16))

        # Gold top accent bar
        ctk.CTkFrame(self.vps_card, height=3, fg_color=GOLD, corner_radius=0).pack(fill="x")

        vpc_inner = ctk.CTkFrame(self.vps_card, fg_color="transparent")
        vpc_inner.pack(fill="x", padx=28, pady=24)

        vps_title_row = ctk.CTkFrame(vpc_inner, fg_color="transparent")
        vps_title_row.pack(fill="x", pady=(0, 8))
        _label(vps_title_row, "☁  VPS Execution", font=FONT_TITLE, color=GOLD_LT).pack(side="left")
        ctk.CTkLabel(vps_title_row, text="RECOMMENDED",
                     fg_color=GOLD_GLOW, text_color=GOLD_LT,
                     font=(FONT_CAPTION[0], 9, "bold"),
                     corner_radius=6, padx=8, pady=4).pack(side="right")

        _label(vpc_inner, "Your MT5 runs on our server — 24/7 execution even when offline.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", pady=(0, 16))

        for benefit in [
            ("✓", "Trades 24 hours, 7 days a week", SUCCESS),
            ("✓", "No MT5 required on this machine", SUCCESS),
            ("✓", "Works on Mac, Windows, any device", SUCCESS),
        ]:
            br = ctk.CTkFrame(vpc_inner, fg_color="transparent")
            br.pack(anchor="w", pady=(0, 6))
            _label(br, benefit[0], color=benefit[2],
                   font=(FONT_LABEL[0], 12, "bold")).pack(side="left", padx=(0, 10))
            _label(br, benefit[1], color=FG_MUTED, font=FONT_SMALL).pack(side="left")

        ctk.CTkFrame(vpc_inner, height=1, fg_color=BORDER, corner_radius=0).pack(
            fill="x", pady=(16, 16))

        self.vps_btn = ctk.CTkButton(
            vpc_inner,
            text="Login to MT5 on VPS  →",
            command=self.enable_managed_mode,
            fg_color=GOLD_DK, hover_color=GOLD,
            text_color=FG, font=(FONT_LABEL[0], 13, "bold"),
            height=52, corner_radius=12,
        )
        self.vps_btn.pack(fill="x", pady=(0, 8))

        self.vps_disable_btn = _btn_outline(
            vpc_inner, "Disconnect VPS", self.disable_managed_mode, height=38)
        self.vps_disable_btn.pack(fill="x")

        # ── Local Card ────────────────────────────────────────────────────────
        local_card = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=20,
                                  border_width=1, border_color=BORDER)
        local_card.pack(fill="x", pady=(0, 16))

        loc_inner = ctk.CTkFrame(local_card, fg_color="transparent")
        loc_inner.pack(fill="x", padx=28, pady=24)

        loc_title_row = ctk.CTkFrame(loc_inner, fg_color="transparent")
        loc_title_row.pack(fill="x", pady=(0, 8))
        _label(loc_title_row, "⬡  Local Mode", font=FONT_TITLE, color=FG_MUTED).pack(side="left")
        if not IS_WINDOWS:
            ctk.CTkLabel(loc_title_row, text="WINDOWS ONLY",
                         fg_color=DANGER_BG, text_color=DANGER,
                         font=(FONT_CAPTION[0], 9, "bold"),
                         corner_radius=6, padx=8, pady=4).pack(side="right")

        _label(loc_inner, "Connect directly to MT5 running on this computer.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", pady=(0, 12))

        for icon, text, col in [
            ("✓", "Low latency — direct connection", ACCENT),
            ("✗", "Requires MT5 open on this machine", DANGER),
            ("✗", "Stops when computer sleeps or closes", DANGER),
        ]:
            cr = ctk.CTkFrame(loc_inner, fg_color="transparent")
            cr.pack(anchor="w", pady=(0, 5))
            _label(cr, icon, color=col,
                   font=(FONT_LABEL[0], 12, "bold")).pack(side="left", padx=(0, 10))
            _label(cr, text, color=FG_MUTED if col == ACCENT else FG_SOFT,
                   font=FONT_SMALL).pack(side="left")

        ctk.CTkFrame(loc_inner, height=1, fg_color=BORDER, corner_radius=0).pack(
            fill="x", pady=(14, 14))

        loc_btns = ctk.CTkFrame(loc_inner, fg_color="transparent")
        loc_btns.pack(fill="x")
        loc_btns.columnconfigure(0, weight=1)
        loc_btns.columnconfigure(1, weight=1)

        self.connect_btn = _btn_outline(
            loc_btns,
            "Connect Local MT5" if IS_WINDOWS else "Windows Only",
            self.start_relay if IS_WINDOWS else lambda: None,
            height=44,
            state="normal" if IS_WINDOWS else "disabled"
        )
        self.connect_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        _btn_danger(loc_btns, "Stop", self.stop_relay,
                    height=44).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        return outer

    # =========================================================================
    # DASHBOARD PANEL
    # =========================================================================
    def _build_dashboard_panel(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                       scrollbar_button_color=BORDER_SOFT,
                                       scrollbar_button_hover_color=BORDER_GLOW)

        # ── Header row ────────────────────────────────────────────────────────
        hdr_row = ctk.CTkFrame(frame, fg_color="transparent")
        hdr_row.pack(fill="x", padx=24, pady=(24, 0))
        _label(hdr_row, "Dashboard", font=FONT_DISPLAY, color=FG).pack(side="left")

        refresh_row = ctk.CTkFrame(hdr_row, fg_color="transparent")
        refresh_row.pack(side="right")
        self._live_dot2 = ctk.CTkLabel(refresh_row, text="●", text_color=DANGER,
                                       font=(FONT_BODY[0], 10), fg_color="transparent")
        self._live_dot2.pack(side="left", padx=(0, 6), pady=10)
        _btn_outline(refresh_row, "↺  Refresh", self._do_refresh,
                     height=36, width=110).pack(side="left", pady=8)
        _btn_outline(refresh_row, "Open Web Dashboard",
                     lambda: webbrowser.open(self.bridge_url_var.get().rstrip("/") + "/dashboard"),
                     height=36).pack(side="left", padx=(8, 0), pady=8)

        _label(frame, "Live connection state, webhook URL, and API key.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=24, pady=(6, 24))

        # ── Connection status cards ───────────────────────────────────────────
        status_row = ctk.CTkFrame(frame, fg_color="transparent")
        status_row.pack(fill="x", padx=24, pady=(0, 24))
        status_row.columnconfigure(0, weight=1)
        status_row.columnconfigure(1, weight=1)
        status_row.columnconfigure(2, weight=1)

        conn_meta = {
            "Bridge": ("Cloud server",  "Routes signals to MT5"),
            "MT5":    ("MT5 terminal",  "Executes trade orders"),
            "Broker": ("Broker server", "Confirms fills & balance"),
        }
        for col_i, (name, (subtitle, desc)) in enumerate(conn_meta.items()):
            # Ring card: the visible colored ring is the CTkFrame border
            ring_outer = ctk.CTkFrame(
                status_row,
                fg_color=BG_CARD,
                corner_radius=20,
                border_width=1,
                border_color=BORDER,
            )
            ring_outer.grid(row=0, column=col_i, sticky="nsew",
                            padx=(0 if col_i == 0 else 8, 8 if col_i < 2 else 0))
            ring_outer.grid_propagate(True)

            # Ring indicator — thick colored border = status ring
            ring_frame = ctk.CTkFrame(
                ring_outer,
                width=84, height=84,
                corner_radius=42,
                border_width=7,
                border_color=DANGER,
                fg_color=DANGER_BG,
            )
            ring_frame.pack(pady=(28, 12))
            ring_frame.pack_propagate(False)

            # Status letter in center of ring
            ring_letter = ctk.CTkLabel(
                ring_frame, text="—",
                text_color=DANGER,
                font=(FONT_LABEL[0], 16, "bold"),
                fg_color="transparent"
            )
            ring_letter.place(relx=0.5, rely=0.5, anchor="center")

            _label(ring_outer, name, font=FONT_LABEL, color=FG).pack()
            _label(ring_outer, subtitle, font=(FONT_SMALL[0], 10), color=FG_SOFT).pack(pady=(2, 0))
            lbl_new = _label(ring_outer, "Offline", color=DANGER, font=(FONT_SMALL[0], 10, "bold"))
            lbl_new.pack(pady=(4, 0))
            _label(ring_outer, desc, color=FG_FAINT, font=(FONT_SMALL[0], 9)).pack(pady=(2, 24))

            dot, lbl = self.status_dots[name]
            self.status_dots[name] = (ring_letter, lbl_new)

            if not hasattr(self, "_status_rings"):
                self._status_rings = {}
            self._status_rings[name] = ring_frame
            if not hasattr(self, "_status_bars"):
                self._status_bars = {}
            self._status_bars[name] = ring_frame  # keep compat

        # ── Webhook URL ───────────────────────────────────────────────────────
        webhook_card = ctk.CTkFrame(
            frame, fg_color=BG_CARD, corner_radius=14,
            border_width=1, border_color=GOLD_BORDER
        )
        webhook_card.pack(fill="x", padx=24, pady=(0, 16))

        wh_hdr = ctk.CTkFrame(webhook_card, fg_color="transparent")
        wh_hdr.pack(fill="x", padx=20, pady=(20, 0))
        _label(wh_hdr, "Webhook URL", font=FONT_LABEL, color=FG).pack(side="left")
        _chip(wh_hdr, "PASTE INTO TRADINGVIEW", GLASS_EMERALD, text_color=PRIMARY_LT).pack(side="right")

        _label(webhook_card,
               "Paste this URL into TradingView alert → Notifications → Webhook URL",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=20, pady=(6, 10))

        url_row = ctk.CTkFrame(webhook_card, fg_color="transparent")
        url_row.pack(fill="x", padx=20, pady=(0, 20))

        url_entry = ctk.CTkEntry(
            url_row,
            textvariable=self.webhook_url_var,
            fg_color=BG_INPUT, border_color=GOLD_BORDER,
            text_color=GOLD_LT, font=FONT_MONO,
            height=46, corner_radius=10, state="readonly"
        )
        url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self._webhook_copy_btn = _btn_primary(url_row, "Copy", height=46, width=90,
                                              command=lambda: self._copy_to_clipboard(
                                                  self.webhook_url_var.get(),
                                                  self._webhook_copy_btn))
        self._webhook_copy_btn.pack(side="right")

        # ── API Key ───────────────────────────────────────────────────────────
        api_card = _card(frame)
        api_card.pack(fill="x", padx=24, pady=(0, 16))

        ak_hdr = ctk.CTkFrame(api_card, fg_color="transparent")
        ak_hdr.pack(fill="x", padx=20, pady=(20, 0))
        _label(ak_hdr, "API Key", font=FONT_LABEL, color=FG).pack(side="left")
        _chip(ak_hdr, "KEEP SECRET", DANGER_BG, text_color=DANGER).pack(side="right")

        _label(api_card,
               "Include this in every TradingView alert message to authenticate your trades.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=20, pady=(6, 10))

        ak_row = ctk.CTkFrame(api_card, fg_color="transparent")
        ak_row.pack(fill="x", padx=20, pady=(0, 8))

        self._apikey_entry = ctk.CTkEntry(
            ak_row,
            textvariable=self.api_key_var,
            fg_color=BG_INPUT, border_color=BORDER_SOFT,
            text_color=ACCENT_LT, font=FONT_MONO,
            height=46, corner_radius=10, state="readonly",
            show="•"
        )
        self._apikey_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._ak_reveal_btn = _btn_outline(ak_row, "Show", height=46, width=72,
                                           command=self._toggle_api_key_reveal)
        self._ak_reveal_btn.pack(side="left", padx=(0, 8))

        self._ak_copy_btn = _btn_outline(ak_row, "Copy", height=46, width=72,
                                         command=lambda: self._copy_to_clipboard(
                                             self.api_key_var.get(), self._ak_copy_btn))
        self._ak_copy_btn.pack(side="left")

        _label(api_card,
               "Don't have an API key? Sign in via OAuth or visit your web dashboard.",
               color=FG_FAINT, font=(FONT_SMALL[0], 10)).pack(anchor="w", padx=20, pady=(4, 20))

        # ── Live Summary Mirror ───────────────────────────────────────────────
        mirror_card = _card(frame)
        mirror_card.pack(fill="x", padx=24, pady=(0, 24))

        mir_hdr = ctk.CTkFrame(mirror_card, fg_color="transparent")
        mir_hdr.pack(fill="x", padx=20, pady=(20, 0))
        _label(mir_hdr, "Account Summary", font=FONT_LABEL, color=FG).pack(side="left")
        _chip(mir_hdr, "Live", GLASS_EMERALD, text_color=PRIMARY_LT).pack(side="right")

        ctk.CTkFrame(mirror_card, height=1, fg_color=BORDER, corner_radius=0).pack(
            fill="x", padx=0, pady=(12, 0))

        self.summary_text = ctk.CTkTextbox(
            mirror_card, height=220,
            fg_color=BG_INPUT, text_color=FG,
            border_color=BORDER_SOFT, border_width=1,
            font=FONT_MONO, corner_radius=10,
        )
        self.summary_text.pack(fill="x", padx=20, pady=(12, 20))
        self.summary_text.insert("end", "Sign in to load your dashboard summary.")
        self.summary_text.configure(state="disabled")

        return frame

    # =========================================================================
    # TRADINGVIEW SETUP PANEL
    # =========================================================================
    def _build_tradingview_panel(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                       scrollbar_button_color=BORDER_SOFT,
                                       scrollbar_button_hover_color=BORDER_GLOW)

        _label(frame, "TradingView Setup", font=FONT_DISPLAY, color=FG).pack(
            anchor="w", padx=24, pady=(24, 4))
        _label(frame, "Build your alert message and copy it straight into TradingView.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=24, pady=(0, 24))

        # ── Two-column layout ─────────────────────────────────────────────────
        cols = ctk.CTkFrame(frame, fg_color="transparent")
        cols.pack(fill="both", expand=True, padx=24)
        cols.columnconfigure(0, weight=5)
        cols.columnconfigure(1, weight=7)

        left = ctk.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # ── LEFT: Step-by-step guide ──────────────────────────────────────────
        guide_card = _card(left)
        guide_card.pack(fill="x", pady=(0, 10))

        _label(guide_card, "How to set up alerts", font=FONT_LABEL, color=FG).pack(
            anchor="w", padx=20, pady=(20, 16))

        steps = [
            ("1", "Get your Webhook URL",
             "Find it in the Dashboard tab. Copy it."),
            ("2", "Open TradingView",
             "Go to a chart → click the Alert (bell) icon."),
            ("3", "Set alert condition",
             "Choose your indicator or strategy conditions."),
            ("4", "Set Webhook URL",
             "In Notifications → check Webhook URL → paste your URL."),
            ("5", "Set alert message",
             "Copy the JSON from the Message Builder and paste it into the Message field."),
            ("6", "Save and test",
             "Click Save. Trigger a test alert and watch the relay execute."),
        ]

        for num, title, desc in steps:
            step_row = ctk.CTkFrame(guide_card, fg_color="transparent")
            step_row.pack(fill="x", padx=20, pady=(0, 14))

            num_badge = ctk.CTkLabel(step_row, text=num,
                                     fg_color=PRIMARY_DK, text_color=PRIMARY_LT,
                                     font=(FONT_LABEL[0], 11, "bold"),
                                     width=26, height=26, corner_radius=13)
            num_badge.pack(side="left", padx=(0, 12))

            text_col = ctk.CTkFrame(step_row, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True)
            _label(text_col, title, font=(FONT_BODY[0], 11, "bold"), color=FG).pack(anchor="w")
            _label(text_col, desc, font=FONT_SMALL, color=FG_MUTED).pack(anchor="w")

        _divider(guide_card)

        # Quick webhook URL copy
        _label(guide_card, "Your Webhook URL", color=FG_MUTED, font=FONT_SMALL).pack(
            anchor="w", padx=20, pady=(0, 6))
        wh_row = ctk.CTkFrame(guide_card, fg_color="transparent")
        wh_row.pack(fill="x", padx=20, pady=(0, 20))
        ctk.CTkEntry(wh_row, textvariable=self.webhook_url_var,
                     fg_color=BG_INPUT, border_color=BORDER_SOFT,
                     text_color=PRIMARY_LT, font=FONT_MONO_SM,
                     height=36, corner_radius=10,
                     state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 8))
        _tv_wh_copy = _btn_primary(wh_row, "Copy", height=36, width=70,
                                   command=lambda: self._copy_to_clipboard(
                                       self.webhook_url_var.get(), _tv_wh_copy))
        _tv_wh_copy.pack(side="right")

        # Dynamic vars tip
        tip_card = _card(left, fg_color=GLASS_DARK)
        tip_card.pack(fill="x", pady=(0, 10))
        _label(tip_card, "TradingView Variables", font=(FONT_LABEL[0], 11, "bold"),
               color=ACCENT).pack(anchor="w", padx=16, pady=(14, 6))
        tips = [
            ("{{ticker}}", "Current chart symbol"),
            ("{{strategy.order.action}}", "BUY or SELL (strategy only)"),
            ("{{close}}", "Last close price"),
        ]
        for var, desc in tips:
            row = ctk.CTkFrame(tip_card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(0, 6))
            _label(row, var, font=FONT_MONO_SM, color=PRIMARY_LT).pack(side="left")
            _label(row, f"  — {desc}", font=FONT_SMALL, color=FG_MUTED).pack(side="left")
        ctk.CTkFrame(tip_card, height=8, fg_color="transparent").pack()

        # ── RIGHT: Message Builder ────────────────────────────────────────────
        builder_card = _card(right, gold=True)
        builder_card.pack(fill="x", pady=(0, 10))

        bld_hdr = ctk.CTkFrame(builder_card, fg_color="transparent")
        bld_hdr.pack(fill="x", padx=20, pady=(20, 0))
        _label(bld_hdr, "Alert Message Builder", font=FONT_HERO, color=FG).pack(side="left")
        _chip(bld_hdr, "LIVE PREVIEW", GLASS_GOLD, text_color=ACCENT_LT).pack(side="right")

        _label(builder_card, "Fill in the fields — JSON updates instantly as you type.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=20, pady=(4, 16))

        # Action
        _label(builder_card, "Action", color=FG_SOFT, font=FONT_SMALL).pack(
            anchor="w", padx=20, pady=(0, 4))
        action_frame = ctk.CTkFrame(builder_card, fg_color="transparent")
        action_frame.pack(fill="x", padx=20, pady=(0, 12))
        _action_colors = {
            "BUY":  (SUCCESS_BG, SUCCESS,  "#0A2010"),
            "SELL": (DANGER_BG,  DANGER,   "#200A06"),
        }
        for action_val in ["BUY", "SELL"]:
            is_sel = self.tv_action_var.get() == action_val
            bg_sel, fg_sel, bg_hov = _action_colors[action_val]
            btn = ctk.CTkButton(
                action_frame, text=action_val,
                height=40, width=88, corner_radius=10,
                fg_color=bg_sel if is_sel else GLASS,
                hover_color=bg_hov,
                text_color=fg_sel if is_sel else FG_MUTED,
                border_width=1,
                border_color=fg_sel if is_sel else BORDER,
                font=(FONT_BODY[0], 12, "bold"),
                command=lambda v=action_val: self._set_tv_action(v)
            )
            btn.pack(side="left", padx=(0, 8))
            if not hasattr(self, "_tv_action_btns"):
                self._tv_action_btns = {}
            self._tv_action_btns[action_val] = btn

        # Symbol / Size / SL / TP / Script in a grid
        fields_grid = ctk.CTkFrame(builder_card, fg_color="transparent")
        fields_grid.pack(fill="x", padx=20, pady=(0, 12))
        fields_grid.columnconfigure(0, weight=1)
        fields_grid.columnconfigure(1, weight=1)

        # Symbol field with quick-pick buttons
        sym_fc = ctk.CTkFrame(fields_grid, fg_color="transparent")
        sym_fc.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 8))
        _label(sym_fc, "Symbol", color=FG_SOFT, font=FONT_SMALL).pack(anchor="w", pady=(0, 4))
        _entry(sym_fc, self.tv_symbol_var, "{{ticker}} or EURUSD", height=38).pack(fill="x")
        sym_quick = ctk.CTkFrame(sym_fc, fg_color="transparent")
        sym_quick.pack(fill="x", pady=(4, 0))
        for sym in ["EURUSD", "XAUUSD", "BTCUSD", "{{ticker}}"]:
            ctk.CTkButton(
                sym_quick, text=sym, height=24,
                font=(FONT_SMALL[0], 9), corner_radius=6,
                fg_color=GLASS, hover_color=BORDER_SOFT,
                text_color=FG_MUTED, border_width=1, border_color=BORDER,
                command=lambda s=sym: self.tv_symbol_var.set(s)
            ).pack(side="left", padx=(0, 4))

        field_data = [
            ("Lot Size",    self.tv_size_var,   "0.1",                  0, 1),
            ("SL (pips)",   self.tv_sl_var,     "20  (optional)",       1, 0),
            ("TP (pips)",   self.tv_tp_var,     "40  (optional)",       1, 1),
            ("Script Name", self.tv_script_var, "MyStrategy (optional)", 2, 0),
        ]
        for label_text, var, ph, row_i, col_i in field_data:
            fc = ctk.CTkFrame(fields_grid, fg_color="transparent")
            fc.grid(row=row_i, column=col_i, sticky="ew",
                    padx=(0 if col_i == 0 else 6, 6 if col_i == 0 else 0),
                    pady=(0, 8))
            _label(fc, label_text, color=FG_SOFT, font=FONT_SMALL).pack(anchor="w", pady=(0, 4))
            _entry(fc, var, ph, height=38).pack(fill="x")

        # Use dynamic action variable checkbox
        dyn_row = ctk.CTkFrame(builder_card, fg_color="transparent")
        dyn_row.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkCheckBox(
            dyn_row,
            text='Use  {{strategy.order.action}}  for action (Pine Script strategies)',
            variable=self.tv_dynamic_var,
            text_color=FG_MUTED, font=FONT_SMALL,
            fg_color=PRIMARY, hover_color=PRIMARY_LT, checkmark_color=BG
        ).pack(side="left")

        # Preview
        _divider(builder_card)
        _label(builder_card, "Generated JSON  (copy into TradingView alert message)",
               color=FG_SOFT, font=FONT_SMALL).pack(anchor="w", padx=20, pady=(0, 8))

        self._tv_preview = ctk.CTkTextbox(
            builder_card, height=220,
            fg_color=BG_INPUT, text_color=PRIMARY_LT,
            border_color=BORDER_GLOW, border_width=1,
            font=FONT_MONO, corner_radius=10, wrap="none"
        )
        self._tv_preview.pack(fill="x", padx=20, pady=(0, 12))

        copy_row = ctk.CTkFrame(builder_card, fg_color="transparent")
        copy_row.pack(fill="x", padx=20, pady=(0, 20))

        self._tv_copy_btn = _btn_gold(copy_row, "Copy Alert Message  →", height=46,
                                      command=self._copy_tv_message)
        self._tv_copy_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        _btn_outline(copy_row, "Reset", height=46, width=80,
                     command=self._reset_tv_fields).pack(side="right")

        self._update_tv_preview()
        return frame

    # =========================================================================
    # INSTRUCTIONS PANEL
    # =========================================================================
    def _build_instructions_panel(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                       scrollbar_button_color=BORDER_SOFT,
                                       scrollbar_button_hover_color=BORDER_GLOW)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(28, 0))
        _label(hdr, "Quick Start Guide", font=FONT_DISPLAY, color=FG).pack(anchor="w")
        _label(frame,
               "Everything you need to go from download to live execution in minutes.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=24, pady=(6, 28))

        # ── Journey progress bar ──────────────────────────────────────────────
        journey_card = _card(frame, gold=True)
        journey_card.pack(fill="x", padx=24, pady=(0, 20))

        jc_inner = ctk.CTkFrame(journey_card, fg_color="transparent")
        jc_inner.pack(fill="x", padx=24, pady=20)

        steps_short = ["Sign In", "Connect MT5", "Get Webhook", "Set Alert", "Go Live"]
        jc_inner.columnconfigure(tuple(range(len(steps_short))), weight=1)

        for i, step_name in enumerate(steps_short):
            col = ctk.CTkFrame(jc_inner, fg_color="transparent")
            col.grid(row=0, column=i, sticky="nsew")

            # Connector line
            line_row = ctk.CTkFrame(col, fg_color="transparent", height=20)
            line_row.pack(fill="x")
            line_row.pack_propagate(False)
            if i > 0:
                ctk.CTkFrame(line_row, height=2, fg_color=GOLD_BORDER, corner_radius=0).pack(
                    side="left", fill="x", expand=True, pady=9)
            # Step circle
            circle = ctk.CTkLabel(line_row,
                                  text=str(i + 1),
                                  fg_color=GOLD_GLOW, text_color=GOLD_LT,
                                  font=(FONT_LABEL[0], 11, "bold"),
                                  width=24, height=24, corner_radius=12)
            circle.pack(side="left")
            if i < len(steps_short) - 1:
                ctk.CTkFrame(line_row, height=2, fg_color=GOLD_BORDER, corner_radius=0).pack(
                    side="left", fill="x", expand=True, pady=9)

            _label(col, step_name, color=FG_MUTED, font=(FONT_SMALL[0], 10)).pack(pady=(4, 0))

        # ── Expandable step sections ───────────────────────────────────────────
        sections = [
            {
                "num":   "01",
                "title": "Sign In to PlatAlgo",
                "color": PRIMARY_LT,
                "bg":    PRIMARY_GLOW,
                "icon":  "⊕",
                "quick": "Go to the Connect tab → Sign in with Google or Facebook OAuth.",
                "steps": [
                    ("Open the Connect tab", "Click Connect in the left sidebar."),
                    ("Choose OAuth", "Click Google or Facebook — no password needed."),
                    ("Browser opens", "Complete login in the browser window that appears."),
                    ("Auto-detected", "The app detects your login automatically within seconds."),
                ],
                "tip": "OAuth is the fastest and most secure way to sign in. Your credentials are never stored locally.",
            },
            {
                "num":   "02",
                "title": "Connect Your MT5 Broker",
                "color": GOLD_LT,
                "bg":    GOLD_GLOW,
                "icon":  "◉",
                "quick": "Enter your MT5 account number, password, and broker server name.",
                "steps": [
                    ("Find your broker server", "Open MT5 → File → Login → note the server name (e.g. ICMarkets-Live01)."),
                    ("Enter credentials", "Fill in Account Number, Password, and Server in the Connect tab."),
                    ("Choose execution mode", "VPS Mode = 24/7 cloud execution. Local Mode = this machine only."),
                    ("Activate", "Click Login to MT5 on VPS (recommended) or Connect Local MT5."),
                ],
                "tip": "VPS Mode runs your trades even when your computer is off. Highly recommended for strategies that trade around the clock.",
            },
            {
                "num":   "03",
                "title": "Get Your Webhook URL",
                "color": ACCENT_LT,
                "bg":    ACCENT_GLOW,
                "icon":  "◎",
                "quick": "Sign in first, then copy your Webhook URL from the Dashboard tab.",
                "steps": [
                    ("Open Dashboard tab", "Click Dashboard in the sidebar after signing in."),
                    ("Locate Webhook URL", "It appears in the Your Webhook URL card."),
                    ("Copy it", "Click the Copy button — the URL is now in your clipboard."),
                    ("Keep it private", "This URL is unique to your account. Don't share it publicly."),
                ],
                "tip": "Your webhook URL looks like: https://app.platalgo.com/webhook/your_user_id",
            },
            {
                "num":   "04",
                "title": "Configure TradingView Alert",
                "color": PRIMARY_LT,
                "bg":    PRIMARY_GLOW,
                "icon":  "◈",
                "quick": "Use the TradingView tab to build your alert message JSON, then paste into TradingView.",
                "steps": [
                    ("Open TradingView Setup tab", "Click TradingView in the sidebar."),
                    ("Build your message", "Fill in Symbol, Lot Size, SL, TP in the Message Builder."),
                    ("Copy JSON", "Click Copy Alert Message to copy the generated JSON."),
                    ("Go to TradingView", "On any chart, click the Alert (bell) icon → Create Alert."),
                    ("Set Webhook URL", "In Notifications → enable Webhook URL → paste your URL."),
                    ("Set Message", "In the Message box, paste your JSON payload."),
                    ("Save", "Click Save. Your alert is now wired to PlatAlgo."),
                ],
                "tip": "Use {{strategy.order.action}} for action if your alert comes from a Pine Script strategy — it auto-fills BUY or SELL.",
            },
            {
                "num":   "05",
                "title": "Go Live & Monitor",
                "color": ACCENT_LT,
                "bg":    ACCENT_GLOW,
                "icon":  "⊞",
                "quick": "Trigger a test alert and watch the execution log in Settings → Execution Logs.",
                "steps": [
                    ("Trigger a test", "In TradingView, use 'Add Alert' and trigger it once manually."),
                    ("Check Dashboard", "The Bridge, MT5, and Broker indicators should all be green."),
                    ("Check Execution Logs", "Go to Settings → Execution Logs to see the trade confirmation."),
                    ("Monitor live", "The Dashboard updates in real-time as signals arrive."),
                    ("Troubleshoot", "If a dot stays red, check Settings → Bridge URL and MT5 path."),
                ],
                "tip": "Always do a test with a micro lot (0.01) before running live. Check your broker's minimum lot size.",
            },
        ]

        for sec in sections:
            sec_card = _card(frame)
            sec_card.pack(fill="x", padx=24, pady=(0, 12))

            # Section header row
            hdr_row = ctk.CTkFrame(sec_card, fg_color="transparent")
            hdr_row.pack(fill="x", padx=20, pady=(18, 0))

            # Step badge
            badge = ctk.CTkLabel(hdr_row,
                                 text=sec["num"],
                                 fg_color=sec["bg"], text_color=sec["color"],
                                 font=(FONT_LABEL[0], 11, "bold"),
                                 width=34, height=34, corner_radius=10)
            badge.pack(side="left", padx=(0, 14))

            # Title + quick summary
            title_col = ctk.CTkFrame(hdr_row, fg_color="transparent")
            title_col.pack(side="left", fill="x", expand=True)
            _label(title_col, sec["title"], font=FONT_HERO, color=FG).pack(anchor="w")
            _label(title_col, sec["quick"], font=FONT_SMALL, color=FG_MUTED).pack(anchor="w")

            # Divider
            ctk.CTkFrame(sec_card, height=1, fg_color=BORDER, corner_radius=0).pack(
                fill="x", padx=0, pady=(14, 0))

            # Steps list
            steps_frame = ctk.CTkFrame(sec_card, fg_color="transparent")
            steps_frame.pack(fill="x", padx=20, pady=(12, 0))

            for step_i, (step_title, step_desc) in enumerate(sec["steps"]):
                step_row = ctk.CTkFrame(steps_frame, fg_color="transparent")
                step_row.pack(fill="x", pady=(0, 10))

                # Number dot
                num_dot = ctk.CTkLabel(step_row,
                                       text=str(step_i + 1),
                                       fg_color=GLASS, text_color=FG_SOFT,
                                       font=(FONT_CAPTION[0], 10, "bold"),
                                       width=22, height=22, corner_radius=11)
                num_dot.pack(side="left", padx=(0, 12))

                # Step text
                text_col = ctk.CTkFrame(step_row, fg_color="transparent")
                text_col.pack(side="left", fill="x", expand=True)
                _label(text_col, step_title,
                       font=(FONT_BODY[0], 11, "bold"), color=FG).pack(anchor="w")
                _label(text_col, step_desc,
                       font=FONT_SMALL, color=FG_MUTED).pack(anchor="w")

            # Pro tip
            tip_row = ctk.CTkFrame(sec_card,
                                   fg_color=sec["bg"],
                                   corner_radius=10,
                                   border_width=1, border_color=BORDER_GOLD)
            tip_row.pack(fill="x", padx=20, pady=(10, 18))

            tip_inner = ctk.CTkFrame(tip_row, fg_color="transparent")
            tip_inner.pack(fill="x", padx=14, pady=12)
            _label(tip_inner, "⚡ Pro tip", font=(FONT_CAPTION[0], 10, "bold"),
                   color=sec["color"]).pack(anchor="w", pady=(0, 4))
            _label(tip_inner, sec["tip"],
                   font=FONT_SMALL, color=FG_MUTED).pack(anchor="w")

        # ── FAQ section ───────────────────────────────────────────────────────
        _label(frame, "Common Questions",
               font=FONT_TITLE, color=FG).pack(anchor="w", padx=24, pady=(16, 4))
        _label(frame, "Quick answers to the most frequent setup issues.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=24, pady=(0, 16))

        faqs = [
            ("My MT5 won't connect — what do I check?",
             "Verify your Account Number (login), Password, and Server name match exactly what you see in MetaTrader 5 → File → Login. The server name is case-sensitive (e.g. 'ICMarkets-Live01', not 'icmarkets-live01')."),
            ("I don't see my signals executing — why?",
             "Check the Dashboard tab — all three dots (Bridge, MT5, Broker) must be green. Also verify your TradingView alert message matches the JSON format in the TradingView Setup tab exactly, including the correct api_key."),
            ("What's the difference between VPS Mode and Local Mode?",
             "VPS Mode runs MT5 on our cloud server — trades execute 24/7 even when your computer is off. Local Mode connects to an MT5 terminal running on this machine — stops when you close MT5 or shut down."),
            ("Is my MT5 password secure?",
             "Yes. Passwords are encrypted before storage using AES-256 and are never transmitted in plain text. OAuth tokens (Google/Facebook) replace passwords entirely for dashboard access."),
            ("The app says 'Bridge Offline' — what does that mean?",
             "The app can't reach the PlatAlgo server. Check your internet connection, or go to Settings and verify the Bridge URL is set to https://app.platalgo.com"),
        ]

        for q, a in faqs:
            faq_card = _card(frame)
            faq_card.pack(fill="x", padx=24, pady=(0, 8))

            fq = ctk.CTkFrame(faq_card, fg_color="transparent")
            fq.pack(fill="x", padx=20, pady=(16, 0))
            ctk.CTkLabel(fq, text="?", fg_color=PRIMARY_GLOW, text_color=PRIMARY_LT,
                         font=(FONT_LABEL[0], 11, "bold"),
                         width=26, height=26, corner_radius=13).pack(side="left", padx=(0, 12))
            _label(fq, q, font=(FONT_BODY[0], 12, "bold"), color=FG).pack(side="left", anchor="w")

            ctk.CTkFrame(faq_card, height=1, fg_color=BORDER, corner_radius=0).pack(
                fill="x", padx=0, pady=(10, 0))
            _label(faq_card, a, font=FONT_SMALL, color=FG_MUTED, wraplength=700).pack(
                anchor="w", padx=20, pady=(10, 18))

        # ── Quick actions ─────────────────────────────────────────────────────
        qa_row = ctk.CTkFrame(frame, fg_color="transparent")
        qa_row.pack(fill="x", padx=24, pady=(8, 32))
        qa_row.columnconfigure(0, weight=1)
        qa_row.columnconfigure(1, weight=1)
        qa_row.columnconfigure(2, weight=1)

        _btn_primary(qa_row, "→ Go to Connect",
                     lambda: self._switch_panel("connect"),
                     height=42).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        _btn_gold(qa_row, "→ Open Dashboard",
                  lambda: self._switch_panel("dashboard"),
                  height=42).grid(row=0, column=1, sticky="ew", padx=3)
        _btn_outline(qa_row, "→ TradingView Setup",
                     lambda: self._switch_panel("tradingview"),
                     height=42).grid(row=0, column=2, sticky="ew", padx=(6, 0))

        return frame

    # =========================================================================
    # SETTINGS PANEL
    # =========================================================================
    def _build_settings_panel(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkScrollableFrame(parent, fg_color="transparent",
                                       scrollbar_button_color=BORDER_SOFT,
                                       scrollbar_button_hover_color=BORDER_GLOW)

        _label(frame, "Settings", font=FONT_DISPLAY, color=FG).pack(
            anchor="w", padx=24, pady=(24, 4))
        _label(frame, "Advanced configuration and execution logs.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=24, pady=(0, 24))

        # ── Connection Settings ───────────────────────────────────────────────
        conn_card = _card(frame)
        conn_card.pack(fill="x", padx=24, pady=(0, 16))

        _section_header(conn_card, "Bridge Connection",
                        "Override only if using a self-hosted bridge server.")

        _label(conn_card, "Bridge URL", color=FG_MUTED, font=FONT_SMALL).pack(
            anchor="w", padx=20, pady=(0, 4))
        _entry(conn_card, self.bridge_url_var, "https://app.platalgo.com").pack(
            fill="x", padx=20, pady=(0, 20))

        # ── MT5 Settings (Windows only) ───────────────────────────────────────
        if IS_WINDOWS:
            mt5_card = _card(frame)
            mt5_card.pack(fill="x", padx=24, pady=(0, 16))

            _section_header(mt5_card, "MT5 Terminal Path",
                            "Path to terminal64.exe. Auto-detected if left blank.")

            _label(mt5_card, "Terminal Path", color=FG_MUTED, font=FONT_SMALL).pack(
                anchor="w", padx=20, pady=(0, 4))
            path_row = ctk.CTkFrame(mt5_card, fg_color="transparent")
            path_row.pack(fill="x", padx=20, pady=(0, 20))
            _entry(path_row, self.mt5_path_var, r"C:\Program Files\MetaTrader 5\terminal64.exe").pack(
                side="left", fill="x", expand=True, padx=(0, 8))
            _btn_outline(path_row, "Auto-detect", height=44, width=110,
                         command=lambda: self.mt5_path_var.set(detect_mt5_path())).pack(side="right")

        # ── Startup ───────────────────────────────────────────────────────────
        startup_card = _card(frame)
        startup_card.pack(fill="x", padx=24, pady=(0, 16))

        _section_header(startup_card, "Startup Behavior",
                        "Control whether the relay launches automatically with your system.")

        startup_row = ctk.CTkFrame(startup_card, fg_color="transparent")
        startup_row.pack(fill="x", padx=20, pady=(0, 24))
        ctk.CTkSwitch(
            startup_row,
            text="Launch relay on system startup",
            variable=self.startup_var,
            command=self._toggle_startup,
            text_color=FG_SOFT, font=FONT_BODY,
            button_color=PRIMARY, button_hover_color=PRIMARY_LT,
            progress_color=PRIMARY_DK,
        ).pack(side="left")

        # ── Danger Zone ───────────────────────────────────────────────────────
        danger_card = _card(frame, fg_color=DANGER_BG, border_color=DANGER_BORDER)
        danger_card.pack(fill="x", padx=24, pady=(0, 16))

        _label(danger_card, "Danger Zone", font=FONT_LABEL, color=DANGER).pack(
            anchor="w", padx=20, pady=(20, 4))
        _label(danger_card, "Stop all active connections. This will interrupt any running relay loops.",
               color=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=20, pady=(0, 12))

        dz_row = ctk.CTkFrame(danger_card, fg_color="transparent")
        dz_row.pack(fill="x", padx=20, pady=(0, 20))
        _btn_danger(dz_row, "Stop All Connections", self.stop_relay, height=44).pack(
            side="left", padx=(0, 10))
        _btn_danger(dz_row, "Disable VPS Mode", self.disable_managed_mode, height=44).pack(side="left")

        # ── Execution Logs ────────────────────────────────────────────────────
        log_card = _card(frame)
        log_card.pack(fill="x", padx=24, pady=(0, 24))

        log_hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        log_hdr.pack(fill="x", padx=20, pady=(20, 0))
        _label(log_hdr, "Execution Logs", font=FONT_LABEL, color=FG).pack(side="left")
        _btn_outline(log_hdr, "Clear", height=32, width=72,
                     command=self._clear_logs).pack(side="right")

        ctk.CTkFrame(log_card, height=1, fg_color=BORDER, corner_radius=0).pack(
            fill="x", padx=0, pady=(12, 0))

        self.log_box = ctk.CTkTextbox(
            log_card, height=200,
            fg_color=BG_INPUT, text_color=FG_MUTED,
            border_color=BORDER_SOFT, border_width=1,
            font=FONT_MONO_SM, corner_radius=12, wrap="word"
        )
        self.log_box.pack(fill="x", padx=20, pady=(12, 20))

        return frame

    # =========================================================================
    # UI Helpers & Actions
    # =========================================================================
    def _set_tv_action(self, value: str):
        self.tv_action_var.set(value)
        _action_colors = {
            "BUY":  (SUCCESS_BG, SUCCESS,  BORDER),
            "SELL": (DANGER_BG,  DANGER,   BORDER),
        }
        for v, btn in self._tv_action_btns.items():
            active = v == value
            bg_sel, fg_sel, _ = _action_colors[v]
            btn.configure(
                fg_color=bg_sel if active else GLASS,
                text_color=fg_sel if active else FG_MUTED,
                border_color=fg_sel if active else BORDER,
            )

    def _update_tv_preview(self):
        if not self._tv_preview:
            return
        uid = self.user_id_var.get().strip() or "your_username"
        ak  = self.api_key_var.get().strip() or "your_api_key"
        action = ("{{strategy.order.action}}"
                  if self.tv_dynamic_var.get()
                  else self.tv_action_var.get())
        symbol = self.tv_symbol_var.get().strip() or "{{ticker}}"
        msg = {
            "user_id": uid,
            "api_key": ak,
            "action":  action,
            "symbol":  symbol,
        }
        try:
            size = float(self.tv_size_var.get())
            msg["size"] = size
        except ValueError:
            pass
        sl = self.tv_sl_var.get().strip()
        if sl:
            try:
                msg["sl"] = float(sl)
            except ValueError:
                pass
        tp = self.tv_tp_var.get().strip()
        if tp:
            try:
                msg["tp"] = float(tp)
            except ValueError:
                pass
        sc = self.tv_script_var.get().strip()
        if sc:
            msg["script_name"] = sc

        preview = json.dumps(msg, indent=2)
        self._tv_preview.configure(state="normal")
        self._tv_preview.delete("1.0", "end")
        self._tv_preview.insert("end", preview)
        self._tv_preview.configure(state="disabled")

    def _copy_tv_message(self):
        if not self._tv_preview:
            return
        self._tv_preview.configure(state="normal")
        text = self._tv_preview.get("1.0", "end").strip()
        self._tv_preview.configure(state="disabled")
        self._copy_to_clipboard(text, self._tv_copy_btn)

    def _reset_tv_fields(self):
        self.tv_symbol_var.set("{{ticker}}")
        self.tv_size_var.set("0.1")
        self.tv_sl_var.set("")
        self.tv_tp_var.set("")
        self.tv_script_var.set("")
        self.tv_dynamic_var.set(True)
        self._set_tv_action("BUY")

    def _copy_to_clipboard(self, text: str, btn=None):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
        except Exception:
            pass
        if btn:
            orig = btn.cget("text")
            btn.configure(text="✓ Copied!", fg_color=PRIMARY_DK, text_color=PRIMARY_LT)
            self.root.after(2000, lambda: btn.configure(
                text=orig, fg_color=GLASS_GOLD if "→" in orig else "transparent",
                text_color=ACCENT_LT if "→" in orig else FG_SOFT))

    def _toggle_api_key_reveal(self):
        self.api_key_visible = not self.api_key_visible
        if self._apikey_entry:
            self._apikey_entry.configure(show="" if self.api_key_visible else "•")
        if self._ak_reveal_btn:
            self._ak_reveal_btn.configure(text="Hide" if self.api_key_visible else "Show")

    def _clear_logs(self):
        if self.log_box:
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.configure(state="disabled")

    def toggle_advanced(self):
        """Kept for compatibility."""
        self._switch_panel("settings")

    def toggle_logs(self):
        """Kept for compatibility."""
        self._switch_panel("settings")

    def _set_dot(self, name: str, online: bool):
        color    = SUCCESS if online else DANGER
        bg_color = SUCCESS_BG if online else DANGER_BG
        label    = "Online" if online else "Offline"
        letter   = "●" if online else "—"
        # Update dashboard ring card
        if name in self.status_dots:
            ring_lbl, status_lbl = self.status_dots[name]
            ring_lbl.configure(text=letter, text_color=color)
            status_lbl.configure(text=label, text_color=color)
        if hasattr(self, "_status_rings") and name in self._status_rings:
            self._status_rings[name].configure(border_color=color, fg_color=bg_color)
        # Update header pill dot
        if name in self._header_dots:
            hdot, hlbl = self._header_dots[name]
            hdot.configure(text_color=color)
            hlbl.configure(text_color=FG_SOFT if online else FG_MUTED,
                           text=f"{name}: {label}")

    def _set_status(self, bridge=None, mt5=None, broker=None):
        if bridge is not None: self._set_dot("Bridge", bridge)
        if mt5    is not None: self._set_dot("MT5",    mt5)
        if broker is not None: self._set_dot("Broker", broker)
        any_on = any(x is True for x in (bridge, mt5, broker))
        all_on = all(x is True for x in (bridge, mt5, broker) if x is not None)
        color = SUCCESS if any_on else DANGER
        if self._live_dot:
            self._live_dot.configure(text_color=color)
        if hasattr(self, "_live_dot2"):
            self._live_dot2.configure(text_color=color)
        if hasattr(self, "_latency_badge"):
            if all_on:
                self._latency_badge.configure(text="● LIVE", text_color=SUCCESS)
            elif any_on:
                self._latency_badge.configure(text="◐ PARTIAL", text_color=GOLD_LT)
            else:
                self._latency_badge.configure(text="○ OFFLINE", text_color=FG_FAINT)

    def _set_state_callback(self, state: dict):
        self.root.after(0, lambda: self._set_status(
            bridge=bool(state.get("cloud_connected")),
            mt5=bool(state.get("mt5_connected")),
            broker=bool(state.get("broker_connected")),
        ))

    def append_log(self, text: str):
        if self.log_box:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

    def update_status(self, text: str):
        self.root.after(0, lambda: self.status_var.set(text))
        self.root.after(0, lambda: self.append_log(text))

    # ── Credentials ───────────────────────────────────────────────────────────
    def _save_cached_credentials(self, user_id: str, password: str):
        if not self.remember_var.get():
            return
        if keyring:
            keyring.set_password(KEYRING_SERVICE, user_id, password)
        data = {"user_id": user_id}
        if self._oauth_provider:
            data["oauth_provider"] = self._oauth_provider
        if self.mt5_acct_var.get():
            data["mt5_acct"]   = self.mt5_acct_var.get()
            data["mt5_server"] = self.mt5_server_var.get()
        try:
            with open(LAST_USER_FILE, "w") as f:
                json.dump(data, f)
        except OSError as exc:
            import logging
            logging.getLogger(__name__).warning(f"Could not save credentials cache: {exc}")

    def _save_oauth_credentials(self, user_id: str, provider: str):
        """Always saves OAuth state (no remember_var check)."""
        data = {"user_id": user_id, "oauth_provider": provider}
        if self.mt5_acct_var.get():
            data["mt5_acct"]   = self.mt5_acct_var.get()
            data["mt5_server"] = self.mt5_server_var.get()
        try:
            with open(LAST_USER_FILE, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

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
            provider = data.get("oauth_provider", "")
            if provider and uid:
                self._oauth_provider = provider
                self.root.after(50, lambda: self._on_oauth_success(provider, uid, from_cache=True))
        except Exception:
            pass

    def _auto_connect_if_cached(self):
        uid = self.user_id_var.get().strip()
        pw  = self.password_var.get()
        if uid and pw:
            if IS_WINDOWS:
                self.root.after(600, self.start_relay)
            else:
                self.root.after(600, self._do_refresh)
        elif uid and self._oauth_provider:
            # OAuth user — refresh dashboard (no password needed, api_key will be None until re-auth)
            self.root.after(600, self._do_refresh)

    # ── Startup ───────────────────────────────────────────────────────────────
    def _toggle_startup(self):
        try:
            if self.startup_var.get():
                _enable_startup()
                self.update_status("Start-on-boot enabled")
            else:
                _disable_startup()
                self.update_status("Start-on-boot disabled")
        except Exception as e:
            self.update_status(f"Startup error: {e}")
            self.startup_var.set(_startup_enabled())

    # ── MT5 credentials ───────────────────────────────────────────────────────
    def _get_mt5_creds(self) -> dict:
        """Return MT5 credentials from GUI fields only. No config.json fallback."""
        return {
            "login":    self.mt5_acct_var.get().strip(),
            "password": self.mt5_pw_var.get(),
            "server":   self.mt5_server_var.get().strip(),
            "path":     self.mt5_path_var.get().strip() if IS_WINDOWS else "",
        }

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
            resp = requests.post(f"{base}/auth/desktop/start",
                                 json={"provider": provider}, timeout=8)
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
            messagebox.showerror(
                "Cannot connect to OAuth",
                f"Could not reach the PlatAlgo server.\n\n"
                f"Bridge URL: {base}\n\n"
                f"Check your internet connection or verify the Bridge URL in Settings.\n\n"
                f"Details: {exc}"
            )
            return

        self.update_status(f"Login with {provider.title()}…")
        threading.Thread(target=self._poll_desktop_token, args=(state, provider), daemon=True).start()

        if webview:
            def launch_webview():
                window = webview.create_window("PlatAlgo Login", auth_url,
                                               width=1024, height=760, resizable=True)
                if hasattr(window, "events"):
                    window.events.closed += lambda: self.root.after(
                        0, lambda: self.update_status("Login window closed"))
                webview.start()
            threading.Thread(target=launch_webview, daemon=True).start()
        else:
            messagebox.showinfo("Opening browser",
                                "Install 'pywebview' to keep login inside the app.")
            webbrowser.open(auth_url)

    def _poll_desktop_token(self, state: str, provider: str = ""):
        base = self.bridge_url_var.get().rstrip("/")
        for i in range(180):
            try:
                resp = requests.get(f"{base}/auth/desktop/consume/{state}", timeout=6)
                if resp.status_code == 200:
                    data    = resp.json()
                    uid     = data.get("user_id", "")
                    api_key = data.get("api_key", "")
                    if uid and api_key:
                        self.api_key = api_key
                        self.api_key_var.set(api_key)
                        self.password_var.set("")
                        self.user_id_var.set(uid)
                        self._avatar.configure(text=uid[:2].upper())
                        self.root.after(0, lambda p=provider, u=uid: self._on_oauth_success(p, u))
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

    def _on_oauth_success(self, provider: str, uid: str, from_cache: bool = False):
        """Show logged-in banner and hide the username/password form."""
        self._oauth_provider = provider
        if not from_cache:
            self._save_oauth_credentials(uid, provider)
        icon = "G" if provider == "google" else "f" if provider == "facebook" else "●"
        provider_name = provider.title()
        if self._oauth_provider_lbl:
            self._oauth_provider_lbl.configure(
                text=f"Signed in via {provider_name}"
            )
        if self._oauth_provider_icon:
            self._oauth_provider_icon.configure(
                text=icon,
                text_color=PRIMARY_LT if provider == "google" else ACCENT_LT
            )
        if self._oauth_user_lbl:
            self._oauth_user_lbl.configure(text=uid)
        if self._login_form_inner:
            self._login_form_inner.pack_forget()
        if self._oauth_logged_in_frame:
            self._oauth_logged_in_frame.pack(fill="x")

    def _sign_out_oauth(self):
        """Clear OAuth state and show the login form again."""
        self._oauth_provider = None
        self.api_key = None
        self.api_key_var.set("")
        self.password_var.set("")
        # Remove cached OAuth state
        try:
            if os.path.exists(LAST_USER_FILE):
                with open(LAST_USER_FILE) as f:
                    data = json.load(f) or {}
                data.pop("oauth_provider", None)
                with open(LAST_USER_FILE, "w") as f:
                    json.dump(data, f)
        except Exception:
            pass
        if self._oauth_logged_in_frame:
            self._oauth_logged_in_frame.pack_forget()
        if self._login_form_inner:
            self._login_form_inner.pack(fill="x")
        self.update_status("Signed out")

    def start_relay(self):
        user_id  = self.user_id_var.get().strip()
        password = self.password_var.get()
        if not user_id or not (password or self.api_key):
            messagebox.showerror("Missing fields", "Provide password or complete OAuth login.")
            return
        if password:
            self._save_cached_credentials(user_id, password)
        self._avatar.configure(text=user_id[:2].upper())
        bridge = self.bridge_url_var.get().strip() or PRODUCTION_BRIDGE_URL
        mt5    = self._get_mt5_creds()
        self.relay = Relay(
            bridge, user_id, password,
            api_key=self.api_key,
            mt5_login=mt5.get("login") or None,
            mt5_password=mt5.get("password") or None,
            mt5_server=mt5.get("server") or None,
            mt5_path=mt5.get("path") or None,
        )
        if not self.relay.executor.get_connection_state().get("mt5_connected"):
            self.update_status("MT5 not connected — open your MT5 terminal or use VPS mode")
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
            messagebox.showerror("Missing fields",
                                 "Sign in first (username/password or Google/Facebook).")
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
                    api_key, mt5, mt5_path_override=mt5.get("path") or None)
            else:
                ok = client.setup_managed_execution_with_login(
                    password, mt5, mt5_path_override=mt5.get("path") or None)
            if ok is True:
                self.update_status("VPS 24/7 mode active — cloud is trading on your behalf")
                self._set_status(bridge=True, mt5=True, broker=True)
                self.vps_active = True
                def _activate():
                    self.vps_btn.configure(
                        text="✓  VPS Active — 24/7",
                        fg_color=GLASS_EMERALD, hover_color=PRIMARY_DK,
                        border_color=BORDER_GLOW, text_color=PRIMARY_LT, state="normal"
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
                self.root.after(0, lambda: messagebox.showerror("VPS Setup Failed", err_detail))
                self.root.after(0, lambda: self.vps_btn.configure(
                    text="Login to MT5 on VPS  →",
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
                            text="● VPS INACTIVE", fg_color=GLASS, text_color=FG_FAINT)
                    self.vps_btn.configure(
                        text="Login to MT5 on VPS  →",
                        fg_color=GLASS_GOLD, hover_color=ACCENT_DK,
                        border_color=ACCENT_DK, text_color=ACCENT_LT, state="normal"
                    )
                    if self.vps_card:
                        self.vps_card.configure(border_color=BORDER_GOLD)
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
        if hasattr(self, "connect_btn") and self.connect_btn:
            self.connect_btn.configure(state="normal")
        if self.vps_btn:
            self.vps_btn.configure(
                text="Login to MT5 on VPS  →",
                fg_color=GLASS_GOLD, hover_color=ACCENT_DK,
                border_color=ACCENT_DK, text_color=ACCENT_LT
            )
        if self.vps_status_chip:
            self.vps_status_chip.configure(
                text="● VPS INACTIVE", fg_color=GLASS, text_color=FG_FAINT)
        if self.vps_card:
            self.vps_card.configure(border_color=BORDER_GOLD)
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
                json=payload, timeout=8,
            )
            if resp.status_code != 200:
                return

            self.root.after(0, lambda: self._set_dot("Bridge", True))

            d       = resp.json()
            dash    = d.get("dashboard", {})
            scripts = dash.get("scripts", [])

            # Update webhook URL
            wh_url = d.get("webhook_url", "")
            if wh_url:
                self.root.after(0, lambda u=wh_url: self.webhook_url_var.set(u))

            # Update API key if returned
            ak = d.get("api_key", "")
            if ak and not self.api_key:
                self.api_key = ak
                self.root.after(0, lambda k=ak: self.api_key_var.set(k))

            # Build summary text
            lines = [
                f"Account      : {uid}",
                f"Webhook URL  : {wh_url}",
                f"Relays       : {dash.get('relay_online', 0)}/{dash.get('relay_total', 0)} online",
                f"Scripts      : {len(scripts)}",
            ]
            if scripts:
                lines.append("")
                lines.append("── Script Performance ──")
                for s in scripts:
                    lines.append(
                        f"  {s.get('script_name', '—'):<24} "
                        f"{s.get('executed_count', 0)} executed  /  "
                        f"{s.get('signals_count', 0)} signals"
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
                f"{self.bridge_url_var.get().rstrip('/')}/version", timeout=5)
            if resp.status_code != 200:
                return
            info   = resp.json()
            latest = info.get("version") or info.get("app_version", "")
            url    = info.get("windows_url" if IS_WINDOWS else "mac_url") or \
                     info.get("relay_download_url", "")
            if latest and latest != APP_VERSION and url:
                self.root.after(0, lambda v=latest, u=url: self._prompt_update(v, u))
        except Exception:
            pass

    def _prompt_update(self, version: str, url: str):
        """Show a polished update-available dialog."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Update Available")
        dlg.resizable(False, False)
        dlg.geometry("420x220")
        if hasattr(dlg, "configure"):
            dlg.configure(fg_color=BG_ELEVATED)
        dlg.grab_set()
        dlg.lift()

        _label(dlg, "Update Available", font=FONT_HERO, color=FG).pack(pady=(28, 4))
        _label(dlg, f"v{version} is ready  —  you're on v{APP_VERSION}",
               color=FG_MUTED, font=FONT_SMALL).pack()

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(pady=28)

        def _start_update():
            dlg.destroy()
            self._download_and_install(url, version)

        _btn_primary(btn_row, "Update Now  →", _start_update, width=160).pack(side="left", padx=(0, 10))
        _btn_outline(btn_row, "Later", dlg.destroy, width=90).pack(side="left")

    def _download_and_install(self, url: str, version: str):
        """Download the new binary with a progress bar, then self-replace."""
        import tempfile

        prog_win = ctk.CTkToplevel(self.root)
        prog_win.title("Downloading Update")
        prog_win.resizable(False, False)
        prog_win.geometry("420x160")
        if hasattr(prog_win, "configure"):
            prog_win.configure(fg_color=BG_ELEVATED)
        prog_win.grab_set()
        prog_win.lift()

        status_lbl = _label(prog_win, f"Downloading v{version}…", color=FG_MUTED, font=FONT_SMALL)
        status_lbl.pack(pady=(28, 10))

        bar = ctk.CTkProgressBar(prog_win, width=360, height=12,
                                 fg_color=GLASS, progress_color=PRIMARY,
                                 corner_radius=6)
        bar.set(0)
        bar.pack(padx=30)

        pct_lbl = _label(prog_win, "0%", color=FG_FAINT, font=FONT_SMALL)
        pct_lbl.pack(pady=(6, 0))

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
                                pct = done / total
                                self.root.after(0, lambda p=pct: bar.set(p))
                                self.root.after(0, lambda p=pct: pct_lbl.configure(
                                    text=f"{int(p * 100)}%"))
                self.root.after(0, lambda: status_lbl.configure(text="Installing…"))
                self.root.after(0, lambda: bar.set(1.0))
                self.root.after(0, lambda: pct_lbl.configure(text="100%"))
                self.root.after(500, lambda: self._apply_update(dest))
            except Exception as exc:
                self.root.after(0, lambda: prog_win.destroy())
                self.root.after(0, lambda: messagebox.showerror(
                    "Update failed", f"Could not download update:\n{exc}"))

        threading.Thread(target=_run, daemon=True).start()

    def _apply_update(self, dest: str):
        """Replace the running binary and relaunch (Windows), or open DMG (Mac)."""
        if IS_WINDOWS:
            import tempfile
            current_exe = sys.executable if getattr(sys, "frozen", False) else ""
            if not current_exe:
                # Running from source — just open download folder
                webbrowser.open(os.path.dirname(dest))
                return
            bat = os.path.join(tempfile.gettempdir(), "platalgo_update.bat")
            with open(bat, "w") as f:
                f.write(
                    f'@echo off\r\n'
                    f'timeout /t 2 /nobreak >nul\r\n'
                    f'move /y "{dest}" "{current_exe}"\r\n'
                    f'start "" "{current_exe}"\r\n'
                    f'del "%~f0"\r\n'
                )
            subprocess.Popen(
                ["cmd", "/c", bat],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            sys.exit(0)
        else:
            # Mac — open the downloaded DMG
            subprocess.run(["open", dest], check=False)
            messagebox.showinfo("Update downloaded",
                                "The installer has been opened. Drag PlatAlgoRelay to Applications to complete the update.")

    # ── Tray ──────────────────────────────────────────────────────────────────
    def _create_tray_icon(self):
        if not pystray or not Image or not ImageDraw:
            return None
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        try:
            img = Image.open(icon_path).convert("RGBA").resize((64, 64), Image.LANCZOS)
        except Exception:
            img  = Image.new("RGB", (64, 64), color=(0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse((8, 8, 56, 56), fill=(10, 132, 255))
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
