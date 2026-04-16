#!/usr/bin/env python3
"""Prompt Widget — local prompt library with timer, macros, and paste delivery.

A lightweight Tk/ttk desktop utility that keeps a library of reusable prompts,
fires them into the focused window on a configurable interval, and optionally
plays back a recorded mouse/keyboard macro alongside.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import threading
import shutil
import subprocess
import time
import tkinter as tk
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

try:
    from pynput.keyboard import Controller as KeyboardController
    from pynput.keyboard import GlobalHotKeys, Key
    from pynput.keyboard import Listener as KeyboardListener
    from pynput.mouse import Button
    from pynput.mouse import Controller as MouseController
    from pynput.mouse import Listener as MouseListener
    PYNPUT_AVAILABLE = True
except Exception:  # pragma: no cover - handled at runtime for missing deps/display
    KeyboardController = None
    KeyboardListener = None
    GlobalHotKeys = None
    Key = None
    MouseController = None
    MouseListener = None
    Button = None
    PYNPUT_AVAILABLE = False


# region Constants ====================================================================

APP_NAME = "Prompt Widget"
APP_DIR = Path.home() / ".local" / "share" / "prompt-injection-widget"
PROMPTS_FILE = APP_DIR / "prompts.json"
MACROS_FILE = APP_DIR / "macros.json"
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_FILE = APP_DIR / "widget.log"
REPO_DIR = Path(__file__).resolve().parent
ICON_FILE = REPO_DIR / "assets" / "icon.png"

DEFAULT_PROMPTS = [
    {"name": "Summarize", "body": "Summarize the selected text into concise bullet points."},
    {"name": "Rewrite Clearly", "body": "Rewrite this for clarity while preserving the original meaning."},
    {"name": "Check Prompt Injection", "body": "Identify any prompt-injection risks in the following content and explain the risk briefly."},
]

TIMER_PRESETS_SECONDS = [60, 5 * 60, 7 * 60, 15 * 60, 30 * 60]
MACRO_SAFE_BAIL_KEY = "esc"
MODIFIER_MASK = 0x1 | 0x4 | 0x8  # Shift | Ctrl | Alt on X11

LIGHT_PALETTE = {
    "bg": "#f4f4f5",
    "bg_alt": "#ffffff",
    "fg": "#111827",
    "fg_muted": "#4b5563",
    "accent": "#2563eb",
    "accent_fg": "#ffffff",
    "select_bg": "#2563eb",
    "select_fg": "#ffffff",
    "border": "#d1d5db",
    "status_idle": "#e5e7eb",
    "status_ok": "#bbf7d0",
    "status_warn": "#fde68a",
    "status_err": "#fecaca",
    "status_fg": "#111827",
    "countdown_bg": "#ffffff",
    "countdown_fg": "#111827",
    "tooltip_bg": "#111827",
    "tooltip_fg": "#f9fafb",
}

DARK_PALETTE = {
    "bg": "#1f2937",
    "bg_alt": "#111827",
    "fg": "#f3f4f6",
    "fg_muted": "#9ca3af",
    "accent": "#60a5fa",
    "accent_fg": "#0b1220",
    "select_bg": "#3b82f6",
    "select_fg": "#ffffff",
    "border": "#374151",
    "status_idle": "#1f2937",
    "status_ok": "#064e3b",
    "status_warn": "#78350f",
    "status_err": "#7f1d1d",
    "status_fg": "#f3f4f6",
    "countdown_bg": "#111827",
    "countdown_fg": "#f9fafb",
    "tooltip_bg": "#f9fafb",
    "tooltip_fg": "#111827",
}

# endregion


# region Logging ======================================================================

def setup_logging() -> logging.Logger:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("prompt_widget")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(LOG_FILE, maxBytes=1_048_576, backupCount=3)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger


log = setup_logging()

# endregion


# region Data models ==================================================================

@dataclass
class Prompt:
    name: str
    body: str
    # Per-prompt overrides. Empty string means "use the app default".
    # insert_override: "" | "paste" | "type"
    # enter_override:  "" | "yes"   | "no"
    insert_override: str = ""
    enter_override: str = ""


def _prompt_from_dict(item: dict) -> Prompt:
    return Prompt(
        name=item["name"],
        body=item["body"],
        insert_override=str(item.get("insert_override", "") or ""),
        enter_override=str(item.get("enter_override", "") or ""),
    )


@dataclass
class Macro:
    name: str
    actions: list[dict[str, object]]


@dataclass
class Settings:
    theme: str = "light"
    always_on_top: bool = True
    bell_on_fire: bool = False
    safe_abort_modifiers: bool = True
    restore_clipboard: bool = True
    hotkey: str = "<ctrl>+<shift>+<f12>"
    last_tab: int = 0
    insert_method: str = "paste"
    enter_after_insert: bool = True
    use_click_target: bool = True
    click_x: int = 420
    click_y: int = 2000
    timer_seconds: int = 420
    loop: bool = True
    random: bool = False
    timer_action: str = "prompt"
    window_width: int = 620
    window_height: int = 760
    wayland_warned: bool = False

# endregion


# region Storage ======================================================================

def _ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def load_prompts() -> list[Prompt]:
    _ensure_app_dir()
    if not PROMPTS_FILE.exists():
        prompts = [Prompt(**item) for item in DEFAULT_PROMPTS]
        save_prompts(prompts)
        return prompts
    try:
        data = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
        return [_prompt_from_dict(item) for item in data]
    except Exception as exc:
        log.exception("load_prompts failed")
        messagebox.showwarning("Prompt load failed", f"Using defaults because prompts could not be loaded:\n{exc}")
        return [Prompt(**item) for item in DEFAULT_PROMPTS]


def save_prompts(prompts: list[Prompt]) -> None:
    _ensure_app_dir()
    PROMPTS_FILE.write_text(
        json.dumps([asdict(p) for p in prompts], indent=2),
        encoding="utf-8",
    )


def load_macros() -> list[Macro]:
    _ensure_app_dir()
    if not MACROS_FILE.exists():
        return []
    try:
        data = json.loads(MACROS_FILE.read_text(encoding="utf-8"))
        return [Macro(name=item["name"], actions=item["actions"]) for item in data]
    except Exception as exc:
        log.exception("load_macros failed")
        messagebox.showwarning("Macro load failed", f"Macros could not be loaded:\n{exc}")
        return []


def save_macros(macros: list[Macro]) -> None:
    _ensure_app_dir()
    MACROS_FILE.write_text(
        json.dumps([asdict(m) for m in macros], indent=2),
        encoding="utf-8",
    )


def load_settings() -> Settings:
    _ensure_app_dir()
    if not SETTINGS_FILE.exists():
        settings = Settings()
        save_settings(settings)
        return settings
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        valid_names = {f.name for f in fields(Settings)}
        clean = {k: v for k, v in data.items() if k in valid_names}
        return Settings(**{**asdict(Settings()), **clean})
    except Exception:
        log.exception("load_settings failed, using defaults")
        return Settings()


def save_settings(settings: Settings) -> None:
    _ensure_app_dir()
    SETTINGS_FILE.write_text(
        json.dumps(asdict(settings), indent=2),
        encoding="utf-8",
    )

# endregion


# region Session and input helpers ====================================================

def detect_session() -> str:
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def ydotool_available() -> bool:
    return shutil.which("ydotool") is not None


def ydotool_run(args: list[str]) -> bool:
    """Run ydotool with args. Returns True on success, False on failure."""
    try:
        subprocess.run(
            ["ydotool", *args],
            check=True,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("ydotool failed: %s", exc)
        return False


def any_modifier_held() -> bool:
    """Best-effort check: is Ctrl, Alt, or Shift currently held on X11?"""
    try:
        from Xlib import display as xdisplay  # pynput pulls python-xlib as a dep
        d = xdisplay.Display()
        try:
            query = d.screen().root.query_pointer()
            return bool(query.mask & MODIFIER_MASK)
        finally:
            d.close()
    except Exception:
        return False


def format_seconds(total: int) -> str:
    total = max(0, int(total))
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def parse_time_input(value: str, fallback_seconds: int) -> int:
    """Parse '7', '7.5', or '7:30' into total seconds. Returns fallback on failure."""
    value = value.strip()
    if not value:
        return fallback_seconds
    try:
        if ":" in value:
            mins_str, secs_str = value.split(":", 1)
            minutes = int(mins_str) if mins_str else 0
            seconds = int(secs_str) if secs_str else 0
            total = minutes * 60 + seconds
        else:
            total = int(round(float(value) * 60))
        return max(1, total)
    except ValueError:
        return fallback_seconds


def ensure_icon_file() -> Path | None:
    """Generate a fallback PNG icon if one isn't already committed."""
    if ICON_FILE.exists():
        return ICON_FILE
    try:
        ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
        import struct
        import zlib

        size = 64
        bg = (37, 99, 235)
        fg = (255, 255, 255)
        glyph = [
            "11111.",
            "1....1",
            "1....1",
            "1....1",
            "11111.",
            "1.....",
            "1.....",
            "1.....",
            "1.....",
        ]
        gh = len(glyph)
        gw = len(glyph[0])
        scale = 5
        off_x = (size - gw * scale) // 2
        off_y = (size - gh * scale) // 2

        raw = bytearray()
        for y in range(size):
            raw.append(0)  # filter type 0
            for x in range(size):
                gx = (x - off_x) // scale
                gy = (y - off_y) // scale
                if 0 <= gx < gw and 0 <= gy < gh and glyph[gy][gx] == "1":
                    r, g, b = fg
                else:
                    r, g, b = bg
                raw.extend((r, g, b, 255))

        def chunk(tag: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(tag + data)
            return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", crc)

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack("!IIBBBBB", size, size, 8, 6, 0, 0, 0)
        idat = zlib.compress(bytes(raw), 9)
        png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
        ICON_FILE.write_bytes(png)
        return ICON_FILE
    except Exception:
        log.exception("icon generation failed")
        return None

# endregion


# region Tooltip ======================================================================

class Tooltip:
    """Delayed hover tooltip attached to a Tk widget. No extra deps."""

    _active: "list[Tooltip]" = []

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.tipwin: tk.Toplevel | None = None
        self.after_id: str | None = None
        self.bg = "#111827"
        self.fg = "#f9fafb"
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)
        Tooltip._active.append(self)

    def set_palette(self, bg: str, fg: str) -> None:
        self.bg = bg
        self.fg = fg

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self.after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def _show(self) -> None:
        if self.tipwin or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            background=self.bg,
            foreground=self.fg,
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=4,
            font=("TkDefaultFont", 9),
            justify="left",
        )
        label.pack()
        self.tipwin = tw

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self.tipwin:
            try:
                self.tipwin.destroy()
            except tk.TclError:
                pass
            self.tipwin = None

