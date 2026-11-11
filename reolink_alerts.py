"""
Reolink Alerts  -  sound + pop-up alerts from the Reolink NVR
=============================================================

What it does
------------
* Listens to your Reolink NVR's instant event push over your home network (the
  same "TCP push" Home Assistant uses). No Reolink app, no cloud.
* When a camera listed in settings.toml sees one of its alert types (day list
  or night list), it plays that camera's sound for that type and pops up a
  borderless, always-on-top, muted live view of that camera. The pop-up is
  shown without taking focus, so a game or full-screen video keeps running.
* The pop-up closes LINGER seconds after the camera says the event is over
  (never sooner than MIN, never later than MAX). One pop-up at a time.
  Left- or right-click a pop-up to close it early.
* Sounds play once per visit: the same camera + type sounds again only after
  that camera has seen none of that type for CLEAR seconds (2 minutes), so a
  dog wandering in and out of view doesn't repeat it.
* Tray icon: snooze 15/30/60 minutes, resume, pop-up size (Normal / Large /
  Extra large, remembered), test pop-up and sounds, open log, quit.
  The hotkey (Ctrl+Alt+S by default) snoozes 30 minutes, press again to resume.
* Warns (tray message, plus a sound in daytime) if the NVR or a listed camera
  goes offline.

Everything you might change is in settings.toml next to this file.
Setup and test steps are in README.md.

Commands
--------
  py reolink_alerts.py --set-password        save the NVR password (once per PC)
  py reolink_alerts.py --check               test everything, print a report
  py reolink_alerts.py --monitors            list screens for the "monitor" setting
  py reolink_alerts.py --test Frontyard      sound + live pop-up for 15 seconds
  py reolink_alerts.py --test Backyard animal  a sound-only camera plays just its sound
  py reolink_alerts.py                       run (console log + tray icon)
  pyw reolink_alerts.py                      run with no console window
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time as dtime
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:          # Python older than 3.11
    tomllib = None

APP_NAME = "Reolink Alerts"
APP_ID = "reolink_alerts"
VERSION = "1.3.4"
IS_WINDOWS = os.name == "nt"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):            # the .exe built later with PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
SETTINGS_PATH = APP_DIR / "settings.toml"
LOG_PATH = APP_DIR / "reolink_alerts.log"
INPUT_CONF_PATH = APP_DIR / "popup_input.conf"
STATE_PATH = APP_DIR / "state.json"          # remembers the pop-up size picked in the tray

USER_CLOSED_EXIT = 10            # mpv exits with this code when the pop-up is clicked
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

log = logging.getLogger(APP_ID)

# Alert type -> the name reolink-aio uses for it.
AI_KEYS = {"person": "person", "animal": "pet", "vehicle": "vehicle", "package": "package"}
ALERT_TYPES = [*AI_KEYS, "motion"]          # "motion" = the camera's plain motion detection
TYPE_LABELS = {"person": "Person", "animal": "Animal", "vehicle": "Vehicle", "package": "Package",
               "motion": "Motion"}
TYPE_ORDER = ["person", "vehicle", "animal", "package", "motion"]      # most important first

# Pop-up feeds: Reolink's names and the NVR's stream names. Balanced comes from the
# NVR's FLV feed (Reolink serves it over FLV/RTMP, not RTSP); Clear and Fluent use RTSP.
STREAMS = {"main": "Clear", "ext": "Balanced", "sub": "Fluent"}
STREAM_NAMES = {"clear": "main", "main": "main", "balanced": "ext", "ext": "ext",
                "fluent": "sub", "sub": "sub"}
STEP_DOWN = {"main": ["ext", "sub"], "ext": ["sub"], "sub": []}   # when a feed won't play
CORNERS = {"bottom-right", "bottom-left", "top-right", "top-left"}


# =============================================================== settings ===

class SettingsError(Exception):
    """A problem in settings.toml, explained in plain words."""


@dataclass
class CameraCfg:
    name: str                       # the camera's name on the NVR
    label: str                      # what the pop-up and log call it
    day: list[str]
    night: list[str]
    sounds: dict[str, str]
    popup: bool = True
    sound: bool = True
    channel: int | None = None      # filled in after connecting
    main_url: str | None = None
    ext_url: str | None = None      # Balanced (FLV)
    sub_url: str | None = None


@dataclass
class Settings:
    nvr_address: str = ""                # set in settings.toml
    nvr_user: str = "alerts"
    nvr_https: bool = True
    night_start: dtime = dtime(22, 0)
    night_end: dtime = dtime(6, 0)
    monitor: str = "primary"
    size_percent: float = 40.0          # the active size, from sizes[size_name]
    size_name: str = "Normal"
    sizes: dict[str, float] = field(default_factory=lambda: {"Normal": 40.0, "Large": 55.0, "Extra large": 70.0})
    corner: str = "bottom-right"
    margin: int = 24
    gap: int = 12
    stream: str = "main"
    fallback_to_sub: bool = True
    max_popups: int = 1
    linger_seconds: float = 15
    min_seconds: float = 10
    max_seconds: float = 90
    cooldown_seconds: float = 30
    mute: bool = True
    click_to_close: bool = True
    wake_screen: bool = True
    sounds_on: bool = True
    volume: int = 100                   # 100 = each file's own level, never boosted
    repeat_seconds: float = 30
    clear_seconds: float = 120          # once per visit: quiet until none of that type is seen this long
    motion_alerts: bool = False         # plain motion alerts; the tray can switch them (remembered)
    motion_wait_seconds: float = 2      # wait this long for a person/animal/vehicle first
    offline_sound: str = ""
    offline_sound_at_night: bool = False
    offline_checks: int = 2
    nvr_offline_seconds: float = 120
    hotkey: str = "ctrl+alt+s"
    hotkey_minutes: int = 30
    snooze_choices: list[int] = field(default_factory=lambda: [15, 30, 60])
    mpv_path: str = ""
    tray: bool = True
    cameras: list[CameraCfg] = field(default_factory=list)


def _hhmm(text, key: str) -> dtime:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text))
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise SettingsError(f'{key} must be a 24-hour time like "23:59", not {text!r}.')
    return dtime(int(match[1]), int(match[2]))


def _types(value, key: str) -> list[str]:
    if not isinstance(value, list):
        raise SettingsError(f"{key} must be a list, like [\"person\", \"animal\"].")
    out = []
    for item in value:
        item = str(item).strip().lower()
        if item not in ALERT_TYPES:
            raise SettingsError(f"{key}: {item!r} isn't an alert type. Use: {', '.join(ALERT_TYPES)}.")
        out.append(item)
    return out


def _num(section: dict, key: str, default, low, high, where: str):
    value = section.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
        raise SettingsError(f"{where}.{key} must be a number from {low} to {high}, not {value!r}.")
    return value


def _bool(section: dict, key: str, default: bool, where: str) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise SettingsError(f"{where}.{key} must be true or false, not {value!r}.")
    return value


def resolve_path(path: str) -> str:
    """Expand %WINDIR% and friends; a relative path is taken from the program folder."""
    if not path:
        return ""
    expanded = os.path.expandvars(path)
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = APP_DIR / candidate
    return str(candidate)


def load_state(s: Settings) -> None:
    """Apply choices made in the tray: the pop-up size and motion alerts on/off."""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    name = data.get("popup_size")
    if name in s.sizes:
        s.size_name, s.size_percent = name, s.sizes[name]
    if isinstance(data.get("motion_alerts"), bool):
        s.motion_alerts = data["motion_alerts"]
    if data.get("popup_stream") in STREAMS:
        s.stream = data["popup_stream"]


def save_state(s: Settings) -> None:
    try:
        STATE_PATH.write_text(json.dumps({"popup_size": s.size_name, "popup_stream": s.stream,
                                          "motion_alerts": s.motion_alerts}), encoding="utf-8")
    except OSError as err:
        log.warning("Couldn't save %s (%s).", STATE_PATH.name, err)


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    if tomllib is None:
        raise SettingsError("Python 3.11 or newer is needed.")
    if not path.is_file():
        raise SettingsError(f"settings.toml wasn't found next to the program ({path}).")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as err:
        raise SettingsError(f"settings.toml has a typo: {err}") from err

    s = Settings()
    nvr = data.get("nvr", {})
    s.nvr_address = str(nvr.get("address", s.nvr_address)).strip()
    if not s.nvr_address:
        raise SettingsError('Put your NVR\'s IP address in settings.toml under [nvr]:  address = "..."')
    s.nvr_user = str(nvr.get("user", s.nvr_user)).strip()
    s.nvr_https = _bool(nvr, "https", s.nvr_https, "nvr")

    night = data.get("night", {})
    s.night_start = _hhmm(night.get("start", "22:00"), "night.start")
    s.night_end = _hhmm(night.get("end", "06:00"), "night.end")

    popup = data.get("popup", {})
    s.monitor = str(popup.get("monitor", s.monitor)).strip().lower()
    if s.monitor != "primary" and not s.monitor.isdigit():
        raise SettingsError('popup.monitor must be "primary" or a number from --monitors.')
    sizes = popup.get("sizes")
    if sizes is None:                                     # older settings files: a single size
        sizes = {"Normal": _num(popup, "size_percent", s.size_percent, 10, 100, "popup")}
    if not isinstance(sizes, dict) or not sizes:
        raise SettingsError('popup.sizes must look like { Normal = 40, Large = 55 }.')
    s.sizes = {}
    for name, pct in sizes.items():
        if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not 10 <= pct <= 100:
            raise SettingsError(f"popup.sizes: {name} must be a percent from 10 to 100, not {pct!r}.")
        s.sizes[str(name)] = float(pct)
    s.size_name = str(popup.get("size", next(iter(s.sizes))))
    if s.size_name not in s.sizes:
        raise SettingsError(f"popup.size must be one of: {', '.join(s.sizes)}.")
    s.size_percent = s.sizes[s.size_name]
    s.corner = str(popup.get("corner", s.corner)).strip().lower()
    if s.corner not in CORNERS:
        raise SettingsError(f"popup.corner must be one of: {', '.join(sorted(CORNERS))}.")
    s.margin = int(_num(popup, "margin", s.margin, 0, 2000, "popup"))
    s.gap = int(_num(popup, "gap", s.gap, 0, 2000, "popup"))
    stream = str(popup.get("stream", "clear")).strip().lower()
    if stream not in STREAM_NAMES:
        raise SettingsError('popup.stream must be "clear", "balanced" or "fluent".')
    s.stream = STREAM_NAMES[stream]
    s.fallback_to_sub = _bool(popup, "fallback_to_sub", s.fallback_to_sub, "popup")
    s.max_popups = int(_num(popup, "max_popups", s.max_popups, 1, 8, "popup"))
    s.linger_seconds = _num(popup, "linger_seconds", s.linger_seconds, 0, 600, "popup")
    s.min_seconds = _num(popup, "min_seconds", s.min_seconds, 0, 600, "popup")
    s.max_seconds = _num(popup, "max_seconds", s.max_seconds, 5, 3600, "popup")
    s.cooldown_seconds = _num(popup, "cooldown_seconds", s.cooldown_seconds, 0, 3600, "popup")
    s.mute = _bool(popup, "mute", s.mute, "popup")
    s.click_to_close = _bool(popup, "click_to_close", s.click_to_close, "popup")
    s.wake_screen = _bool(popup, "wake_screen", s.wake_screen, "popup")

    sound = data.get("sound", {})
    s.sounds_on = _bool(sound, "enabled", s.sounds_on, "sound")
    s.volume = int(_num(sound, "volume", s.volume, 0, 100, "sound"))
    s.repeat_seconds = _num(sound, "repeat_seconds", s.repeat_seconds, 0, 3600, "sound")
    s.clear_seconds = _num(sound, "clear_seconds", s.clear_seconds, 0, 3600, "sound")
    s.offline_sound = resolve_path(str(sound.get("offline", "")))
    s.offline_sound_at_night = _bool(sound, "offline_at_night", s.offline_sound_at_night, "sound")

    motion = data.get("motion", {})
    s.motion_alerts = _bool(motion, "enabled", s.motion_alerts, "motion")
    s.motion_wait_seconds = _num(motion, "wait_seconds", s.motion_wait_seconds, 0, 10, "motion")

    watch = data.get("watchdog", {})
    s.offline_checks = int(_num(watch, "camera_offline_checks", s.offline_checks, 1, 60, "watchdog"))
    s.nvr_offline_seconds = _num(watch, "nvr_offline_seconds", s.nvr_offline_seconds, 30, 3600, "watchdog")

    snooze = data.get("snooze", {})
    s.hotkey = str(snooze.get("hotkey", s.hotkey)).strip().lower()
    s.hotkey_minutes = int(_num(snooze, "hotkey_minutes", s.hotkey_minutes, 1, 1440, "snooze"))
    choices = snooze.get("choices", s.snooze_choices)
    if not isinstance(choices, list) or not all(isinstance(c, int) and 1 <= c <= 1440 for c in choices):
        raise SettingsError("snooze.choices must be a list of minutes, like [15, 30, 60].")
    s.snooze_choices = choices

    program = data.get("program", {})
    s.mpv_path = str(program.get("mpv_path", "")).strip()
    s.tray = _bool(program, "tray", s.tray, "program")

    cams = data.get("camera", [])
    if not isinstance(cams, list) or not cams:
        raise SettingsError("Add at least one [[camera]] section to settings.toml.")
    for i, cam in enumerate(cams, start=1):
        where = f"camera #{i}"
        name = str(cam.get("name", "")).strip()
        if not name:
            raise SettingsError(f"{where} needs a name (exactly as it's named on the NVR).")
        sounds = cam.get("sounds", {})
        if not isinstance(sounds, dict):
            raise SettingsError(f"{where}.sounds must look like {{ person = 'C:\\path\\file.wav' }}.")
        for key in sounds:
            if key not in ALERT_TYPES:
                raise SettingsError(f"{where}.sounds: {key!r} isn't an alert type. Use: {', '.join(ALERT_TYPES)}.")
        s.cameras.append(CameraCfg(
            name=name,
            label=str(cam.get("label", name)).strip() or name,
            day=_types(cam.get("day", ["person"]), f"{where}.day"),
            night=_types(cam.get("night", ["person"]), f"{where}.night"),
            sounds={key: resolve_path(str(val)) for key, val in sounds.items()},
            popup=_bool(cam, "popup", True, where),
            sound=_bool(cam, "sound", True, where),
        ))
    return s


# ================================================================== time ===

def in_window(now: dtime, start: dtime, end: dtime) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end                # crosses midnight


def is_night(s: Settings, now: datetime | None = None) -> bool:
    return in_window((now or datetime.now()).time(), s.night_start, s.night_end)


def watched_types(cam: CameraCfg, s: Settings, now: datetime | None = None) -> list[str]:
    types = cam.night if is_night(s, now) else cam.day
    if not s.motion_alerts:
        types = [kind for kind in types if kind != "motion"]
    return types


def size_named(s: Settings, text: str) -> str | None:
    """Match a typed size ("large", "Extra large", "extralarge") to one in settings.toml."""
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    return next((name for name in s.sizes if re.sub(r"[^a-z0-9]", "", name.lower()) == key), None)


def has_motion(s: Settings) -> bool:
    """True when any camera lists "motion", so the tray shows the on/off switch."""
    return any("motion" in cam.day or "motion" in cam.night for cam in s.cameras)


def clock_text(when: datetime) -> str:
    return when.strftime("%I:%M %p").lstrip("0")


def _duration(minutes: int) -> str:
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours > 1 else ''}"
    return f"{minutes} minutes"


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def mask(url: str | None) -> str:
    """Hide the password in a stream address (RTSP user:pass@ or FLV ...&password=)."""
    if not url:
        return "(none)"
    url = re.sub(r"(rtsps?://[^:/@]+):[^@]*@", r"\1:****@", url)
    return re.sub(r"((?:password|token)=).*$", r"\1****", url)     # the password is the last part


def stream_url(cam: CameraCfg, stream: str) -> str | None:
    return {"main": cam.main_url, "ext": cam.ext_url, "sub": cam.sub_url}.get(stream)


def next_stream(cam: CameraCfg, stream: str) -> str | None:
    """The next feed down (Clear > Balanced > Fluent) that has an address."""
    for lower in STEP_DOWN.get(stream, []):
        if stream_url(cam, lower):
            return lower
    return None


# ======================================================= Windows helpers ===

@dataclass
class Monitor:
    number: int                     # 1-based, as --monitors prints it
    left: int
    top: int
    right: int
    bottom: int
    work_left: int
    work_top: int
    work_right: int
    work_bottom: int
    primary: bool
    device: str


def set_dpi_awareness() -> None:
    """Use real pixels on scaled screens, the same coordinates mpv uses."""
    if not IS_WINDOWS:
        return
    import ctypes
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def list_monitors() -> list[Monitor]:
    if not IS_WINDOWS:
        return [Monitor(1, 0, 0, 1920, 1080, 0, 0, 1920, 1032, True, "test")]
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    found: list[Monitor] = []
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(RECT), wintypes.LPARAM)
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, proc_type, wintypes.LPARAM]

    def callback(hmon, _hdc, _rect, _param):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            m, w = info.rcMonitor, info.rcWork
            found.append(Monitor(len(found) + 1, m.left, m.top, m.right, m.bottom,
                                 w.left, w.top, w.right, w.bottom,
                                 bool(info.dwFlags & 1), info.szDevice))
        return 1

    enum_proc = proc_type(callback)
    user32.EnumDisplayMonitors(None, None, enum_proc, 0)
    return found


def pick_monitor(s: Settings, monitors: list[Monitor]) -> Monitor:
    if s.monitor.isdigit():
        for mon in monitors:
            if mon.number == int(s.monitor):
                return mon
        log.warning("Monitor %s not found; using the main screen.", s.monitor)
    for mon in monitors:
        if mon.primary:
            return mon
    return monitors[0]


def slot_geometry(s: Settings, mon: Monitor, slot: int) -> tuple[int, int, int, int]:
    """Size and position (relative to the monitor) of pop-up number `slot`."""
    work_w = mon.work_right - mon.work_left
    work_h = mon.work_bottom - mon.work_top
    width = max(160, min(int(work_w * s.size_percent / 100), work_w - 2 * s.margin))
    height = int(width * 9 / 16)
    if height > work_h - 2 * s.margin:                    # too tall for this screen: shrink to fit
        height = max(90, work_h - 2 * s.margin)
        width = int(height * 16 / 9)
    columns = max(1, (work_w - 2 * s.margin + s.gap) // (width + s.gap))
    col, row = slot % columns, slot // columns
    if s.corner.endswith("right"):
        x = mon.work_right - s.margin - width - col * (width + s.gap)
    else:
        x = mon.work_left + s.margin + col * (width + s.gap)
    if s.corner.startswith("bottom"):
        y = mon.work_bottom - s.margin - height - row * (height + s.gap)
    else:
        y = mon.work_top + s.margin + row * (height + s.gap)
    return width, height, x - mon.left, y - mon.top


def wake_display() -> None:
    if not IS_WINDOWS:
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
    ctypes.windll.kernel32.SetThreadExecutionState(
        ctypes.c_uint(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED))
    mouse_move = 0x0001                       # a 1-pixel nudge wakes a sleeping monitor
    ctypes.windll.user32.mouse_event(mouse_move, 1, 0, 0, 0)
    ctypes.windll.user32.mouse_event(mouse_move, -1, 0, 0, 0)


def release_display() -> None:
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(0x80000000))


def single_instance() -> bool:
    """Stop a second copy from running (for example Task Scheduler plus a manual start)."""
    if not IS_WINDOWS:
        return True
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    single_instance.handle = kernel32.CreateMutexW(None, False, "Local\\ReolinkAlertsSingleInstance")
    return ctypes.get_last_error() != 183                     # 183 = already exists


def foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    return user32.GetForegroundWindow() or 0


def windows_of(pid: int) -> list[int]:
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnds: list[int] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.EnumWindows.argtypes = [proc_type, wintypes.LPARAM]

    def callback(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    enum_proc = proc_type(callback)
    user32.EnumWindows(enum_proc, 0)
    return hwnds


def make_unfocusable(pid: int, previous_foreground: int) -> bool:
    """Keep the pop-up off the taskbar, unable to take focus, and on top."""
    hwnds = windows_of(pid)
    if not hwnds:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    get_style, set_style = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
    get_style.restype, get_style.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int]
    set_style.restype, set_style.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x08000000, 0x80, 0x40000
    flags = 0x0001 | 0x0002 | 0x0010 | 0x0020      # no size, no move, no activate, frame changed
    for hwnd in hwnds:
        style = get_style(hwnd, GWL_EXSTYLE)
        set_style(hwnd, GWL_EXSTYLE, (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, flags)    # -1 = topmost
    if previous_foreground and foreground_window() in hwnds:
        user32.SetForegroundWindow(wintypes.HWND(previous_foreground))
    return True


def window_rect(pid: int) -> tuple[int, int, int, int] | None:
    """The pop-up window's real position and size on the desktop: (x, y, width, height)."""
    hwnds = windows_of(pid)
    if not hwnds:
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnds[0], ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def place_window(pid: int, x: int, y: int, width: int, height: int) -> None:
    """Move and size the pop-up without taking focus or changing its on-top order."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    for hwnd in windows_of(pid):
        user32.SetWindowPos(hwnd, None, x, y, width, height, 0x0004 | 0x0010)   # no z-order, no activate


def no_activate_startupinfo():
    """Ask Windows to show mpv's window without activating it."""
    if not IS_WINDOWS:
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 4                          # SW_SHOWNOACTIVATE
    return info


# ==================================================================== mpv ===

def find_mpv(s: Settings) -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [resolve_path(s.mpv_path) if s.mpv_path else "",
                  r"C:\Program Files\MPV Player\mpv.exe",          # where winget's shinchiro.mpv installs
                  r"C:\Program Files\mpv\mpv.exe", r"C:\mpv\mpv.exe",
                  str(Path(local) / "Programs" / "mpv" / "mpv.exe") if local else ""]
    for path in candidates:
        if path and Path(path).is_file():
            return _prefer_exe(path)
    found = shutil.which("mpv")
    return _prefer_exe(found) if found else None


def _prefer_exe(path: str) -> str:
    """mpv.com is a console wrapper; use mpv.exe next to it so no console flashes."""
    p = Path(path)
    if p.suffix.lower() == ".com" and p.with_suffix(".exe").is_file():
        return str(p.with_suffix(".exe"))
    return str(p)


def write_input_conf(s: Settings) -> None:
    lines = ["# Written by Reolink Alerts: what a click on a pop-up does."]
    if s.click_to_close:
        lines += [f"MBTN_LEFT quit {USER_CLOSED_EXIT}", f"MBTN_LEFT_DBL quit {USER_CLOSED_EXIT}",
                  f"MBTN_RIGHT quit {USER_CLOSED_EXIT}"]
    try:
        INPUT_CONF_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as err:
        log.warning("Couldn't write %s (%s).", INPUT_CONF_PATH.name, err)