# endregion


# region Countdown display ============================================================

class SegmentClock(tk.Canvas):
    """7-segment-style readout rendered via Canvas primitives.

    Used instead of a font-based Label because some Python distributions ship a
    Tk without Xft support (notably Anaconda), leaving only bitmap fonts that
    look terrible when scaled up for a big countdown. Drawing rectangles on a
    Canvas bypasses the font stack entirely."""

    _SEGMENTS = {
        "0": "abcdef",
        "1": "bc",
        "2": "abdeg",
        "3": "abcdg",
        "4": "bcfg",
        "5": "acdfg",
        "6": "acdefg",
        "7": "abc",
        "8": "abcdefg",
        "9": "abcdfg",
    }

    def __init__(
        self,
        master: tk.Widget,
        *,
        fg: str,
        bg: str,
        digit_w: int = 34,
        digit_h: int = 64,
        thickness: int = 7,
        spacing: int = 5,
    ) -> None:
        super().__init__(master, highlightthickness=0, bd=0, background=bg)
        self._fg = fg
        self._bg = bg
        self._dim = self._blend(fg, bg, 0.82)
        self._dw = digit_w
        self._dh = digit_h
        self._t = thickness
        self._sp = spacing
        self._text = ""

    @staticmethod
    def _blend(fg: str, bg: str, ratio: float) -> str:
        def hx(c: str) -> tuple[int, int, int]:
            return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
        fr, fgc, fb = hx(fg)
        br, bgc, bb = hx(bg)
        r = int(fr + (br - fr) * ratio)
        g = int(fgc + (bgc - fgc) * ratio)
        b = int(fb + (bb - fb) * ratio)
        return f"#{r:02x}{g:02x}{b:02x}"

    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self._redraw()

    def set_palette(self, fg: str, bg: str) -> None:
        self._fg = fg
        self._bg = bg
        self._dim = self._blend(fg, bg, 0.82)
        self.configure(background=bg)
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        dw, dh, t, sp = self._dw, self._dh, self._t, self._sp
        pad = 3
        colon_w = max(3, t)

        total_w = pad * 2
        for i, ch in enumerate(self._text):
            if i > 0:
                total_w += sp
            if ch == ":":
                total_w += colon_w
            elif ch.isdigit():
                total_w += dw
        total_h = dh + pad * 2
        self.configure(width=total_w, height=total_h)

        x = pad
        y = pad
        for i, ch in enumerate(self._text):
            if i > 0:
                x += sp
            if ch == ":":
                r = max(2, t // 2)
                cx = x + colon_w / 2
                self.create_oval(cx - r, y + dh * 0.32 - r, cx + r, y + dh * 0.32 + r,
                                 fill=self._fg, outline=self._fg)
                self.create_oval(cx - r, y + dh * 0.68 - r, cx + r, y + dh * 0.68 + r,
                                 fill=self._fg, outline=self._fg)
                x += colon_w
            elif ch.isdigit():
                self._draw_digit(x, y, ch)
                x += dw

    def _draw_digit(self, x: float, y: float, digit: str) -> None:
        dw, dh, t = self._dw, self._dh, self._t
        mid = y + (dh - t) / 2
        segs = {
            "a": (x + t, y,               x + dw - t, y + t),
            "g": (x + t, mid,             x + dw - t, mid + t),
            "d": (x + t, y + dh - t,      x + dw - t, y + dh),
            "f": (x,     y + t,           x + t,      mid),
            "e": (x,     mid + t,         x + t,      y + dh - t),
            "b": (x + dw - t, y + t,      x + dw,     mid),
            "c": (x + dw - t, mid + t,    x + dw,     y + dh - t),
        }
        active = set(self._SEGMENTS[digit])
        for name, (x0, y0, x1, y1) in segs.items():
            color = self._fg if name in active else self._dim
            self.create_rectangle(x0, y0, x1, y1, fill=color, outline=color)

# endregion


# region Dialogs ======================================================================

_INSERT_OVERRIDE_LABELS = {"": "Use default", "paste": "Paste with Ctrl+V", "type": "Type text"}
_INSERT_OVERRIDE_VALUES = {v: k for k, v in _INSERT_OVERRIDE_LABELS.items()}
_ENTER_OVERRIDE_LABELS = {"": "Use default", "yes": "Yes", "no": "No"}
_ENTER_OVERRIDE_VALUES = {v: k for k, v in _ENTER_OVERRIDE_LABELS.items()}


def _override_label_insert(value: str) -> str:
    return _INSERT_OVERRIDE_LABELS.get(value, "Use default")


def _override_value_insert(label: str) -> str:
    return _INSERT_OVERRIDE_VALUES.get(label, "")


def _override_label_enter(value: str) -> str:
    return _ENTER_OVERRIDE_LABELS.get(value, "Use default")


def _override_value_enter(label: str) -> str:
    return _ENTER_OVERRIDE_VALUES.get(label, "")


class PromptDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Widget, title: str, prompt: Prompt | None = None) -> None:
        self.prompt = prompt
        self.result: Prompt | None = None
        super().__init__(parent, title)

    def body(self, master: tk.Frame) -> tk.Widget:
        ttk.Label(master, text="Name").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.name_var = tk.StringVar(value=self.prompt.name if self.prompt else "")
        name_entry = ttk.Entry(master, textvariable=self.name_var, width=44)
        name_entry.grid(row=1, column=0, sticky="ew", padx=8)
        ttk.Label(master, text="Prompt").grid(row=2, column=0, sticky="w", padx=8, pady=(10, 2))
        self.text = tk.Text(master, width=54, height=12, wrap="word")
        self.text.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        if self.prompt:
            self.text.insert("1.0", self.prompt.body)

        overrides = ttk.LabelFrame(master, text="Overrides (optional)")
        overrides.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))
        overrides.columnconfigure(1, weight=1)

        ttk.Label(overrides, text="Insert method").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.insert_override_var = tk.StringVar(value=self.prompt.insert_override if self.prompt else "")
        insert_combo = ttk.Combobox(
            overrides,
            textvariable=self.insert_override_var,
            values=["Use default", "Paste with Ctrl+V", "Type text"],
            state="readonly",
        )
        insert_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=4)
        insert_combo.set(_override_label_insert(self.insert_override_var.get()))

        ttk.Label(overrides, text="Enter after send").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.enter_override_var = tk.StringVar(value=self.prompt.enter_override if self.prompt else "")
        enter_combo = ttk.Combobox(
            overrides,
            textvariable=self.enter_override_var,
            values=["Use default", "Yes", "No"],
            state="readonly",
        )
        enter_combo.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=4)
        enter_combo.set(_override_label_enter(self.enter_override_var.get()))

        master.columnconfigure(0, weight=1)
        master.rowconfigure(3, weight=1)
        return name_entry

    def validate(self) -> bool:
        name = self.name_var.get().strip()
        body = self.text.get("1.0", "end").strip()
        if not name or not body:
            messagebox.showerror("Missing prompt", "Both name and prompt text are required.", parent=self)
            return False
        self.result = Prompt(
            name=name,
            body=body,
            insert_override=_override_value_insert(self.insert_override_var.get()),
            enter_override=_override_value_enter(self.enter_override_var.get()),
        )
        return True


class RenameDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Widget, title: str, initial: str) -> None:
        self.initial = initial
        self.result: str | None = None
        super().__init__(parent, title)

    def body(self, master: tk.Frame) -> tk.Widget:
        ttk.Label(master, text="New name").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.name_var = tk.StringVar(value=self.initial)
        entry = ttk.Entry(master, textvariable=self.name_var, width=36)
        entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        entry.select_range(0, "end")
        master.columnconfigure(0, weight=1)
        return entry

    def validate(self) -> bool:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror("Missing name", "Name cannot be empty.", parent=self)
            return False
        self.result = name
        return True

# endregion


# region Main widget ==================================================================