def pipe_name(cam: CameraCfg) -> str:
    return rf"\\.\pipe\reolink-alerts-{normalize(cam.name) or 'cam'}"


def popup_args(mpv: str, url: str, s: Settings, cam: CameraCfg, caption: str,
               screen: int, geometry: tuple[int, int, int, int]) -> list[str]:
    width, height, x, y = geometry
    args = [mpv, url,
            "--no-config",                       # ignore mpv.conf, scripts and saved state on this PC
            "--profile=low-latency", "--rtsp-transport=tcp", "--hwdec=auto-safe",
            "--network-timeout=8", "--ontop", "--no-border",
            f"--screen={screen}", f"--geometry={width}x{height}+{x}+{y}",
            "--force-window=immediate", "--keep-open=no", "--idle=no",
            "--no-osc", "--osd-bar=no", "--cursor-autohide=1000", "--window-dragging=no",
            "--no-input-default-bindings", f"--input-conf={INPUT_CONF_PATH}",
            f"--input-ipc-server={pipe_name(cam)}",
            f"--title={APP_NAME} - {cam.label}",
            f"--osd-playing-msg={caption}", "--osd-duration=6000", "--osd-font-size=30",
            "--no-terminal"]
    if s.mute:
        args.append("--aid=no")                 # no camera audio at all (lighter than muting)
    return args


# mpv spreads a mono file over two speakers at -3 dB each; this plays it at full level on
# both sides like other players do, and leaves stereo files exactly as they are.
MONO_FULL_LEVEL = "--af=lavfi=[pan=stereo|FL=FL+FC|FR=FR+FC]"


def sound_args(mpv: str, path: str, volume: int, mono_fix: bool = True) -> list[str]:
    args = [mpv, "--no-config", "--no-video", "--audio-display=no", "--force-window=no", "--no-terminal",
            f"--volume={volume}"]
    if mono_fix:
        args.append(MONO_FULL_LEVEL)
    return args + [path]


# ================================================================= sounds ===