class PromptWidget(tk.Tk):
    # region init and lifecycle -------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.palette = DARK_PALETTE if self.settings.theme == "dark" else LIGHT_PALETTE

        self.prompts = load_prompts()
        self.macros = load_macros()

        self.keyboard = KeyboardController() if KeyboardController else None
        self.mouse = MouseController() if MouseController else None

        self.title(APP_NAME)
        self.geometry(f"{self.settings.window_width}x{self.settings.window_height}")
        self.minsize(460, 620)
        self.attributes("-topmost", self.settings.always_on_top)

        # Tk variables bound to settings
        self.theme_var = tk.StringVar(value=self.settings.theme)
        self.always_on_top_var = tk.BooleanVar(value=self.settings.always_on_top)
        self.bell_on_fire_var = tk.BooleanVar(value=self.settings.bell_on_fire)
        self.safe_abort_var = tk.BooleanVar(value=self.settings.safe_abort_modifiers)
        self.restore_clipboard_var = tk.BooleanVar(value=self.settings.restore_clipboard)
        self.hotkey_var = tk.StringVar(value=self.settings.hotkey)
        self.insert_method = tk.StringVar(value=self.settings.insert_method)
        self.press_enter_after_insert = tk.BooleanVar(value=self.settings.enter_after_insert)
        self.use_click_target = tk.BooleanVar(value=self.settings.use_click_target)
        self.click_x = tk.IntVar(value=self.settings.click_x)
        self.click_y = tk.IntVar(value=self.settings.click_y)
        self.loop_timer = tk.BooleanVar(value=self.settings.loop)
        self.shuffle_prompts = tk.BooleanVar(value=self.settings.random)
        self.timer_action = tk.StringVar(value=self.settings.timer_action)
        self.timer_input_var = tk.StringVar(value=format_seconds(self.settings.timer_seconds))

        # Runtime state
        self.selected_macro_name = tk.StringVar(value=self.macros[0].name if self.macros else "")
        self.search_var = tk.StringVar(value="")
        self.mouse_position_var = tk.StringVar(value="Live mouse: unavailable")
        self.countdown_var = tk.StringVar(value=format_seconds(self.settings.timer_seconds))
        self.status_var = tk.StringVar(value="Ready. Select a prompt, then start the timer.")

        self.timer_job: str | None = None
        self.timer_paused = False
        self.remaining_seconds = 0
        self.visible_prompt_indexes: list[int] = list(range(len(self.prompts)))
        self.undo_stack: list[dict[str, object]] = []
        self.bag_order: list[int] = []

        self.capture_listener = None
        self.recording_macro = False
        self.recorded_actions: list[dict[str, object]] = []
        self.last_macro_event_at = 0.0
        self.macro_mouse_listener = None
        self.macro_keyboard_listener = None
        self.hotkey_listener = None
        self.macro_thread: threading.Thread | None = None
        self.saved_clipboard: str | None = None
        self.mouse_poll_id: str | None = None

        # Drag-reorder state
        self._drag_from: int | None = None
        self._drag_current: int | None = None

        self.app_icon: tk.PhotoImage | None = None
        try:
            icon_path = ensure_icon_file()
            if icon_path:
                self.app_icon = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, self.app_icon)
        except Exception:
            log.exception("icon load failed")

        self.tooltips: list[Tooltip] = []

        self.build_ui()
        self.apply_theme()
        self.refresh_list()
        self.refresh_macro_options()
        self.refresh_macro_detail()
        self.start_mouse_poll()
        self.bind_shortcuts()
        self.start_hotkey_listener()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.settings.last_tab and 0 <= self.settings.last_tab < len(self.notebook.tabs()):
            self.notebook.select(self.settings.last_tab)

        self.session = detect_session()
        self.use_ydotool = self.session == "wayland" and ydotool_available()
        log.info(
            "launched session=%s pynput=%s ydotool=%s",
            self.session, PYNPUT_AVAILABLE, self.use_ydotool,
        )
        if self.session == "wayland" and not self.settings.wayland_warned:
            self.after(400, self._show_wayland_advisory)
        elif self.session == "unknown":
            self.set_status("Warning: no X11/Wayland display detected. Paste/mouse features unavailable.", "warn")

    def on_close(self) -> None:
        self.save_runtime_settings()
        self.stop_timer(clear_status=False)
        self.stop_macro_recording(cancel=True)
        self.stop_hotkey_listener()
        if self.capture_listener:
            try:
                self.capture_listener.stop()
            except Exception:
                pass
            self.capture_listener = None
        if self.mouse_poll_id:
            try:
                self.after_cancel(self.mouse_poll_id)
            except tk.TclError:
                pass
        self.destroy()

    def _show_wayland_advisory(self) -> None:
        if self.use_ydotool:
            body = (
                "You're running Wayland.\n\n"
                "ydotool was found on PATH — the widget will route Ctrl+V and Enter "
                "through it for timer paste. Make sure ydotoold is running, e.g.:\n\n"
                "    systemctl --user start ydotoold\n\n"
                "Mouse-position features (click-target, macro playback) still rely "
                "on pynput and may not work on Wayland. Switch to X11 for full "
                "functionality."
            )
        else:
            body = (
                "You appear to be running Wayland.\n\n"
                "This app uses pynput to move the mouse and send Ctrl+V. "
                "On Wayland, these calls often fail silently — timer paste, macro "
                "playback, and click-target may not work.\n\n"
                "Install ydotool and start its daemon (systemctl --user start ydotoold) "
                "to recover paste and Enter, or switch to an X11 session for full "
                "functionality."
            )
        messagebox.showwarning("Wayland session detected", body, parent=self)
        self.settings.wayland_warned = True
        save_settings(self.settings)

    # endregion

    # region Theming ------------------------------------------------------------------

    def apply_theme(self) -> None:
        theme_name = self.theme_var.get()
        self.palette = DARK_PALETTE if theme_name == "dark" else LIGHT_PALETTE
        p = self.palette

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.configure(bg=p["bg"])
        style.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["bg_alt"], bordercolor=p["border"])
        style.configure("TFrame", background=p["bg"])
        style.configure("TLabel", background=p["bg"], foreground=p["fg"])
        style.configure("TLabelframe", background=p["bg"], foreground=p["fg"], bordercolor=p["border"])
        style.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        style.configure("TButton", background=p["bg_alt"], foreground=p["fg"], bordercolor=p["border"], focusthickness=2, focuscolor=p["accent"])
        style.map("TButton",
                  background=[("active", p["accent"])],
                  foreground=[("active", p["accent_fg"])])
        style.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"], bordercolor=p["accent"], padding=(10, 6))
        style.map("Accent.TButton",
                  background=[("active", p["select_bg"])],
                  foreground=[("active", p["select_fg"])])
        style.configure("Preset.TButton", background=p["bg_alt"], foreground=p["fg"], padding=(6, 4))
        style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"], focuscolor=p["accent"])
        style.map("TCheckbutton", background=[("active", p["bg"])])
        style.configure("TRadiobutton", background=p["bg"], foreground=p["fg"])
        style.map("TRadiobutton", background=[("active", p["bg"])])
        style.configure("TEntry", fieldbackground=p["bg_alt"], foreground=p["fg"], insertcolor=p["fg"], bordercolor=p["border"])
        style.configure("TSpinbox", fieldbackground=p["bg_alt"], foreground=p["fg"], insertcolor=p["fg"], arrowcolor=p["fg"])
        style.configure("TCombobox", fieldbackground=p["bg_alt"], foreground=p["fg"], background=p["bg_alt"], arrowcolor=p["fg"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", p["bg_alt"])],
                  foreground=[("readonly", p["fg"])])

        style.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
        style.configure("TNotebook.Tab", background=p["bg_alt"], foreground=p["fg_muted"], padding=(14, 6))
        style.map("TNotebook.Tab",
                  background=[("selected", p["bg"])],
                  foreground=[("selected", p["fg"])])

        style.configure("Status.TLabel", background=p["status_idle"], foreground=p["status_fg"], padding=(8, 4))
        style.configure("StatusOk.TLabel", background=p["status_ok"], foreground=p["status_fg"], padding=(8, 4))
        style.configure("StatusWarn.TLabel", background=p["status_warn"], foreground=p["status_fg"], padding=(8, 4))
        style.configure("StatusErr.TLabel", background=p["status_err"], foreground=p["status_fg"], padding=(8, 4))
        style.configure("Countdown.TLabel", background=p["countdown_bg"], foreground=p["countdown_fg"], padding=(10, 6))
        style.configure("Muted.TLabel", background=p["bg"], foreground=p["fg_muted"])

        clock = getattr(self, "countdown_clock", None)
        if clock is not None:
            clock.set_palette(p["countdown_fg"], p["countdown_bg"])

        # Non-ttk widgets need explicit coloring.
        for widget in (getattr(self, "listbox", None), getattr(self, "search_entry_tk", None)):
            pass  # placeholder, styling applied below per widget

        listbox = getattr(self, "listbox", None)
        if listbox is not None:
            listbox.configure(
                bg=p["bg_alt"],
                fg=p["fg"],
                selectbackground=p["select_bg"],
                selectforeground=p["select_fg"],
                highlightbackground=p["border"],
                highlightcolor=p["accent"],
                borderwidth=1,
                relief="solid",
            )

        preview = getattr(self, "preview", None)
        if preview is not None:
            preview.configure(
                bg=p["bg_alt"],
                fg=p["fg"],
                insertbackground=p["fg"],
                selectbackground=p["select_bg"],
                selectforeground=p["select_fg"],
                borderwidth=1,
                relief="solid",
                highlightbackground=p["border"],
                highlightcolor=p["accent"],
            )

        macro_steps = getattr(self, "macro_steps", None)
        if macro_steps is not None:
            macro_steps.configure(
                bg=p["bg_alt"],
                fg=p["fg"],
                selectbackground=p["select_bg"],
                selectforeground=p["select_fg"],
                highlightbackground=p["border"],
                highlightcolor=p["accent"],
                borderwidth=1,
                relief="solid",
            )

        for tip in Tooltip._active:
            tip.set_palette(p["tooltip_bg"], p["tooltip_fg"])

        self._apply_status_style()

    # endregion

    # region UI construction ----------------------------------------------------------

    def build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # Header: large countdown + Send Now + Pause/Stop
        header = ttk.Frame(self, padding=(12, 10, 12, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        p = self.palette
        self.countdown_clock = SegmentClock(
            header,
            fg=p["countdown_fg"],
            bg=p["countdown_bg"],
        )
        self.countdown_clock.grid(row=0, column=0, sticky="w")
        self.countdown_clock.set_text(self.countdown_var.get())
        self.countdown_var.trace_add(
            "write",
            lambda *_: self.countdown_clock.set_text(self.countdown_var.get()),
        )

        header_actions = ttk.Frame(header)
        header_actions.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.start_button = ttk.Button(header_actions, text="Start Timer", command=self.start_timer, style="Accent.TButton")
        self.start_button.grid(row=0, column=0, padx=(0, 6))
        self.pause_timer_button = ttk.Button(header_actions, text="Pause", command=self.toggle_timer_pause)
        self.pause_timer_button.grid(row=0, column=1, padx=(0, 6))
        self.stop_button = ttk.Button(header_actions, text="Stop", command=self.stop_timer_button)
        self.stop_button.grid(row=0, column=2, padx=(0, 6))
        self.send_now_button = ttk.Button(header_actions, text="Send Now", command=self.send_now, style="Accent.TButton")
        self.send_now_button.grid(row=0, column=3)

        Tooltip(self.start_button, "Start the countdown (Space)")
        Tooltip(self.pause_timer_button, "Pause and resume the countdown (Space)")
        Tooltip(self.stop_button, "Stop the timer (Esc)")
        Tooltip(self.send_now_button, "Fire the configured action immediately (Ctrl+Enter)")

        # Notebook
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 4))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.tab_prompts = ttk.Frame(self.notebook, padding=10)
        self.tab_timer = ttk.Frame(self.notebook, padding=10)
        self.tab_macros = ttk.Frame(self.notebook, padding=10)
        self.tab_settings = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_prompts, text="Prompts")
        self.notebook.add(self.tab_timer, text="Timer")
        self.notebook.add(self.tab_macros, text="Macros")
        self.notebook.add(self.tab_settings, text="Settings")

        self._build_prompts_tab(self.tab_prompts)
        self._build_timer_tab(self.tab_timer)
        self._build_macros_tab(self.tab_macros)
        self._build_settings_tab(self.tab_settings)

        # Status bar
        self.status_label = ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w")
        self.status_label.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 10))

    def _build_prompts_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=2)
        parent.rowconfigure(4, weight=1)

        search_row = ttk.Frame(parent)
        search_row.grid(row=0, column=0, sticky="ew")
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Filter").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.search_var.trace_add("write", lambda *_: self.refresh_list())
        self.search_entry.bind("<Escape>", self._clear_search_focus)
        Tooltip(self.search_entry, "Filter prompts by name (Ctrl+F)")

        ttk.Label(parent, text="Prompts", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 2))

        list_frame = ttk.Frame(parent)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(list_frame, height=10, exportselection=False, activestyle="dotbox")
        self.listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.bind("<<ListboxSelect>>", lambda _event: self.update_preview())
        self.listbox.bind("<Double-Button-1>", lambda _event: self.edit_prompt())
        self.listbox.bind("<Button-1>", self._drag_start, add=True)
        self.listbox.bind("<B1-Motion>", self._drag_motion)
        self.listbox.bind("<ButtonRelease-1>", self._drag_end)

        ttk.Label(parent, text="Preview", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 2))
        preview_frame = ttk.Frame(parent)
        preview_frame.grid(row=4, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview = tk.Text(preview_frame, height=6, wrap="word", state="disabled")
        self.preview.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=preview_scroll.set)

        tokens_hint = ttk.Label(
            parent,
            text="Tokens: {date} {time} {datetime} {weekday} {clipboard} {prompt_name}",
            style="Muted.TLabel",
        )
        tokens_hint.grid(row=5, column=0, sticky="w", pady=(4, 0))
        Tooltip(
            tokens_hint,
            "These placeholders get expanded when the prompt is sent.\n"
            "Edit a prompt and type e.g. 'It is {time} on {weekday}.' to see it fill in.",
        )

        btn_row = ttk.Frame(parent)
        btn_row.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        for col in range(5):
            btn_row.columnconfigure(col, weight=1)
        add_btn = ttk.Button(btn_row, text="Add", command=self.add_prompt)
        edit_btn = ttk.Button(btn_row, text="Edit", command=self.edit_prompt)
        dup_btn = ttk.Button(btn_row, text="Duplicate", command=self.duplicate_prompt)
        rm_btn = ttk.Button(btn_row, text="Remove", command=self.remove_prompt)
        io_btn = ttk.Menubutton(btn_row, text="Import / Export")
        io_menu = tk.Menu(io_btn, tearoff=False)
        io_menu.add_command(label="Import prompts...", command=self.import_prompts)
        io_menu.add_command(label="Export prompts...", command=self.export_prompts)
        io_menu.add_separator()
        io_menu.add_command(label="Import macros...", command=self.import_macros)
        io_menu.add_command(label="Export macros...", command=self.export_macros)
        io_btn.configure(menu=io_menu)
        add_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        edit_btn.grid(row=0, column=1, sticky="ew", padx=4)
        dup_btn.grid(row=0, column=2, sticky="ew", padx=4)
        rm_btn.grid(row=0, column=3, sticky="ew", padx=4)
        io_btn.grid(row=0, column=4, sticky="ew", padx=(4, 0))

        Tooltip(add_btn, "Create a new prompt (Ctrl+N)")
        Tooltip(edit_btn, "Edit selected prompt (Ctrl+E)")
        Tooltip(dup_btn, "Duplicate selected prompt (Ctrl+D)")
        Tooltip(rm_btn, "Delete selected prompt (Del). Ctrl+Z undoes the delete.")
        Tooltip(io_btn, "Import or export prompts and macros as JSON")

    def _build_timer_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        # Interval
        interval_frame = ttk.LabelFrame(parent, text="Interval", padding=10)
        interval_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        interval_frame.columnconfigure(1, weight=1)

        ttk.Label(interval_frame, text="Time (mm:ss)").grid(row=0, column=0, sticky="w")
        self.timer_entry = ttk.Entry(interval_frame, textvariable=self.timer_input_var, width=10)
        self.timer_entry.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.timer_entry.bind("<FocusOut>", lambda _event: self._commit_timer_input())
        self.timer_entry.bind("<Return>", lambda _event: self._commit_timer_input())
        Tooltip(self.timer_entry, "Accepts '7' (minutes), '7.5', or '7:30' (mm:ss)")

        preset_frame = ttk.Frame(interval_frame)
        preset_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for i, total in enumerate(TIMER_PRESETS_SECONDS):
            label = f"{total // 60}m"
            btn = ttk.Button(preset_frame, text=label, style="Preset.TButton", command=lambda s=total: self._set_timer_seconds(s))
            btn.grid(row=0, column=i, padx=(0 if i == 0 else 4, 0))
            Tooltip(btn, f"Set interval to {label}")

        # Delivery
        delivery = ttk.LabelFrame(parent, text="Delivery", padding=10)
        delivery.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Radiobutton(delivery, text="Paste with Ctrl+V", variable=self.insert_method, value="paste").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(delivery, text="Type text", variable=self.insert_method, value="type").grid(row=0, column=1, sticky="w", padx=(12, 0))
        enter_cb = ttk.Checkbutton(delivery, text="Enter after send", variable=self.press_enter_after_insert)
        enter_cb.grid(row=0, column=2, sticky="w", padx=(12, 0))
        Tooltip(enter_cb, "Submit the prompt automatically by pressing Enter after paste/type")

        # Repeat
        repeat = ttk.LabelFrame(parent, text="Repeat", padding=10)
        repeat.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        loop_cb = ttk.Checkbutton(repeat, text="Loop", variable=self.loop_timer)
        loop_cb.grid(row=0, column=0, sticky="w")
        Tooltip(loop_cb, "Keep firing on the interval until Stop")
        rand_cb = ttk.Checkbutton(repeat, text="Random (no repeat within cycle)", variable=self.shuffle_prompts, command=self._reset_bag)
        rand_cb.grid(row=0, column=1, sticky="w", padx=(12, 0))
        Tooltip(rand_cb, "Each cycle shuffles the prompt order and runs through all once")

        # Timer action
        action = ttk.LabelFrame(parent, text="What to send", padding=10)
        action.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        ttk.Radiobutton(action, text="Prompt only", variable=self.timer_action, value="prompt").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(action, text="Macro only", variable=self.timer_action, value="macro").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Radiobutton(action, text="Macro then prompt", variable=self.timer_action, value="macro_prompt").grid(row=0, column=2, sticky="w", padx=(12, 0))

        # Click target
        click = ttk.LabelFrame(parent, text="Cursor position for prompt", padding=10)
        click.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        click.columnconfigure(5, weight=1)
        cb = ttk.Checkbutton(click, text="Click before paste", variable=self.use_click_target)
        cb.grid(row=0, column=0, sticky="w")
        Tooltip(cb, "Click an on-screen coordinate before pasting, in case the target loses focus.")
        ttk.Label(click, text="X").grid(row=0, column=1, sticky="e", padx=(12, 4))
        ttk.Spinbox(click, from_=0, to=20000, textvariable=self.click_x, width=7).grid(row=0, column=2, sticky="w")
        ttk.Label(click, text="Y").grid(row=0, column=3, sticky="e", padx=(12, 4))
        ttk.Spinbox(click, from_=0, to=20000, textvariable=self.click_y, width=7).grid(row=0, column=4, sticky="w")
        set_btn = ttk.Button(click, text="Click to set target", command=self.arm_click_capture)
        set_btn.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        Tooltip(set_btn, "After clicking this, the next click anywhere on screen becomes the paste target")
        self.mouse_live_label = ttk.Label(click, textvariable=self.mouse_position_var, style="Muted.TLabel")
        self.mouse_live_label.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

    def _build_macros_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        pick = ttk.Frame(parent)
        pick.grid(row=0, column=0, sticky="ew")
        pick.columnconfigure(1, weight=1)
        ttk.Label(pick, text="Saved macro").grid(row=0, column=0, sticky="w")
        self.macro_combo = ttk.Combobox(pick, textvariable=self.selected_macro_name, values=self.macro_names(), state="readonly")
        self.macro_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.macro_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_macro_detail())

        btn_row = ttk.Frame(parent)
        btn_row.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        for col in range(4):
            btn_row.columnconfigure(col, weight=1)
        self.add_macro_button = ttk.Button(btn_row, text="Record", command=self.start_macro_recording, style="Accent.TButton")
        self.stop_macro_button = ttk.Button(btn_row, text="Stop Recording", command=self.stop_macro_recording, state="disabled")
        rename_btn = ttk.Button(btn_row, text="Rename", command=self.rename_selected_macro)
        delete_btn = ttk.Button(btn_row, text="Delete", command=self.delete_selected_macro)
        self.add_macro_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_macro_button.grid(row=0, column=1, sticky="ew", padx=4)
        rename_btn.grid(row=0, column=2, sticky="ew", padx=4)
        delete_btn.grid(row=0, column=3, sticky="ew", padx=(4, 0))

        Tooltip(self.add_macro_button, "Start recording mouse clicks and keystrokes")
        Tooltip(self.stop_macro_button, "Finish recording and save to the macro list")
        Tooltip(rename_btn, "Rename the selected macro")
        Tooltip(delete_btn, "Delete the selected macro (Ctrl+Z restores)")

        test_row = ttk.Frame(parent)
        test_row.grid(row=2, column=0, sticky="ew")
        test_btn = ttk.Button(test_row, text="Test Macro (plays once)", command=self.play_selected_macro)
        test_btn.grid(row=0, column=0, sticky="w")
        Tooltip(test_btn, "Play the selected macro once right now, without the timer")

        header_row = ttk.Frame(parent)
        header_row.grid(row=3, column=0, sticky="ew", pady=(10, 2))
        header_row.columnconfigure(0, weight=1)
        ttk.Label(header_row, text="Steps", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.macro_step_summary = ttk.Label(header_row, text="", style="Muted.TLabel")
        self.macro_step_summary.grid(row=0, column=1, sticky="e")

        detail_frame = ttk.Frame(parent)
        detail_frame.grid(row=4, column=0, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self.macro_steps = tk.Listbox(detail_frame, height=10, exportselection=False, activestyle="dotbox")
        self.macro_steps.grid(row=0, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.macro_steps.yview)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        self.macro_steps.configure(yscrollcommand=detail_scroll.set)

        step_btn_row = ttk.Frame(parent)
        step_btn_row.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        for col in range(3):
            step_btn_row.columnconfigure(col, weight=1)
        del_step_btn = ttk.Button(step_btn_row, text="Delete step", command=self.delete_macro_step)
        up_step_btn = ttk.Button(step_btn_row, text="Move up", command=lambda: self.move_macro_step(-1))
        down_step_btn = ttk.Button(step_btn_row, text="Move down", command=lambda: self.move_macro_step(1))
        del_step_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        up_step_btn.grid(row=0, column=1, sticky="ew", padx=4)
        down_step_btn.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        Tooltip(del_step_btn, "Remove the selected step from this macro")
        Tooltip(up_step_btn, "Swap with the step above (its delay moves with it)")
        Tooltip(down_step_btn, "Swap with the step below")

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        appear = ttk.LabelFrame(parent, text="Appearance", padding=10)
        appear.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        appear.columnconfigure(1, weight=1)

        ttk.Label(appear, text="Theme").grid(row=0, column=0, sticky="w")
        theme_row = ttk.Frame(appear)
        theme_row.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Radiobutton(theme_row, text="Light", variable=self.theme_var, value="light", command=self._on_theme_change).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(theme_row, text="Dark", variable=self.theme_var, value="dark", command=self._on_theme_change).grid(row=0, column=1, sticky="w", padx=(10, 0))

        top_cb = ttk.Checkbutton(appear, text="Always on top", variable=self.always_on_top_var, command=self._on_always_on_top_change)
        top_cb.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        feedback = ttk.LabelFrame(parent, text="Feedback", padding=10)
        feedback.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Checkbutton(feedback, text="Bell on timer fire", variable=self.bell_on_fire_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(feedback, text="Safe-abort when a modifier key is held", variable=self.safe_abort_var).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(feedback, text="Restore clipboard after paste", variable=self.restore_clipboard_var).grid(row=2, column=0, sticky="w", pady=(6, 0))

        hotkey_frame = ttk.LabelFrame(parent, text="Global hotkey (pause / resume timer)", padding=10)
        hotkey_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        hotkey_frame.columnconfigure(0, weight=1)
        ttk.Label(hotkey_frame, text="Leave empty to disable. Example: <ctrl>+<shift>+<f12>").grid(row=0, column=0, sticky="w", columnspan=2)
        hotkey_entry = ttk.Entry(hotkey_frame, textvariable=self.hotkey_var)
        hotkey_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        apply_btn = ttk.Button(hotkey_frame, text="Apply", command=self._apply_hotkey)
        apply_btn.grid(row=1, column=1, padx=(8, 0), pady=(6, 0))
        Tooltip(hotkey_entry, "pynput format: <ctrl>, <shift>, <alt>, letters as-is")

        paths = ttk.LabelFrame(parent, text="Data location", padding=10)
        paths.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        paths.columnconfigure(0, weight=1)
        ttk.Label(paths, text=str(APP_DIR), style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(paths, text=f"Log: {LOG_FILE}", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

    # endregion

    # region Shortcuts and focus helpers ---------------------------------------------

    def bind_shortcuts(self) -> None:
        self.bind_all("<Control-n>", self._shortcut(self.add_prompt))
        self.bind_all("<Control-N>", self._shortcut(self.add_prompt))
        self.bind_all("<Control-e>", self._shortcut(self.edit_prompt))
        self.bind_all("<Control-E>", self._shortcut(self.edit_prompt))
        self.bind_all("<Control-d>", self._shortcut(self.duplicate_prompt))
        self.bind_all("<Control-D>", self._shortcut(self.duplicate_prompt))
        self.bind_all("<Control-f>", self._shortcut(self._focus_search))
        self.bind_all("<Control-F>", self._shortcut(self._focus_search))
        self.bind_all("<Control-z>", self._shortcut(self.undo_last_action))
        self.bind_all("<Control-Z>", self._shortcut(self.undo_last_action))
        self.bind_all("<Control-Return>", self._shortcut(self.send_now))
        self.bind_all("<Delete>", self._shortcut_if_listbox(self.remove_prompt))
        self.bind_all("<Alt-Up>", self._shortcut_if_listbox(lambda: self.reorder_selected(-1)))
        self.bind_all("<Alt-Down>", self._shortcut_if_listbox(lambda: self.reorder_selected(1)))
        self.bind_all("<space>", self._space_handler)
        self.bind_all("<Escape>", self._escape_handler)

    def _shortcut(self, callback):
        def handler(event):
            if self._focus_is_text_input(event.widget):
                return
            callback()
            return "break"
        return handler

    def _shortcut_if_listbox(self, callback):
        def handler(event):
            widget = event.widget
            if isinstance(widget, tk.Listbox) or widget is self.listbox:
                callback()
                return "break"
        return handler

    def _focus_is_text_input(self, widget: object) -> bool:
        if not widget:
            return False
        if isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox, ttk.Spinbox, tk.Spinbox)):
            return True
        klass = widget.__class__.__name__
        return klass in {"Entry", "Text", "TEntry", "TCombobox", "TSpinbox", "Spinbox"}

    def _space_handler(self, event):
        if self._focus_is_text_input(event.widget):
            return
        self.toggle_timer_pause()
        return "break"

    def _escape_handler(self, event):
        widget = event.widget
        if widget is self.search_entry:
            self.search_var.set("")
            self.listbox.focus_set()
            return "break"
        if self._focus_is_text_input(widget):
            return
        self.stop_timer_button()
        return "break"

    def _focus_search(self) -> None:
        try:
            self.notebook.select(self.tab_prompts)
        except tk.TclError:
            pass
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def _clear_search_focus(self, _event=None) -> None:
        self.search_var.set("")
        self.listbox.focus_set()

    # endregion

    # region Prompt CRUD --------------------------------------------------------------

    def refresh_list(self) -> None:
        query = self.search_var.get().strip().lower()
        selected_index = self.current_real_index()
        self.listbox.delete(0, "end")
        self.visible_prompt_indexes = []
        for idx, prompt in enumerate(self.prompts):
            if query and query not in prompt.name.lower():
                continue
            self.listbox.insert("end", prompt.name)
            self.visible_prompt_indexes.append(idx)

        if self.visible_prompt_indexes:
            target = selected_index if selected_index in self.visible_prompt_indexes else self.visible_prompt_indexes[0]
            visible_pos = self.visible_prompt_indexes.index(target)
            self.listbox.selection_set(visible_pos)
            self.listbox.activate(visible_pos)
        self.update_preview()

    def current_real_index(self) -> int | None:
        selection = self.listbox.curselection() if hasattr(self, "listbox") else ()
        if not selection:
            return None
        pos = selection[0]
        if 0 <= pos < len(self.visible_prompt_indexes):
            return self.visible_prompt_indexes[pos]
        return None

    def selected_prompt(self) -> Prompt | None:
        idx = self.current_real_index()
        if idx is None:
            return None
        return self.prompts[idx]

    def update_preview(self) -> None:
        prompt = self.selected_prompt()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if prompt:
            self.preview.insert("1.0", prompt.body)
        self.preview.configure(state="disabled")

    def select_prompt_by_real_index(self, real_idx: int) -> None:
        if real_idx not in self.visible_prompt_indexes:
            # Clear search filter so the prompt becomes visible.
            self.search_var.set("")
        if real_idx not in self.visible_prompt_indexes:
            return
        pos = self.visible_prompt_indexes.index(real_idx)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(pos)
        self.listbox.activate(pos)
        self.listbox.see(pos)
        self.update_preview()

    def add_prompt(self) -> None:
        dialog = PromptDialog(self, "Add Prompt")
        if dialog.result:
            self.prompts.append(dialog.result)
            save_prompts(self.prompts)
            self.refresh_list()
            self.select_prompt_by_real_index(len(self.prompts) - 1)
            self._reset_bag()

    def edit_prompt(self) -> None:
        idx = self.current_real_index()
        if idx is None:
            self.set_status("Select a prompt to edit.", "warn")
            return
        dialog = PromptDialog(self, "Edit Prompt", self.prompts[idx])
        if dialog.result:
            self.prompts[idx] = dialog.result
            save_prompts(self.prompts)
            self.refresh_list()
            self.select_prompt_by_real_index(idx)

    def duplicate_prompt(self) -> None:
        idx = self.current_real_index()
        if idx is None:
            self.set_status("Select a prompt to duplicate.", "warn")
            return
        original = self.prompts[idx]
        new_name = self._unique_prompt_name(f"{original.name} (copy)")
        new_prompt = Prompt(
            name=new_name,
            body=original.body,
            insert_override=original.insert_override,
            enter_override=original.enter_override,
        )
        self.prompts.insert(idx + 1, new_prompt)
        save_prompts(self.prompts)
        self.refresh_list()
        self.select_prompt_by_real_index(idx + 1)
        self._reset_bag()

    def remove_prompt(self) -> None:
        idx = self.current_real_index()
        if idx is None:
            self.set_status("Select a prompt to remove.", "warn")
            return
        prompt = self.prompts[idx]
        if not messagebox.askyesno("Remove prompt", f"Remove '{prompt.name}'?", parent=self):
            return
        self._push_undo({"kind": "prompt_delete", "index": idx, "prompt": asdict(prompt)})
        del self.prompts[idx]
        save_prompts(self.prompts)
        self.refresh_list()
        self._reset_bag()
        self.set_status(f"Removed '{prompt.name}'. Ctrl+Z to undo.", "warn")

    def reorder_selected(self, delta: int) -> None:
        if self.search_var.get().strip():
            self.set_status("Clear the filter to reorder.", "warn")
            return
        idx = self.current_real_index()
        if idx is None:
            return
        new_idx = idx + delta
        if not 0 <= new_idx < len(self.prompts):
            return
        self.prompts[idx], self.prompts[new_idx] = self.prompts[new_idx], self.prompts[idx]
        save_prompts(self.prompts)
        self.refresh_list()
        self.select_prompt_by_real_index(new_idx)

    def _unique_prompt_name(self, candidate: str) -> str:
        base = candidate.strip() or "Prompt"
        existing = {p.name for p in self.prompts}
        if base not in existing:
            return base
        suffix = 2
        while f"{base} {suffix}" in existing:
            suffix += 1
        return f"{base} {suffix}"

    # endregion

    # region Drag-to-reorder ---------------------------------------------------------

    def _drag_start(self, event):
        if self.search_var.get().strip():
            self._drag_from = None
            return
        pos = self.listbox.nearest(event.y)
        if 0 <= pos < len(self.visible_prompt_indexes):
            self._drag_from = pos
            self._drag_current = pos
        else:
            self._drag_from = None

    def _drag_motion(self, event):
        if self._drag_from is None or self.search_var.get().strip():
            return
        pos = self.listbox.nearest(event.y)
        if pos == self._drag_current or pos < 0 or pos >= len(self.visible_prompt_indexes):
            return
        real_from = self.visible_prompt_indexes[self._drag_current]
        real_to = self.visible_prompt_indexes[pos]
        self.prompts[real_from], self.prompts[real_to] = self.prompts[real_to], self.prompts[real_from]
        # Swap in visible index list mirror
        names = [self.listbox.get(i) for i in range(self.listbox.size())]
        names[self._drag_current], names[pos] = names[pos], names[self._drag_current]
        self.listbox.delete(0, "end")
        for name in names:
            self.listbox.insert("end", name)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(pos)
        self.listbox.activate(pos)
        self._drag_current = pos

    def _drag_end(self, _event):
        if self._drag_from is not None and self._drag_current is not None and self._drag_from != self._drag_current:
            save_prompts(self.prompts)
            self.refresh_list()
            # Restore selection
            if 0 <= self._drag_current < len(self.visible_prompt_indexes):
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(self._drag_current)
                self.listbox.activate(self._drag_current)
            self.update_preview()
            self._reset_bag()
        self._drag_from = None
        self._drag_current = None

    # endregion

    # region Undo ---------------------------------------------------------------------

    def _push_undo(self, entry: dict[str, object]) -> None:
        self.undo_stack.append(entry)
        if len(self.undo_stack) > 20:
            self.undo_stack.pop(0)

    def undo_last_action(self) -> None:
        if not self.undo_stack:
            self.set_status("Nothing to undo.", "warn")
            return
        entry = self.undo_stack.pop()
        kind = entry.get("kind")
        if kind == "prompt_delete":
            prompt_data = entry["prompt"]
            prompt = _prompt_from_dict(prompt_data)
            insert_at = min(int(entry["index"]), len(self.prompts))
            self.prompts.insert(insert_at, prompt)
            save_prompts(self.prompts)
            self.refresh_list()
            self.select_prompt_by_real_index(insert_at)
            self.set_status(f"Restored prompt '{prompt.name}'.", "ok")
            self._reset_bag()
        elif kind == "macro_delete":
            macro_data = entry["macro"]
            macro = Macro(name=macro_data["name"], actions=macro_data["actions"])
            insert_at = min(int(entry["index"]), len(self.macros))
            self.macros.insert(insert_at, macro)
            save_macros(self.macros)
            self.selected_macro_name.set(macro.name)
            self.refresh_macro_options()
            self.refresh_macro_detail()
            self.set_status(f"Restored macro '{macro.name}'.", "ok")

    # endregion

    # region Import / Export ---------------------------------------------------------

    def export_prompts(self) -> None:
        self._export_json("prompts", [asdict(p) for p in self.prompts])

    def export_macros(self) -> None:
        self._export_json("macros", [asdict(m) for m in self.macros])

    def _export_json(self, kind: str, payload: list) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title=f"Export {kind}",
            defaultextension=".json",
            initialfile=f"{kind}.json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.set_status(f"Exported {len(payload)} {kind} to {path}.", "ok")
        except Exception as exc:
            log.exception("export failed")
            messagebox.showerror("Export failed", str(exc), parent=self)

    def import_prompts(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Import prompts",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("File must contain a JSON array of {name, body} objects.")
            added = 0
            for item in data:
                name = str(item.get("name", "")).strip()
                body = str(item.get("body", "")).strip()
                if not name or not body:
                    continue
                self.prompts.append(
                    Prompt(
                        name=self._unique_prompt_name(name),
                        body=body,
                        insert_override=str(item.get("insert_override", "") or ""),
                        enter_override=str(item.get("enter_override", "") or ""),
                    )
                )
                added += 1
            save_prompts(self.prompts)
            self.refresh_list()
            self._reset_bag()
            self.set_status(f"Imported {added} prompt(s) from {path}.", "ok")
        except Exception as exc:
            log.exception("import prompts failed")
            messagebox.showerror("Import failed", str(exc), parent=self)

    def import_macros(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Import macros",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("File must contain a JSON array of {name, actions} objects.")
            added = 0
            for item in data:
                name = str(item.get("name", "")).strip()
                actions = item.get("actions") or []
                if not name or not isinstance(actions, list):
                    continue
                self.macros.append(Macro(name=self.unique_macro_name(name), actions=actions))
                added += 1
            save_macros(self.macros)
            self.refresh_macro_options()
            self.refresh_macro_detail()
            self.set_status(f"Imported {added} macro(s) from {path}.", "ok")
        except Exception as exc:
            log.exception("import macros failed")
            messagebox.showerror("Import failed", str(exc), parent=self)

    # endregion

    # region Macros -------------------------------------------------------------------

    def macro_names(self) -> list[str]:
        return [macro.name for macro in self.macros]

    def refresh_macro_options(self) -> None:
        names = self.macro_names()
        if hasattr(self, "macro_combo"):
            self.macro_combo.configure(values=names)
        if not names:
            self.selected_macro_name.set("")
            return
        if self.selected_macro_name.get() not in names:
            self.selected_macro_name.set(names[0])
        self.refresh_macro_detail()

    def refresh_macro_detail(self) -> None:
        macro = self.selected_macro()
        self.macro_steps.delete(0, "end")
        if not macro:
            self.macro_steps.insert("end", "(no macro selected)")
            self.macro_step_summary.configure(text="")
            return
        for i, action in enumerate(macro.actions):
            self.macro_steps.insert("end", self._format_macro_step(i, action))
        self.macro_step_summary.configure(text=f"{macro.name} — {len(macro.actions)} step(s)")

    @staticmethod
    def _format_macro_step(index: int, action: dict) -> str:
        kind = action.get("kind", "?")
        delay = float(action.get("delay", 0.0))
        if kind == "mouse_click":
            btn = action.get("button", "?")
            x = action.get("x", "?")
            y = action.get("y", "?")
            pressed = "press" if action.get("pressed") else "release"
            return f"{index+1:>3}. +{delay:5.2f}s  mouse {btn} {pressed} at ({x},{y})"
        if kind in ("key_press", "key_release"):
            key = action.get("key", "?")
            return f"{index+1:>3}. +{delay:5.2f}s  {kind} {key}"
        return f"{index+1:>3}. +{delay:5.2f}s  {kind}"

    def _selected_macro_step(self) -> int | None:
        sel = self.macro_steps.curselection()
        if not sel:
            return None
        macro = self.selected_macro()
        if not macro:
            return None
        idx = int(sel[0])
        if idx < 0 or idx >= len(macro.actions):
            return None
        return idx

    def delete_macro_step(self) -> None:
        macro = self.selected_macro()
        idx = self._selected_macro_step()
        if not macro or idx is None:
            self.set_status("Select a step to delete.", "warn")
            return
        del macro.actions[idx]
        save_macros(self.macros)
        self.refresh_macro_detail()
        if macro.actions:
            new_pos = min(idx, len(macro.actions) - 1)
            self.macro_steps.selection_set(new_pos)
            self.macro_steps.activate(new_pos)
            self.macro_steps.see(new_pos)
        self.set_status("Step removed.", "ok")

    def move_macro_step(self, direction: int) -> None:
        macro = self.selected_macro()
        idx = self._selected_macro_step()
        if not macro or idx is None:
            self.set_status("Select a step to move.", "warn")
            return
        target = idx + direction
        if target < 0 or target >= len(macro.actions):
            return
        macro.actions[idx], macro.actions[target] = macro.actions[target], macro.actions[idx]
        save_macros(self.macros)
        self.refresh_macro_detail()
        self.macro_steps.selection_set(target)
        self.macro_steps.activate(target)
        self.macro_steps.see(target)

    def selected_macro(self) -> Macro | None:
        selected_name = self.selected_macro_name.get()
        for macro in self.macros:
            if macro.name == selected_name:
                return macro
        return None

    def start_macro_recording(self) -> None:
        if not PYNPUT_AVAILABLE or not MouseListener or not KeyboardListener:
            messagebox.showerror("Macro recording unavailable",
                                 "Macro recording requires pynput. Run ./run.sh to install dependencies.",
                                 parent=self)
            return
        if self.recording_macro:
            return
        if self.capture_listener:
            self.capture_listener.stop()
            self.capture_listener = None

        self.recording_macro = True
        self._recording_tab = self.notebook.index("current")
        self.recorded_actions = []
        self.last_macro_event_at = time.monotonic()
        self.add_macro_button.configure(state="disabled")
        self.stop_macro_button.configure(state="normal")
        self.set_status("Recording macro. Perform clicks and keystrokes, then click Stop Recording.", "warn")
        self.title(f"{APP_NAME} — recording")
        self.macro_mouse_listener = MouseListener(on_click=self.handle_macro_click)
        self.macro_keyboard_listener = KeyboardListener(on_press=self.handle_macro_key_press, on_release=self.handle_macro_key_release)
        self.macro_mouse_listener.start()
        self.macro_keyboard_listener.start()
        log.info("macro recording started")

    def stop_macro_recording(self, cancel: bool = False) -> None:
        if not self.recording_macro:
            return
        self.recording_macro = False
        if self.macro_mouse_listener:
            try:
                self.macro_mouse_listener.stop()
            except Exception:
                pass
            self.macro_mouse_listener = None
        if self.macro_keyboard_listener:
            try:
                self.macro_keyboard_listener.stop()
            except Exception:
                pass
            self.macro_keyboard_listener = None
        self.add_macro_button.configure(state="normal")
        self.stop_macro_button.configure(state="disabled")
        self._update_title_from_timer()

        if cancel or not self.recorded_actions:
            self.set_status("Macro recording discarded.", "warn" if self.recorded_actions else "idle")
            return

        name = simpledialog.askstring("Save Macro", "Macro name:", parent=self)
        if not name:
            self.set_status("Macro recording discarded.", "warn")
            return

        macro = Macro(name=self.unique_macro_name(name.strip()), actions=self.recorded_actions)
        self.macros.append(macro)
        self.selected_macro_name.set(macro.name)
        save_macros(self.macros)
        self.refresh_macro_options()
        self.set_status(f"Saved macro '{macro.name}' ({len(macro.actions)} actions).", "ok")
        log.info("macro saved name=%s actions=%d", macro.name, len(macro.actions))

    def unique_macro_name(self, name: str) -> str:
        base_name = name.strip() or "Macro"
        existing_names = set(self.macro_names())
        if base_name not in existing_names:
            return base_name
        suffix = 2
        while f"{base_name} {suffix}" in existing_names:
            suffix += 1
        return f"{base_name} {suffix}"

    def rename_selected_macro(self) -> None:
        macro = self.selected_macro()
        if not macro:
            self.set_status("Select a macro to rename.", "warn")
            return
        dialog = RenameDialog(self, "Rename macro", macro.name)
        if not dialog.result or dialog.result == macro.name:
            return
        new_name = self.unique_macro_name(dialog.result)
        old_name = macro.name
        macro.name = new_name
        save_macros(self.macros)
        self.selected_macro_name.set(new_name)
        self.refresh_macro_options()
        self.set_status(f"Renamed '{old_name}' to '{new_name}'.", "ok")

    def delete_selected_macro(self) -> None:
        macro = self.selected_macro()
        if not macro:
            self.set_status("Select a macro to delete.", "warn")
            return
        if not messagebox.askyesno("Delete macro", f"Delete '{macro.name}'?", parent=self):
            return
        idx = self.macros.index(macro)
        self._push_undo({"kind": "macro_delete", "index": idx, "macro": asdict(macro)})
        del self.macros[idx]
        save_macros(self.macros)
        self.refresh_macro_options()
        self.set_status(f"Deleted macro '{macro.name}'. Ctrl+Z to undo.", "warn")

    def handle_macro_click(self, x: int, y: int, button, pressed: bool) -> None:
        if not self.recording_macro or self.point_inside_widget(int(x), int(y)):
            return
        self.record_macro_action({
            "kind": "mouse_click",
            "x": int(x),
            "y": int(y),
            "button": self.button_to_name(button),
            "pressed": bool(pressed),
        })

    def handle_macro_key_press(self, key) -> None:
        if not self.recording_macro:
            return
        self.record_macro_action({"kind": "key_press", "key": self.key_to_spec(key)})

    def handle_macro_key_release(self, key) -> None:
        if not self.recording_macro:
            return
        self.record_macro_action({"kind": "key_release", "key": self.key_to_spec(key)})

    def record_macro_action(self, action: dict[str, object]) -> None:
        now = time.monotonic()
        action["delay"] = min(30.0, max(0.0, now - self.last_macro_event_at))
        self.last_macro_event_at = now
        self.recorded_actions.append(action)

    def point_inside_widget(self, x: int, y: int) -> bool:
        left = self.winfo_rootx()
        top = self.winfo_rooty()
        return left <= x <= left + self.winfo_width() and top <= y <= top + self.winfo_height()

    def key_to_spec(self, key) -> str:
        if hasattr(key, "char") and key.char is not None:
            return f"char:{key.char}"
        return f"key:{getattr(key, 'name', str(key).replace('Key.', ''))}"

    def spec_to_key(self, spec: str):
        if spec.startswith("char:"):
            return spec.removeprefix("char:")
        key_name = spec.removeprefix("key:")
        return getattr(Key, key_name)

    def button_to_name(self, button) -> str:
        return getattr(button, "name", str(button).replace("Button.", ""))

    def name_to_button(self, name: str):
        return getattr(Button, name)

    def play_selected_macro(self) -> None:
        macro = self.selected_macro()
        if not macro:
            self.set_status("Select a macro first.", "warn")
            return
        if not self.can_play_macro(macro):
            return
        self._run_in_thread(lambda: self._play_macro_worker(macro, label="Test macro"))

    # endregion

    # region Timer core ---------------------------------------------------------------

    def _commit_timer_input(self) -> None:
        seconds = parse_time_input(self.timer_input_var.get(), self.settings.timer_seconds)
        self.settings.timer_seconds = seconds
        self.timer_input_var.set(format_seconds(seconds))
        if not self.timer_job and not self.timer_paused:
            self.countdown_var.set(format_seconds(seconds))
        save_settings(self.settings)

    def _set_timer_seconds(self, seconds: int) -> None:
        self.settings.timer_seconds = seconds
        self.timer_input_var.set(format_seconds(seconds))
        if not self.timer_job and not self.timer_paused:
            self.countdown_var.set(format_seconds(seconds))
        save_settings(self.settings)

    def timer_interval_seconds(self) -> int:
        return max(1, int(self.settings.timer_seconds))

    def start_timer(self) -> None:
        self._commit_timer_input()
        if self.timer_action_uses_prompt() and not self.prompts:
            self.set_status("Add at least one prompt before starting the timer.", "warn")
            return
        if self.timer_action_uses_prompt() and not self.shuffle_prompts.get() and not self.selected_prompt():
            self.set_status("Select a prompt before starting the timer, or enable Random.", "warn")
            return
        if self.timer_action_uses_macro() and not self.selected_macro():
            self.set_status("Record or select a macro before starting the timer.", "warn")
            return
        self.stop_timer(clear_status=False)
        self.timer_paused = False
        self.pause_timer_button.configure(text="Pause")
        self.remaining_seconds = self.timer_interval_seconds()
        self._reset_bag()
        self.set_status("Timer running. Place the cursor in the target app before zero.", "ok")
        self.tick_timer()

    def tick_timer(self) -> None:
        if self.timer_paused:
            self.timer_job = None
            self.countdown_var.set(format_seconds(self.remaining_seconds))
            self._update_title_from_timer()
            return
        if self.remaining_seconds <= 0:
            self.schedule_timer_action()
            if self.loop_timer.get():
                self.remaining_seconds = max(0, self.timer_interval_seconds() - 1)
                self.timer_job = self.after(1000, self.tick_timer)
            else:
                self.timer_job = None
                self.countdown_var.set(format_seconds(0))
            self._update_title_from_timer()
            return
        self.countdown_var.set(format_seconds(self.remaining_seconds))
        self._update_title_from_timer()
        self.remaining_seconds -= 1
        self.timer_job = self.after(1000, self.tick_timer)

    def toggle_timer_pause(self) -> None:
        if self.timer_paused:
            self.timer_paused = False
            self.pause_timer_button.configure(text="Pause")
            self.set_status("Timer resumed.", "ok")
            self.tick_timer()
            return
        if not self.timer_job or self.remaining_seconds <= 0:
            self.set_status("No timer is running.", "warn")
            return
        try:
            self.after_cancel(self.timer_job)
        except tk.TclError:
            pass
        self.timer_job = None
        self.timer_paused = True
        self.pause_timer_button.configure(text="Resume")
        self.set_status("Timer paused.", "warn")
        self._update_title_from_timer()

    def stop_timer(self, clear_status: bool = True) -> None:
        if self.timer_job:
            try:
                self.after_cancel(self.timer_job)
            except tk.TclError:
                pass
            self.timer_job = None
        self.timer_paused = False
        self.pause_timer_button.configure(text="Pause")
        self.countdown_var.set(format_seconds(self.settings.timer_seconds))
        self._update_title_from_timer()
        if clear_status:
            self.set_status("Timer stopped.", "idle")

    def stop_timer_button(self) -> None:
        self.stop_timer(clear_status=True)

    def _update_title_from_timer(self) -> None:
        if self.recording_macro:
            self.title(f"{APP_NAME} — recording")
            return
        if self.timer_paused and self.remaining_seconds > 0:
            self.title(f"{APP_NAME} — paused {format_seconds(self.remaining_seconds)}")
        elif self.timer_job and self.remaining_seconds > 0:
            self.title(f"{APP_NAME} — {format_seconds(self.remaining_seconds)}")
        else:
            self.title(APP_NAME)

    def timer_action_uses_prompt(self) -> bool:
        return self.timer_action.get() in {"prompt", "macro_prompt"}

    def timer_action_uses_macro(self) -> bool:
        return self.timer_action.get() in {"macro", "macro_prompt"}

    # endregion

    # region Fire action --------------------------------------------------------------

    def send_now(self) -> None:
        if self.timer_action_uses_prompt() and not self.prompts:
            self.set_status("Add at least one prompt first.", "warn")
            return
        if self.timer_action_uses_prompt() and not self.shuffle_prompts.get() and not self.selected_prompt():
            self.set_status("Select a prompt or enable Random.", "warn")
            return
        if self.timer_action_uses_macro() and not self.selected_macro():
            self.set_status("Record or select a macro first.", "warn")
            return
        self.schedule_timer_action(manual=True)

    def schedule_timer_action(self, manual: bool = False) -> None:
        if self.safe_abort_var.get() and any_modifier_held():
            self.set_status("Skipped tick: a modifier key is held.", "warn")
            log.info("skipped tick due to held modifier")
            return

        prompt = self._pick_prompt_for_fire() if self.timer_action_uses_prompt() else None
        macro = self.selected_macro() if self.timer_action_uses_macro() else None

        if self.timer_action_uses_prompt() and not prompt:
            self.set_status("No prompt available to send.", "warn")
            return
        if self.timer_action_uses_macro() and not macro:
            self.set_status("No macro selected.", "warn")
            return
        if self.timer_action_uses_prompt() and (not self.keyboard or not Key):
            if prompt:
                self.copy_to_clipboard(self._expand_template(prompt.body, prompt))
            self.set_status("pynput unavailable: prompt copied to clipboard, but paste not sent.", "err")
            return
        if self.timer_action_uses_macro() and not self.can_play_macro(macro):
            return

        self.set_status(f"{'Sending' if manual else 'Timer fired:'} {self._action_summary()}.", "ok")
        if self.bell_on_fire_var.get():
            try:
                self.bell()
            except tk.TclError:
                pass

        def worker():
            try:
                if macro:
                    self._play_macro_worker(macro, label="Playing macro")
                if prompt is not None:
                    self._send_prompt_worker(prompt)
                self.after(0, lambda: self.set_status("Done.", "ok"))
                log.info("fire succeeded action=%s manual=%s", self.timer_action.get(), manual)
            except Exception as exc:
                log.exception("fire failed")
                self.after(0, lambda: self.set_status(f"Action failed: {exc}", "err"))

        self._run_in_thread(worker)

    def _pick_prompt_for_fire(self) -> Prompt | None:
        if self.shuffle_prompts.get() and self.prompts:
            if not self.bag_order:
                self._reset_bag()
            idx = self.bag_order.pop()
            if idx >= len(self.prompts):
                idx = 0
            prompt = self.prompts[idx]
            self.after(0, lambda i=idx: self.select_prompt_by_real_index(i))
            return prompt
        return self.selected_prompt()

    def _reset_bag(self) -> None:
        order = list(range(len(self.prompts)))
        random.shuffle(order)
        self.bag_order = order

    def _action_summary(self) -> str:
        action = self.timer_action.get()
        if action == "macro":
            return "macro"
        if action == "macro_prompt":
            verb = "typing" if self.insert_method.get() == "type" else "pasting"
            return f"macro + {verb} prompt"
        verb = "typing" if self.insert_method.get() == "type" else "pasting"
        return f"{verb} prompt"

    def can_play_macro(self, macro: Macro | None) -> bool:
        if not macro:
            return False
        needs_mouse = any(action.get("kind") == "mouse_click" for action in macro.actions)
        needs_keyboard = any(str(action.get("kind", "")).startswith("key_") for action in macro.actions)
        if needs_mouse and (not self.mouse or not Button):
            self.set_status("Macro needs mouse control but pynput is unavailable.", "err")
            return False
        if needs_keyboard and (not self.keyboard or not Key):
            self.set_status("Macro needs keyboard control but pynput is unavailable.", "err")
            return False
        return True

    def _run_in_thread(self, worker) -> None:
        thread = threading.Thread(target=worker, daemon=True)
        self.macro_thread = thread
        thread.start()

    def _play_macro_worker(self, macro: Macro, label: str) -> None:
        self.after(0, lambda: self.set_status(f"{label}: {macro.name}", "ok"))
        for action in macro.actions:
            time.sleep(float(action.get("delay", 0.0)))
            kind = action.get("kind")
            if kind == "mouse_click":
                self.mouse.position = (int(action["x"]), int(action["y"]))
                button = self.name_to_button(str(action["button"]))
                if action.get("pressed"):
                    self.mouse.press(button)
                else:
                    self.mouse.release(button)
            elif kind == "key_press":
                self.keyboard.press(self.spec_to_key(str(action["key"])))
            elif kind == "key_release":
                self.keyboard.release(self.spec_to_key(str(action["key"])))

    def _send_prompt_worker(self, prompt: Prompt) -> None:
        saved_clipboard: str | None = None
        if self.restore_clipboard_var.get():
            saved_clipboard = self._read_clipboard()
        body = self._expand_template(prompt.body, prompt, clipboard_snapshot=saved_clipboard)
        method = prompt.insert_override or self.insert_method.get()
        press_enter = (
            prompt.enter_override == "yes"
            if prompt.enter_override
            else self.press_enter_after_insert.get()
        )
        self._write_clipboard(body)
        time.sleep(0.05)
        if self.use_click_target.get() and self.mouse and Button:
            self.mouse.position = (int(self.click_x.get()), int(self.click_y.get()))
            self.mouse.click(Button.left, 1)
            time.sleep(0.08)
        if method == "type":
            if self.use_ydotool and ydotool_run(["type", body]):
                pass
            else:
                self.keyboard.type(body)
        else:
            if self.use_ydotool and ydotool_run(["key", "29:1", "47:1", "47:0", "29:0"]):
                pass
            else:
                self.keyboard.press(Key.ctrl)
                self.keyboard.press("v")
                self.keyboard.release("v")
                self.keyboard.release(Key.ctrl)
        if press_enter:
            time.sleep(0.05)
            if self.use_ydotool and ydotool_run(["key", "28:1", "28:0"]):
                pass
            else:
                self.keyboard.press(Key.enter)
                self.keyboard.release(Key.enter)
        if saved_clipboard is not None:
            time.sleep(1.0)
            self.after(0, lambda c=saved_clipboard: self._write_clipboard(c))

    def _expand_template(
        self,
        text: str,
        prompt: Prompt | None = None,
        clipboard_snapshot: str | None = None,
    ) -> str:
        if "{" not in text:
            return text
        now = datetime.now()
        if clipboard_snapshot is None:
            clipboard_snapshot = self._read_clipboard() or ""
        replacements = {
            "{date}": now.strftime("%Y-%m-%d"),
            "{time}": now.strftime("%H:%M"),
            "{datetime}": now.strftime("%Y-%m-%d %H:%M"),
            "{weekday}": now.strftime("%A"),
            "{clipboard}": clipboard_snapshot,
            "{prompt_name}": prompt.name if prompt else "",
        }
        for token, value in replacements.items():
            text = text.replace(token, value)
        return text

    # endregion

    # region Clipboard ---------------------------------------------------------------

    def copy_to_clipboard(self, text: str) -> None:
        self._write_clipboard(text)

    def _write_clipboard(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError:
            log.exception("clipboard write failed")

    def _read_clipboard(self) -> str | None:
        try:
            return self.clipboard_get()
        except tk.TclError:
            return None

    # endregion

    # region Click capture and mouse poll --------------------------------------------

    def arm_click_capture(self) -> None:
        if not MouseListener:
            self.set_status("Click capture requires pynput.", "err")
            return
        if self.capture_listener:
            try:
                self.capture_listener.stop()
            except Exception:
                pass
            self.capture_listener = None
        self.set_status("Click anywhere on the screen to set the paste target.", "warn")
        self.capture_listener = MouseListener(on_click=self.handle_target_click)
        self.capture_listener.start()

    def handle_target_click(self, x: int, y: int, _button, pressed: bool) -> bool:
        if not pressed:
            return True
        self.after(0, lambda: self.set_click_target(int(x), int(y)))
        return False

    def set_click_target(self, x: int, y: int) -> None:
        self.click_x.set(x)
        self.click_y.set(y)
        self.use_click_target.set(True)
        self.capture_listener = None
        self.set_status(f"Click target set to ({x}, {y}).", "ok")

    def start_mouse_poll(self) -> None:
        self._poll_mouse_position()

    def _poll_mouse_position(self) -> None:
        try:
            if self.mouse and self.winfo_viewable():
                current_tab = None
                try:
                    current_tab = self.notebook.index("current")
                except tk.TclError:
                    current_tab = None
                # Only poll when the Timer tab (which shows the live label) is active.
                if current_tab is not None and self.notebook.tabs()[current_tab] == str(self.tab_timer):
                    x, y = self.mouse.position
                    self.mouse_position_var.set(f"Live mouse: ({int(x)}, {int(y)})")
        except Exception:
            pass
        self.mouse_poll_id = self.after(400, self._poll_mouse_position)

    # endregion

    # region Status bar ---------------------------------------------------------------

    def set_status(self, text: str, tone: str = "idle") -> None:
        self.status_var.set(text)
        self._status_tone = tone
        self._apply_status_style()

    def _apply_status_style(self) -> None:
        tone = getattr(self, "_status_tone", "idle")
        style_name = {
            "idle": "Status.TLabel",
            "ok": "StatusOk.TLabel",
            "warn": "StatusWarn.TLabel",
            "err": "StatusErr.TLabel",
        }.get(tone, "Status.TLabel")
        if hasattr(self, "status_label"):
            self.status_label.configure(style=style_name)

    # endregion

    # region Notebook + settings -----------------------------------------------------

    def _on_tab_changed(self, _event) -> None:
        # Prevent tab switching while recording a macro: keep the Macros tab.
        if self.recording_macro:
            try:
                current = self.notebook.index("current")
                if current != self._recording_tab:
                    self.notebook.select(self._recording_tab)
                    return
            except tk.TclError:
                pass
        try:
            self.settings.last_tab = self.notebook.index("current")
            save_settings(self.settings)
        except tk.TclError:
            pass

    def _on_theme_change(self) -> None:
        self.settings.theme = self.theme_var.get()
        save_settings(self.settings)
        self.apply_theme()

    def _on_always_on_top_change(self) -> None:
        value = bool(self.always_on_top_var.get())
        self.settings.always_on_top = value
        save_settings(self.settings)
        self.attributes("-topmost", value)

    def _apply_hotkey(self) -> None:
        combo = self.hotkey_var.get().strip()
        self.settings.hotkey = combo
        save_settings(self.settings)
        self.stop_hotkey_listener()
        self.start_hotkey_listener()
        if combo:
            self.set_status(f"Global hotkey set to {combo}.", "ok")
        else:
            self.set_status("Global hotkey disabled.", "idle")

    def save_runtime_settings(self) -> None:
        try:
            self.settings.insert_method = self.insert_method.get()
            self.settings.enter_after_insert = bool(self.press_enter_after_insert.get())
            self.settings.use_click_target = bool(self.use_click_target.get())
            self.settings.click_x = int(self.click_x.get())
            self.settings.click_y = int(self.click_y.get())
            self.settings.loop = bool(self.loop_timer.get())
            self.settings.random = bool(self.shuffle_prompts.get())
            self.settings.timer_action = self.timer_action.get()
            self.settings.bell_on_fire = bool(self.bell_on_fire_var.get())
            self.settings.safe_abort_modifiers = bool(self.safe_abort_var.get())
            self.settings.restore_clipboard = bool(self.restore_clipboard_var.get())
            self.settings.theme = self.theme_var.get()
            self.settings.always_on_top = bool(self.always_on_top_var.get())
            self.settings.hotkey = self.hotkey_var.get().strip()
            self.settings.window_width = max(460, self.winfo_width())
            self.settings.window_height = max(620, self.winfo_height())
            try:
                self.settings.last_tab = self.notebook.index("current")
            except tk.TclError:
                pass
            save_settings(self.settings)
        except Exception:
            log.exception("save_runtime_settings failed")

    # endregion

    # region Global hotkey -----------------------------------------------------------

    def start_hotkey_listener(self) -> None:
        if not PYNPUT_AVAILABLE or not GlobalHotKeys:
            return
        combo = (self.hotkey_var.get() or "").strip()
        if not combo:
            return
        try:
            self.hotkey_listener = GlobalHotKeys({combo: self._on_hotkey_triggered})
            self.hotkey_listener.start()
            log.info("global hotkey active: %s", combo)
        except Exception:
            log.exception("global hotkey failed to start")
            self.hotkey_listener = None

    def stop_hotkey_listener(self) -> None:
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except Exception:
                log.exception("global hotkey stop failed")
            self.hotkey_listener = None

    def _on_hotkey_triggered(self) -> None:
        self.after(0, self.toggle_timer_pause)

    # endregion

# endregion


# region Entrypoint ===================================================================

def main() -> int:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        sys.stderr.write(
            "Prompt Widget needs a graphical session ($DISPLAY or $WAYLAND_DISPLAY).\n"
        )
        return 1
    try:
        app = PromptWidget()
        app.mainloop()
    except Exception:
        log.exception("fatal error")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

# endregion