class SoundQueue:
    """Plays alert sounds one after another, so two cameras don't talk over each other."""

    def __init__(self, launcher=subprocess.Popen) -> None:
        self.mpv: str | None = None
        self.volume = 100
        self.launcher = launcher
        self.queue: asyncio.Queue[str] | None = None

    def play(self, path: str) -> None:
        if not path:
            return
        if self.queue is None:
            self.queue = asyncio.Queue()
        self.queue.put_nowait(path)

    async def run(self) -> None:
        if self.queue is None:
            self.queue = asyncio.Queue()
        while True:
            path = await self.queue.get()
            try:
                await self.play_now(path)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Couldn't play %s", path)

    async def play_now(self, path: str) -> None:
        if not Path(path).is_file():
            log.warning("Sound file not found: %s", path)
            _beep()
            return
        if self.mpv:
            proc = self.launcher(sound_args(self.mpv, path, self.volume), creationflags=NO_WINDOW)
            code = await asyncio.to_thread(_wait_quietly, proc, 20)
            if code not in (0, None):            # e.g. a channel layout the mono fix can't handle
                proc = self.launcher(sound_args(self.mpv, path, self.volume, mono_fix=False),
                                     creationflags=NO_WINDOW)
                code = await asyncio.to_thread(_wait_quietly, proc, 20)
                if code not in (0, None):
                    log.warning("mpv couldn't play %s (code %s).", Path(path).name, code)
        elif IS_WINDOWS and path.lower().endswith(".wav"):
            import winsound
            await asyncio.to_thread(winsound.PlaySound, path, winsound.SND_FILENAME)
        else:
            log.warning("mpv isn't installed, so %s can't play.", Path(path).name)
            _beep()


def _wait_quietly(proc, timeout: float) -> int | None:
    """Wait for a sound to finish; returns mpv's exit code (None if it had to be stopped)."""
    try:
        return proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return None


def _beep() -> None:
    if IS_WINDOWS:
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:
            pass


# ============================================================ alert logic ===

@dataclass
class PopupWindow:
    proc: object
    slot: int
    stream: str
    opened_at: float
    test: bool = False              # opened from a test (steps down a feed even with no event)


class CameraAlert:
    """One camera's event state and its pop-up."""

    def __init__(self, cam: CameraCfg, core: "AlertCore") -> None:
        self.cam = cam
        self.core = core
        self.active: set[str] = set()
        self.event_active = False
        self.inactive_since: float | None = None
        self.cooldown_until = 0.0
        self.window: PopupWindow | None = None
        self.user_closed = False
        self.last_sound: dict[str, float] = {}
        self.offline_count = 0
        self.offline_warned = False
        self.specific_seen = False                       # a person/animal/vehicle in this event
        self.motion_pending_since: float | None = None   # plain motion waiting for its sound
        self.motion_pending_away = float("inf")          # how long motion was gone when the wait began
        self.last_seen: dict[str, float] = {}            # type -> last time this camera reported it
        self.away_for: dict[str, float] = {}             # type -> how long it was gone before it came back

    def top_type(self) -> str | None:
        for kind in TYPE_ORDER:
            if kind in self.active:
                return kind
        return None

    def update(self, detected: set[str], now: float) -> None:
        for kind in self.active - detected:             # just went out of view: remember when
            self.last_seen[kind] = now
        new = detected - self.active
        for kind in new:                                 # back in view: how long was it gone?
            self.away_for[kind] = now - self.last_seen.get(kind, float("-inf"))
        for kind in detected:
            self.last_seen[kind] = now
        self.active = set(detected)
        if detected:
            if not self.event_active:                   # a new event started
                self.event_active = True
                self.user_closed = False
                self.specific_seen = False
            self.inactive_since = None
            if detected - {"motion"}:                   # a person/animal/vehicle beats plain motion
                self.specific_seen = True
                self.motion_pending_since = None
            for kind in sorted(new, key=TYPE_ORDER.index):
                if kind == "motion":
                    if not self.specific_seen:          # sound it only if nothing more specific follows
                        if self.motion_pending_since is None:
                            self.motion_pending_away = self.away_for[kind]
                        self.motion_pending_since = now
                else:
                    self.core.announce(self, kind, now)
            if self.window is None:
                self.core.maybe_open(self, now)
            elif new:
                self.core.caption(self)
        elif self.event_active:                         # the event just ended
            self.event_active = False
            self.inactive_since = now


class AlertCore:
    """Decides when to sound, open and close. No network code in here."""

    def __init__(self, s: Settings, launcher=subprocess.Popen, clock=time.monotonic,
                 monitors=list_monitors) -> None:
        self.s = s
        self.launcher = launcher
        self.clock = clock
        self.monitors = monitors
        self.mpv: str | None = None
        self.alerts = [CameraAlert(cam, self) for cam in s.cameras]
        self.sounds = SoundQueue(launcher)
        self.sounds.volume = s.volume
        self.snooze_until: datetime | None = None
        self.ui = ConsoleUI()

    # -- snooze ---------------------------------------------------------------
    @property
    def snoozed(self) -> bool:
        return self.snooze_until is not None and datetime.now() < self.snooze_until

    def check_snooze(self) -> None:
        """Called from the engine loop: ends a snooze whose time is up."""
        if self.snooze_until is not None and datetime.now() >= self.snooze_until:
            self.snooze_until = None
            log.info("Snooze over; alerts are back on.")
            self.ui.notify("Snooze over; alerts are back on.")
            self.ui.refresh()

    def snooze(self, minutes: int) -> None:
        self.snooze_until = datetime.now() + timedelta(minutes=minutes)
        self.close_all("snoozed")
        log.info("Snoozed for %s minutes (until %s).", minutes, clock_text(self.snooze_until))
        self.ui.refresh()

    def resume(self) -> None:
        self.snooze_until = None
        log.info("Alerts resumed.")
        self.ui.refresh()

    def set_size(self, name: str) -> None:
        if name not in self.s.sizes:
            return
        self.s.size_name, self.s.size_percent = name, self.s.sizes[name]
        save_state(self.s)
        log.info("Pop-up size set to %s (%s%% of the screen width).", name, f"{self.s.size_percent:g}")
        self.ui.refresh()

    def set_stream(self, stream: str) -> None:
        if stream not in STREAMS:
            return
        self.s.stream = stream
        save_state(self.s)
        log.info("Pop-up feed set to %s.", STREAMS[stream])
        self.ui.refresh()

    def set_motion(self, on: bool) -> None:
        self.s.motion_alerts = on
        if not on:
            for alert in self.alerts:
                alert.motion_pending_since = None
        save_state(self.s)
        log.info("Motion alerts turned %s.", "on" if on else "off")
        self.ui.refresh()

    def toggle_snooze(self, minutes: int) -> None:
        if self.snoozed:
            self.resume()
            self.ui.notify("Alerts are back on.")
        else:
            self.snooze(minutes)
            self.ui.notify(f"Snoozed until {clock_text(self.snooze_until)}.")

    # -- events ---------------------------------------------------------------
    def evaluate(self, host, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        for alert in self.alerts:
            ch = alert.cam.channel
            if ch is None:
                continue
            detected = set()
            for kind in watched_types(alert.cam, self.s):
                if kind == "motion":
                    if host.motion_detected(ch):            # the camera's own motion detection
                        detected.add(kind)
                elif host.ai_detected(ch, AI_KEYS[kind]):
                    detected.add(kind)
            alert.update(detected, now)

    def announce(self, alert: CameraAlert, kind: str, now: float, away: float | None = None) -> None:
        """A type just came into view. Sound it once per visit: a new visit needs that
        type to have been out of view for clear_seconds (motion passes in its own `away`)."""
        if away is None:
            away = alert.away_for.get(kind, float("inf"))
        if self.s.clear_seconds and away < self.s.clear_seconds:
            log.info("%s at %s (same visit, back after %.0f s: no sound).",
                     TYPE_LABELS[kind], alert.cam.label, away)
            return
        log.info("%s at %s.", TYPE_LABELS[kind], alert.cam.label)
        if self.snoozed or not self.s.sounds_on or not alert.cam.sound:
            return
        if now - alert.last_sound.get(kind, float("-inf")) < self.s.repeat_seconds:
            return
        alert.last_sound[kind] = now
        path = alert.cam.sounds.get(kind)
        if path:
            self.sounds.play(path)

    def maybe_open(self, alert: CameraAlert, now: float) -> None:
        if self.snoozed or not alert.cam.popup or alert.user_closed:
            return
        if now < alert.cooldown_until:
            log.info("%s: still cooling down after a long event; no pop-up.", alert.cam.label)
            return
        slot = self.free_slot()
        if slot is None:
            log.info("%s: %s pop-ups already open; not adding another.", alert.cam.label, self.s.max_popups)
            return
        self.open_window(alert, slot, now, self.s.stream)

    def free_slot(self) -> int | None:
        used = {a.window.slot for a in self.alerts if a.window}
        for slot in range(self.s.max_popups):
            if slot not in used:
                return slot
        return None

    def open_window(self, alert: CameraAlert, slot: int, now: float, stream: str,
                    caption: str | None = None, test: bool = False) -> bool:
        cam = alert.cam
        url = stream_url(cam, stream)
        if not url:                                          # use the next feed that has an address
            for other in STEP_DOWN.get(stream, []) + ["sub", "ext", "main"]:
                if stream_url(cam, other):
                    stream, url = other, stream_url(cam, other)
                    break
        if not self.mpv or not url:
            log.error("%s: can't open a pop-up (%s).", cam.label,
                      "mpv not found" if not self.mpv else "no stream address yet")
            return False
        monitors = self.monitors() or [Monitor(1, 0, 0, 1920, 1080, 0, 0, 1920, 1032, True, "?")]
        mon = pick_monitor(self.s, monitors)
        geometry = slot_geometry(self.s, mon, slot)
        if caption is None:
            kind = alert.top_type() or "person"
            caption = f"{TYPE_LABELS[kind]} · {cam.label}  {clock_text(datetime.now())}"
        args = popup_args(self.mpv, url, self.s, cam, caption, mon.number - 1, geometry)
        previous = foreground_window()
        if self.s.wake_screen:
            wake_display()
        kwargs = {"creationflags": NO_WINDOW}
        info = no_activate_startupinfo()
        if info is not None:
            kwargs["startupinfo"] = info
        try:
            proc = self.launcher(args, **kwargs)
        except OSError as err:
            log.error("Couldn't start mpv: %s", err)
            return False
        alert.window = PopupWindow(proc, slot, stream, now, test)
        width, height, x, y = geometry
        log.info("%s: pop-up opened (%s feed, %s %sx%s on screen %s, slot %s).", cam.label,
                 STREAMS[stream], self.s.size_name, width, height, mon.number, slot + 1)
        if IS_WINDOWS:
            self._spawn(self._unfocus(proc, previous, (mon.left + x, mon.top + y, width, height),
                                      cam.label))
        return True

    async def _unfocus(self, proc, previous: int, want=None, label: str = "") -> None:
        for _ in range(40):                                  # up to 10 seconds
            if proc.poll() is not None:
                return
            if make_unfocusable(proc.pid, previous):
                break
            await asyncio.sleep(0.25)
        else:
            return
        if want is None:
            return
        warned = False
        for delay in (0, 0.4, 0.6, 1.5, 2.5, 5):            # right away, and again as the video starts
            await asyncio.sleep(delay)
            if proc.poll() is not None:
                return
            have = window_rect(proc.pid)
            if have and any(abs(a - b) > 8 for a, b in zip(have, want)):
                if not warned:
                    log.warning("%s: the pop-up came out %sx%s at %s,%s; setting it to %sx%s at %s,%s.",
                                label, have[2], have[3], have[0], have[1], want[2], want[3], want[0], want[1])
                    warned = True
                place_window(proc.pid, *want)

    def caption(self, alert: CameraAlert) -> None:
        kind = alert.top_type()
        if alert.window is None or not kind or not IS_WINDOWS:
            return
        text = f"{TYPE_LABELS[kind]} · {alert.cam.label}  {clock_text(datetime.now())}"
        self._spawn(_show_text(pipe_name(alert.cam), text))

    def _spawn(self, coro) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()                                     # no event loop (unit tests)

    def any_open(self) -> bool:
        return any(a.window for a in self.alerts)

    def tick(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        for alert in self.alerts:
            self._motion_due(alert, now)
            self._tick_one(alert, now)

    def _motion_due(self, alert: CameraAlert, now: float) -> None:
        """Plain motion with no person/animal/vehicle after the wait: play the motion sound."""
        since = alert.motion_pending_since
        if since is None or now - since < self.s.motion_wait_seconds:
            return
        alert.motion_pending_since = None
        if not alert.specific_seen and self.s.motion_alerts:
            self.announce(alert, "motion", now, away=alert.motion_pending_away)

    def _tick_one(self, alert: CameraAlert, now: float) -> None:
        win = alert.window
        if win is None:
            return
        code = win.proc.poll()
        if code is not None:                                 # mpv ended by itself
            alert.window = None
            if code == USER_CLOSED_EXIT:
                alert.user_closed = True
                log.info("%s: pop-up closed by a click.", alert.cam.label)
            elif (self.s.fallback_to_sub and (alert.event_active or win.test)
                  and now - win.opened_at < 12 and next_stream(alert.cam, win.stream)):
                lower = next_stream(alert.cam, win.stream)
                log.warning("%s: the %s feed didn't play (mpv code %s); trying %s.",
                            alert.cam.label, STREAMS[win.stream], code, STREAMS[lower])
                self.open_window(alert, win.slot, now, lower, test=win.test)
            else:
                log.info("%s: pop-up ended (mpv code %s).", alert.cam.label, code)
            self._release_if_idle()
            return
        shown = now - win.opened_at
        if shown >= self.s.max_seconds:
            self.close(alert, f"max time {self.s.max_seconds:.0f} s")
            if alert.event_active:
                alert.cooldown_until = now + self.s.cooldown_seconds
            return
        if (not alert.event_active and alert.inactive_since is not None
                and now - alert.inactive_since >= self.s.linger_seconds
                and shown >= self.s.min_seconds):
            self.close(alert, f"event over, shown {shown:.0f} s")

    def close(self, alert: CameraAlert, reason: str) -> None:
        win = alert.window
        if win is None:
            return
        alert.window = None
        try:
            win.proc.terminate()
            win.proc.wait(timeout=3)
        except Exception:
            try:
                win.proc.kill()
            except Exception:
                pass
        log.info("%s: pop-up closed (%s).", alert.cam.label, reason)
        self._release_if_idle()

    def close_all(self, reason: str) -> None:
        for alert in self.alerts:
            self.close(alert, reason)

    def _release_if_idle(self) -> None:
        if not self.any_open():
            release_display()

    # -- offline warnings -----------------------------------------------------
    def offline_alarm(self) -> None:
        if not self.s.offline_sound or (is_night(self.s) and not self.s.offline_sound_at_night):
            return
        self.sounds.play(self.s.offline_sound)


async def _show_text(pipe: str, text: str, ms: int = 5000) -> None:
    message = json.dumps({"command": ["show-text", text, ms]}) + "\n"
    for _ in range(20):
        try:
            with open(pipe, "w", encoding="utf-8") as handle:
                handle.write(message)
            return
        except OSError:
            await asyncio.sleep(0.25)


# ===================================================================== UI ===

class ConsoleUI:
    """Used when there's no tray icon: everything just goes to the log."""

    def notify(self, message: str, title: str = APP_NAME) -> None:
        log.info("%s: %s", title, message)

    def problem(self, message: str) -> None:
        log.warning("Problem: %s", message)

    def clear_problem(self) -> None:
        pass

    def refresh(self) -> None:
        pass


class TrayUI:
    """System-tray icon with the snooze and test menu."""

    COLORS = {"ok": (46, 125, 50), "snoozed": (249, 168, 37), "problem": (198, 40, 40),
              "starting": (117, 117, 117)}

    def __init__(self, app: "App") -> None:
        self.app = app
        self.icon = None
        self._menu_lock = threading.Lock()          # one menu rebuild at a time
        self.problem_text: str | None = None
        self.connected = False

    # menu helpers
    def status_text(self) -> str:
        core = self.app.core
        if self.problem_text:
            return self.problem_text
        if not self.connected:
            return "Connecting to the NVR..."
        if core.snoozed:
            return f"Snoozed until {clock_text(core.snooze_until)}"
        if is_night(core.s):
            mode = "night: people only"
        elif core.s.motion_alerts and has_motion(core.s):
            mode = "day, motion alerts on"
        else:
            mode = "day"
        return f"Watching {len([a for a in core.alerts if a.cam.channel is not None])} cameras ({mode})"

    def _image(self, state: str):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((4, 4, 60, 60), fill=self.COLORS[state] + (255,))
        draw.rounded_rectangle((16, 22, 40, 42), radius=4, fill=(255, 255, 255, 255))   # camera body
        draw.polygon([(40, 28), (50, 22), (50, 42), (40, 36)], fill=(255, 255, 255, 255))  # lens
        return img

    def _state(self) -> str:
        if self.problem_text:
            return "problem"
        if not self.connected:
            return "starting"
        return "snoozed" if self.app.core.snoozed else "ok"

    def run(self) -> None:
        import pystray
        core = self.app.core
        item = pystray.MenuItem
        snooze_items = [item(f"Snooze {_duration(m)}", self._snooze_action(m)) for m in core.s.snooze_choices]
        size_items = [item(f"{name} ({pct:g}% wide)", self._size_action(name),
                           checked=self._size_checked(name), radio=True)
                      for name, pct in core.s.sizes.items()]
        feed_items = [item(label, self._stream_action(key), checked=self._stream_checked(key), radio=True)
                      for key, label in (("main", "Clear (sharpest)"), ("ext", "Balanced"),
                                         ("sub", "Fluent (lightest)"))]
        test_items = [item(f"{a.cam.label} pop-up", self._test_action(a.cam.name))
                      for a in core.alerts if a.cam.popup]
        sound_items = [item(f"{a.cam.label} {kind} sound", self._sound_action(path))
                       for a in core.alerts for kind, path in a.cam.sounds.items()]
        motion_items = []
        if has_motion(core.s):
            motion_items = [item("Motion alerts (daytime)", self._motion_action,
                                 checked=lambda _i: core.s.motion_alerts)]
        menu = pystray.Menu(
            item(lambda _i: self.status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            *snooze_items,
            item("Resume alerts", self._resume_action, enabled=lambda _i: core.snoozed),
            pystray.Menu.SEPARATOR,
            item("Pop-up size", pystray.Menu(*size_items)),
            item("Pop-up feed", pystray.Menu(*feed_items)),
            *motion_items,
            item("Test", pystray.Menu(*test_items, pystray.Menu.SEPARATOR, *sound_items)),
            item("Open the log", lambda _icon, _item: _open(LOG_PATH)),
            item("Open settings", lambda _icon, _item: _open(SETTINGS_PATH)),
            item("Quit", self._quit),
        )
        self.icon = pystray.Icon(APP_ID, self._image("starting"), APP_NAME, menu)
        self.icon.run(setup=self._setup)

    def _setup(self, icon) -> None:
        icon.visible = True
        self.app.start_background()

    # Menu clicks change the setting right here, before pystray rebuilds the menu, so the
    # check marks show the new choice at once (the menu is rebuilt straight after a click).
    def _snooze_action(self, minutes: int):
        def act(_icon, _item):
            core = self.app.core
            core.snooze_until = datetime.now() + timedelta(minutes=minutes)
            self.app.call(core.snooze, minutes)                  # closes pop-ups, logs
        return act

    def _resume_action(self, _icon, _item) -> None:
        self.app.core.snooze_until = None
        self.app.call(self.app.core.resume)

    def _test_action(self, name: str):
        return lambda _icon, _item: self.app.call_async(self.app.test_in_app(name))

    def _sound_action(self, path: str):
        return lambda _icon, _item: self.app.call(self.app.core.sounds.play, path)

    def _size_action(self, name: str):
        return lambda _icon, _item: self.app.core.set_size(name)

    def _size_checked(self, name: str):
        return lambda _item: self.app.core.s.size_name == name

    def _stream_action(self, stream: str):
        return lambda _icon, _item: self.app.core.set_stream(stream)

    def _stream_checked(self, stream: str):
        return lambda _item: self.app.core.s.stream == stream

    def _motion_action(self, _icon, _item) -> None:
        core = self.app.core
        core.set_motion(not core.s.motion_alerts)

    def _quit(self, icon, _item) -> None:
        self.app.stop()
        icon.stop()

    # called from the worker thread
    def notify(self, message: str, title: str = APP_NAME) -> None:
        log.info("%s: %s", title, message)
        try:
            if self.icon is not None:
                self.icon.notify(message, title)
        except Exception:
            pass

    def problem(self, message: str) -> None:
        log.warning("Problem: %s", message)
        self.problem_text = message
        self.notify(message, f"{APP_NAME}: problem")
        self.refresh()

    def clear_problem(self) -> None:
        if self.problem_text:
            self.problem_text = None
            self.refresh()

    def refresh(self) -> None:
        try:
            if self.icon is not None:
                self.icon.icon = self._image(self._state())
                self.icon.title = f"{APP_NAME}: {self.status_text()}"
                with self._menu_lock:
                    self.icon.update_menu()
        except Exception:
            log.debug("Tray refresh failed", exc_info=True)


def _open(path: Path) -> None:
    """Open the log or settings in Notepad (.toml has no default app on Windows)."""
    if not IS_WINDOWS:
        return
    try:
        subprocess.Popen(["notepad.exe", str(path)])
    except OSError as err:
        log.warning("Couldn't open %s (%s).", path, err)


# ================================================================ hotkey ===

VK_NAMES = {f"f{n}": 0x6F + n for n in range(1, 13)}


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    mods_map = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
    parts = [p.strip() for p in spec.lower().split("+") if p.strip()]
    if not parts:
        return None
    mods, key = 0, parts[-1]
    for part in parts[:-1]:
        if part not in mods_map:
            return None
        mods |= mods_map[part]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key in VK_NAMES:
        vk = VK_NAMES[key]
    else:
        return None
    return mods, vk


def start_hotkey(spec: str, callback) -> None:
    if not IS_WINDOWS or not spec:
        return
    parsed = parse_hotkey(spec)
    if not parsed:
        log.warning('Hotkey "%s" isn\'t understood (try "ctrl+alt+s").', spec)
        return
    mods, vk = parsed

    def worker() -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, mods | 0x4000, vk):         # 0x4000 = no auto-repeat
            log.warning('Hotkey "%s" is already used by another program.', spec)
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:                                       # WM_HOTKEY
                callback()

    threading.Thread(target=worker, name="hotkey", daemon=True).start()


# ============================================================ credentials ===

def credential_key(s: Settings) -> str:
    return f"{s.nvr_user}@{s.nvr_address}"


def get_password(s: Settings) -> str | None:
    env = os.environ.get("REOLINK_ALERTS_PASSWORD")
    if env:
        return env
    try:
        import keyring
        return keyring.get_password(APP_ID, credential_key(s))
    except Exception as err:
        log.error("Couldn't read the saved password (%s).", err)
        return None


def set_password(s: Settings) -> None:
    import getpass
    import keyring
    pw = getpass.getpass(f"Password for NVR user '{s.nvr_user}' on {s.nvr_address} (typing is hidden): ")
    if not pw:
        print("Nothing entered; no change.")
        return
    keyring.set_password(APP_ID, credential_key(s), pw)
    print("Saved in Windows Credential Manager.")


# ============================================================== NVR link ===

def resolve_channels(host, s: Settings) -> list[str]:
    """Match each [[camera]] name to an NVR channel. Returns the names not found."""
    by_name = {normalize(host.camera_name(ch)): ch for ch in host.channels}
    missing = []
    for cam in s.cameras:
        cam.channel = by_name.get(normalize(cam.name))
        if cam.channel is None:
            missing.append(cam.name)
    return missing


async def make_host(s: Settings, password: str):
    from reolink_aio.api import Host
    host = Host(s.nvr_address, s.nvr_user, password, use_https=s.nvr_https)
    await host.get_host_data()
    return host


async def fetch_urls(host, s: Settings) -> None:
    for cam in s.cameras:
        if cam.channel is None:
            continue
        for stream in ("main", "sub"):
            try:
                url = await host.get_rtsp_stream_source(cam.channel, stream)
            except Exception as err:
                log.warning("%s: couldn't get the %s stream address (%s).", cam.label, STREAMS[stream], err)
                url = None
            setattr(cam, f"{stream}_url", url)
        if cam.popup:                                   # Balanced: the NVR's FLV feed
            try:
                cam.ext_url = host.get_flv_stream_source(cam.channel, "ext")
            except Exception as err:
                log.warning("%s: couldn't get the Balanced stream address (%s).", cam.label, err)
                cam.ext_url = None


async def safe_logout(host) -> None:
    if host is None:
        return
    for step in (host.baichuan.unsubscribe_events, host.logout):
        try:
            await asyncio.wait_for(step(), 5)
        except Exception:
            pass


class App:
    """Runs the NVR link and alert logic on a background asyncio thread."""

    def __init__(self, s: Settings, password: str) -> None:
        self.s = s
        self.password = password
        self.core = AlertCore(s)
        self.ui = ConsoleUI()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.stop_event: asyncio.Event | None = None
        self.host = None

    # -- thread plumbing --------------------------------------------------------
    def start_background(self) -> None:
        self.thread = threading.Thread(target=self._thread_main, name="alerts", daemon=True)
        self.thread.start()

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.main())
        except Exception:
            log.exception("The alert engine stopped with an error.")
        finally:
            self.loop.close()

    def call(self, func, *args) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(func, *args)

    def call_async(self, coro) -> None:
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        else:
            coro.close()

    def stop(self) -> None:
        if self.loop is not None and self.stop_event is not None:
            self.loop.call_soon_threadsafe(self.stop_event.set)

    # -- main ---------------------------------------------------------------------
    async def main(self) -> None:
        self.stop_event = asyncio.Event()
        self.core.ui = self.ui
        self.core.mpv = find_mpv(self.s)
        self.core.sounds.mpv = self.core.mpv
        if not self.core.mpv:
            self.ui.problem("mpv wasn't found. Install it or set mpv_path in settings.toml.")
        write_input_conf(self.s)
        start_hotkey(self.s.hotkey, lambda: self.call(self.core.toggle_snooze, self.s.hotkey_minutes))
        tasks = [asyncio.create_task(self.core.sounds.run()),
                 asyncio.create_task(self.link())]
        await self.stop_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.core.close_all("quitting")
        await safe_logout(self.host)
        log.info("Stopped.")

    async def sleep(self, seconds: float) -> bool:
        """Sleep, but wake early if quitting. Returns True when it's time to quit."""
        try:
            await asyncio.wait_for(self.stop_event.wait(), seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def link(self) -> None:
        from reolink_aio.exceptions import CredentialsInvalidError, LoginError
        down_since: float | None = None
        warned = False
        while True:
            self.host = None
            delay = 30
            try:
                self.host = await make_host(self.s, self.password)
                missing = resolve_channels(self.host, self.s)
                names = ", ".join(self.host.camera_name(ch) for ch in self.host.channels)
                for name in missing:
                    self.ui.notify(f'Camera "{name}" isn\'t on the NVR. Names there: {names}.')
                await fetch_urls(self.host, self.s)
                self.host.baichuan.register_callback(APP_ID, self.on_push, cmd_id=33)
                await self.host.baichuan.subscribe_events()
                log.info("Connected to %s (%s). Watching: %s.", self.host.nvr_name, self.s.nvr_address,
                         ", ".join(f"{c.label} (ch {c.channel + 1})" for c in self.s.cameras if c.channel is not None))
                if warned:
                    self.ui.notify("The NVR is back online.")
                warned, down_since = False, None
                if isinstance(self.ui, TrayUI):
                    self.ui.connected = True
                self.ui.clear_problem()
                self.ui.refresh()
                await self.watch(self.host)
                return                                            # watch() returns only when quitting
            except CredentialsInvalidError:
                self.ui.problem("The NVR rejected the password. Run --set-password, then restart.")
                self.core.close_all("login failed")
                return                                            # don't retry: wrong passwords lock the NVR
            except LoginError as err:
                log.warning("NVR login problem (%s). Trying again in 5 minutes.", err)
                delay = 300
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.warning("Lost contact with the NVR (%s). Trying again in 30 s.", err)
            finally:
                if self.host is not None:
                    try:
                        self.host.baichuan.unregister_callback(APP_ID)
                    except Exception:
                        pass
            self.core.close_all("connection lost")
            if isinstance(self.ui, TrayUI):
                self.ui.connected = False
            down_since = down_since or time.monotonic()
            if not warned and time.monotonic() - down_since >= self.s.nvr_offline_seconds:
                self.ui.problem(f"Can't reach the NVR at {self.s.nvr_address}.")
                self.core.offline_alarm()
                warned = True
            await safe_logout(self.host)
            if await self.sleep(delay):
                return

    def on_push(self) -> None:
        try:
            if self.host is not None:
                self.core.evaluate(self.host)
        except Exception:
            log.exception("Error handling a camera event")

    async def watch(self, host) -> None:
        last_keepalive = last_status = last_poll = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now - last_keepalive >= 60:                         # keeps the push link healthy
                await host.baichuan.check_subscribe_events()
                last_keepalive = now
            if now - last_status >= 60:
                await self.check_cameras(host)
                self.ui.refresh()                                  # day/night text in the tray
                last_status = now
            poll_every = 3 if self.core.any_open() else 5
            if (not host.baichuan.events_active or self.core.any_open()) and now - last_poll >= poll_every:
                last_poll = now
                await host.get_motion_state_all_ch()
                self.core.evaluate(host)
            self.core.tick()
            self.core.check_snooze()
            await asyncio.sleep(0.25)

    async def check_cameras(self, host) -> None:
        try:
            await host.get_state("GetChannelstatus")
        except Exception as err:
            log.debug("Channel status check failed: %s", err)
            return
        for alert in self.core.alerts:
            ch = alert.cam.channel
            if ch is None:
                continue
            if host.camera_online(ch):
                if alert.offline_warned:
                    self.ui.notify(f"{alert.cam.label} is back online.")
                    alert.offline_warned = False
                    self.ui.clear_problem()
                alert.offline_count = 0
            else:
                alert.offline_count += 1
                if alert.offline_count >= self.s.offline_checks and not alert.offline_warned:
                    alert.offline_warned = True
                    self.ui.problem(f"{alert.cam.label} camera is offline.")
                    self.core.offline_alarm()

    async def test_in_app(self, name: str) -> None:
        """Tray > Test a pop-up: the camera's first daytime sound plus its pop-up."""
        alert = next((a for a in self.core.alerts if a.cam.name == name), None)
        if alert is None or alert.window is not None or alert.cam.channel is None:
            return
        kind = (alert.cam.day or ["person"])[0]
        path = alert.cam.sounds.get(kind)
        if path:
            self.core.sounds.play(path)
        slot = self.core.free_slot()
        if slot is None:
            return
        now = time.monotonic()
        if self.core.open_window(alert, slot, now, self.s.stream, caption=f"Test · {alert.cam.label}",
                                 test=True):
            if not alert.event_active:
                alert.inactive_since = now        # closes like an event that just ended


# ======================================================== check and test ===

async def tcp_open(host: str, port: int, timeout: float = 3) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def probe_flv(url: str | None, timeout: float = 6) -> bool:
    """True if the NVR's FLV (Balanced) feed starts sending video."""
    if not url:
        return False
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(url, ssl=False) as resp:        # the NVR's own certificate
                if resp.status != 200:
                    return False
                return (await resp.content.readexactly(3)) == b"FLV"
    except Exception:
        return False


async def run_check(s: Settings) -> None:
    print(f"{APP_NAME} {VERSION} check  ({datetime.now():%Y-%m-%d %H:%M})")
    print(f"Python {sys.version.split()[0]}   folder {APP_DIR}")
    mpv = find_mpv(s)
    print(f"mpv: {mpv or 'NOT FOUND - install it or set program.mpv_path'}")
    print(f"Mode now: {'NIGHT (people only)' if is_night(s) else 'day'}   "
          f"night is {s.night_start:%H:%M} to {s.night_end:%H:%M}")
    print(f"Pop-up: {s.size_name} ({s.size_percent:g}% of the screen width), screen {s.monitor}, {s.corner}")
    steps = " then ".join(STREAMS[x] for x in STEP_DOWN[s.stream])
    print(f"Pop-up feed: {STREAMS[s.stream]}"
          + (f"  (if it won't play: {steps})" if steps and s.fallback_to_sub else ""))
    print(f"Sounds: volume {s.volume} of 100 (100 = the file's own level)")
    if s.clear_seconds:
        clear = f"{s.clear_seconds / 60:g} min" if s.clear_seconds >= 60 else f"{s.clear_seconds:g} s"
        print(f"        once per visit: the same camera + type sounds again only after {clear} "
              "with none of that type in view")
    else:
        print(f"        once per visit is off: the same camera + type can repeat after {s.repeat_seconds:g} s")
    if has_motion(s):
        print(f"Motion alerts: {'ON' if s.motion_alerts else 'off'} (tray menu > Motion alerts switches them)")
    print("\nNVR ports:")
    web_port = 443 if s.nvr_https else 80
    for label, port in ((f"{'HTTPS' if s.nvr_https else 'HTTP'} (login)", web_port),
                        ("RTSP (video)", 554), ("Event push", 9000)):
        state = "open" if await tcp_open(s.nvr_address, port) else "CLOSED"
        print(f"  {label:<16} {s.nvr_address}:{port:<5} {state}")

    password = get_password(s)
    if not password:
        print("\nNo password saved yet. Run:  py reolink_alerts.py --set-password")
        return
    host = None
    try:
        host = await make_host(s, password)
        print(f"\nLogged in to: {host.nvr_name}  model {host.model}  firmware {host.sw_version}")
        print("Channels on the NVR:")
        for ch in host.channels:
            types = ", ".join(host.ai_supported_types(ch)) or "none"
            state = "online" if host.camera_online(ch) else "OFFLINE"
            print(f"  ch {ch + 1:<2} {host.camera_name(ch):<16} {host.camera_model(ch):<14} {state:<8} AI: {types}")
        missing = resolve_channels(host, s)
        await fetch_urls(host, s)
        print("\nCameras in settings.toml:")
        for cam in s.cameras:
            if cam.channel is None:
                print(f"  {cam.label}: NOT FOUND (no channel named {cam.name!r})")
                continue
            print(f"  {cam.label} -> ch {cam.channel + 1}   day: {', '.join(cam.day)}   night: {', '.join(cam.night)}")
            for kind in sorted(set(cam.day + cam.night), key=TYPE_ORDER.index):
                if kind == "motion":
                    if not host.supported(cam.channel, "motion_detection"):
                        print("     note: this camera doesn't report plain motion")
                elif not host.ai_supported(cam.channel, AI_KEYS[kind]):
                    print(f"     note: this camera doesn't report {kind} detection")
            for kind, path in cam.sounds.items():
                print(f"     sound {kind:<8} {'ok' if Path(path).is_file() else 'MISSING'}  {path}")
            print(f"     Clear:    {mask(cam.main_url)}")
            if cam.popup:
                verdict = "plays" if await probe_flv(cam.ext_url) else "NO VIDEO"
                print(f"     Balanced: {mask(cam.ext_url)}  -> {verdict}")
                if verdict != "plays":
                    rtmp = getattr(host, "rtmp_enabled", None)
                    if rtmp is False:
                        print("               RTMP is off on the NVR, and Balanced most likely needs it: tick RTMP")
                        print("               in the NVR's Server Settings (where HTTPS and RTSP are), then --check again.")
                    else:
                        print("               Balanced won't play; pop-ups will step down to Fluent.")
            print(f"     Fluent:   {mask(cam.sub_url)}")
        if missing:
            print(f"\nFix the name(s) {', '.join(missing)} in settings.toml to match the list above.")
    except Exception as err:
        print(f"\nLOGIN FAILED: {type(err).__name__}: {err}")
    finally:
        await safe_logout(host)
    print("\nScreens (for popup.monitor):")
    for mon in list_monitors():
        print(f"  {mon.number}: {mon.right - mon.left}x{mon.bottom - mon.top} at ({mon.left}, {mon.top})"
              f"{'  main screen' if mon.primary else ''}")


async def run_test(s: Settings, names: list[str], kind: str, seconds: float = 15) -> None:
    password = get_password(s)
    if not password:
        print("No password saved yet. Run:  py reolink_alerts.py --set-password")
        return
    host = None
    try:
        host = await make_host(s, password)
        resolve_channels(host, s)
        await fetch_urls(host, s)
    finally:
        await safe_logout(host)
    core = AlertCore(s)
    core.mpv = find_mpv(s)
    core.sounds.mpv = core.mpv
    if not core.mpv:
        print("mpv not found. Install it or set program.mpv_path in settings.toml.")
        return
    write_input_conf(s)
    wanted = {normalize(n) for n in names}
    alerts = [a for a in core.alerts if not wanted or normalize(a.cam.name) in wanted
              or normalize(a.cam.label) in wanted]
    if not alerts:
        print(f"No camera called {', '.join(names)} in settings.toml.")
        return
    sound_task = asyncio.create_task(core.sounds.run())
    alert = alerts[0]
    path = alert.cam.sounds.get(kind)
    if path:
        core.sounds.play(path)
    else:
        print(f"{alert.cam.label} has no {kind} sound set.")
    opened = False
    if alert.cam.channel is None:
        print(f"{alert.cam.label}: not found on the NVR, so there's no pop-up to show.")
    elif alert.cam.popup:
        alert.active = {kind}
        alert.event_active = True
        opened = core.open_window(alert, 0, time.monotonic(), s.stream, test=True)
        print(f"{alert.cam.label}: {'pop-up open' if opened else 'pop-up FAILED (see the log)'}"
              f" ({s.size_name} size, {STREAMS[s.stream]} feed)")
    else:
        print(f"{alert.cam.label} is sound-only on this PC (popup = false).")
    if not opened:
        seconds = 4                                    # long enough for the sound
    end = time.monotonic() + seconds
    used = s.stream if opened else None
    while time.monotonic() < end and (core.any_open() or not opened):
        core.tick()                                    # steps down a feed if one won't play
        if alert.window is not None:
            used = alert.window.stream
        await asyncio.sleep(0.25)
    core.close_all("test finished")
    if used and used != s.stream:
        print(f"{STREAMS[s.stream]} didn't play, so it used {STREAMS[used]} (see the log).")
    await asyncio.sleep(0.5)
    sound_task.cancel()
    print("Test finished.")


def print_monitors() -> None:
    print("Screens (use the number in settings.toml, popup.monitor):")
    for mon in list_monitors():
        work = f"usable area {mon.work_right - mon.work_left}x{mon.work_bottom - mon.work_top}"
        print(f"  {mon.number}: {mon.right - mon.left}x{mon.bottom - mon.top} at ({mon.left}, {mon.top}), "
              f"{work}{'   <- main screen' if mon.primary else ''}")


# ================================================================== main ===

def setup_logging(console: bool) -> None:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(fmt)
        log.addHandler(handler)
    except OSError:
        pass
    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        log.addHandler(stream)
    logging.getLogger("reolink_aio").setLevel(logging.WARNING)


def fatal(message: str, printed: bool) -> None:
    """A startup problem: log it, print it, or show a message box when there's no console."""
    log.error("%s", message)
    if sys.stdout is None and IS_WINDOWS:                  # started with pyw.exe
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
        except Exception:
            pass
    elif not printed:
        print(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME}: pop-up and sound alerts from a Reolink NVR")
    parser.add_argument("--set-password", action="store_true", help="save the NVR password")
    parser.add_argument("--check", action="store_true", help="test the setup and print a report")
    parser.add_argument("--monitors", action="store_true", help="list screens")
    parser.add_argument("--test", nargs="+", metavar="CAMERA [TYPE]",
                        help="sound + pop-up for 15 s, e.g. --test Frontyard vehicle")
    parser.add_argument("--no-tray", action="store_true", help="run without the tray icon")
    parser.add_argument("--stream", choices=["clear", "balanced", "fluent"],
                        help="use this pop-up feed for this run only (handy with --test)")
    parser.add_argument("--size", help="use this pop-up size for this run only: normal, large or extralarge")
    parser.add_argument("--version", action="store_true")
    opts = parser.parse_args()

    if opts.version:
        print(f"{APP_NAME} {VERSION}")
        return
    console = sys.stdout is not None
    console_log = console and not (opts.check or opts.monitors or opts.set_password)
    setup_logging(console=console_log)
    set_dpi_awareness()
    if opts.monitors:
        print_monitors()
        return
    try:
        s = load_settings()
    except SettingsError as err:
        fatal(f"settings.toml: {err}", printed=console_log)
        return
    load_state(s)                                          # choices made in the tray
    if opts.stream:
        s.stream = STREAM_NAMES[opts.stream]               # this run only; not saved
    if opts.size:
        name = size_named(s, opts.size)
        if name is None:
            print(f"Size must be one of: {', '.join(s.sizes)}  (e.g. --size large, --size extralarge)")
            return
        s.size_name, s.size_percent = name, s.sizes[name]  # this run only; not saved

    if opts.set_password:
        set_password(s)
        return
    if opts.check:
        asyncio.run(run_check(s))
        return
    if opts.test:
        names, kind = [opts.test[0]], ""
        if len(opts.test) > 1:
            kind = opts.test[1].lower()
            if kind not in ALERT_TYPES:
                print(f"Type must be one of: {', '.join(ALERT_TYPES)}")
                return
        if not kind:
            cam = next((c for c in s.cameras if normalize(c.name) == normalize(names[0])
                        or normalize(c.label) == normalize(names[0])), None)
            kind = (cam.day or ["person"])[0] if cam else "person"
        asyncio.run(run_test(s, names, kind))
        return

    if not single_instance():
        log.info("Already running; this copy will exit.")
        return
    password = get_password(s)
    if not password:
        fatal("No NVR password saved yet. Run:  py reolink_alerts.py --set-password", printed=console_log)
        return

    app = App(s, password)
    log.info("%s %s starting.", APP_NAME, VERSION)
    use_tray = s.tray and not opts.no_tray
    if use_tray:
        try:                                   # a real import, so a blocked or missing library is caught
            import pystray  # noqa: F401
            from PIL import Image, ImageDraw  # noqa: F401
        except (ImportError, OSError) as err:
            log.warning("Tray icon unavailable (%s); running without it.", err)
            use_tray = False
    if use_tray:
        app.ui = TrayUI(app)
        try:
            app.ui.run()                       # blocks until Quit; starts the engine in setup
        except KeyboardInterrupt:
            app.stop()
        if app.thread:
            app.thread.join(timeout=10)
        return
    app.start_background()
    try:
        while app.thread and app.thread.is_alive():
            app.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        log.info("Stopping...")
        app.stop()
        if app.thread:
            app.thread.join(timeout=10)


if __name__ == "__main__":
    main()
