"""Native Windows mouse & virtual screen backend via Win32 SendInput / SetCursorPos,
plus a deterministic VirtualPointerBackend for automated testing.

Zero third-party automation packages required (no pyautogui/pynput).
"""

from __future__ import annotations

import ctypes
import logging
import platform
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

MouseButton = Literal["left", "right", "middle"]


@dataclass(frozen=True)
class ScreenBounds:
    """Virtual desktop bounds in OS physical pixels."""

    left: int = 0
    top: int = 0
    width: int = 1920
    height: int = 1080

    @property
    def right(self) -> int:
        return self.left + self.width - 1

    @property
    def bottom(self) -> int:
        return self.top + self.height - 1

    def clamp(self, x: float, y: float) -> tuple[int, int]:
        cx = max(self.left, min(self.right, int(round(x))))
        cy = max(self.top, min(self.bottom, int(round(y))))
        return cx, cy


class OSPointerBackend(ABC):
    """Abstract OS mouse & desktop-bounds backend."""

    @abstractmethod
    def get_screen_bounds(self) -> ScreenBounds:
        """Return virtual desktop rectangle in physical OS pixels."""

    @abstractmethod
    def get_cursor_pos(self) -> tuple[int, int]:
        """Return current OS cursor position in physical pixels."""

    @abstractmethod
    def set_cursor_pos(self, x: int, y: int) -> None:
        """Move OS cursor to physical coordinates `(x, y)`."""

    @abstractmethod
    def mouse_down(self, button: MouseButton = "left") -> None:
        """Press and hold a mouse button at current cursor position."""

    @abstractmethod
    def mouse_up(self, button: MouseButton = "left") -> None:
        """Release a mouse button at current cursor position."""

    def click(self, button: MouseButton = "left", *, x: int | None = None, y: int | None = None) -> None:
        """Perform a single click, optionally locking coordinates first."""
        if x is not None and y is not None:
            self.set_cursor_pos(x, y)
        self.mouse_down(button)
        self.mouse_up(button)

    def double_click(self, button: MouseButton = "left", *, x: int | None = None, y: int | None = None) -> None:
        """Perform a double click at the exact target coordinates."""
        if x is not None and y is not None:
            self.set_cursor_pos(x, y)
        self.mouse_down(button)
        self.mouse_up(button)
        self.mouse_down(button)
        self.mouse_up(button)

    @abstractmethod
    def scroll(self, delta_notches: float, *, horizontal: bool = False) -> None:
        """Scroll vertically (positive = up, negative = down) or horizontally."""


# ---------------------------------------------------------------------------
# Win32 SendInput implementation
# ---------------------------------------------------------------------------

_INPUT_MOUSE = 0
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x1000
_WHEEL_DELTA = 120

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUT_UNION)]


def enable_windows_dpi_awareness() -> None:
    """Opt into Per-Monitor V2 DPI awareness so GetSystemMetrics / SetCursorPos use true pixels."""
    if platform.system() != "Windows":
        return
    try:
        user32 = ctypes.windll.user32
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class WindowsPointerBackend(OSPointerBackend):
    """Low-level Win32 user32 mouse controller (works across multi-monitor & elevated/normal apps)."""

    def __init__(self, *, primary_only: bool = False) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("WindowsPointerBackend requires Windows")
        enable_windows_dpi_awareness()
        self._user32 = ctypes.windll.user32
        self._primary_only = bool(primary_only)

    def get_screen_bounds(self) -> ScreenBounds:
        if self._primary_only:
            w = int(self._user32.GetSystemMetrics(0))
            h = int(self._user32.GetSystemMetrics(1))
            return ScreenBounds(left=0, top=0, width=max(320, w), height=max(240, h))
        left = int(self._user32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
        top = int(self._user32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
        width = int(self._user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
        height = int(self._user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))
        if width <= 0 or height <= 0:
            width = max(320, int(self._user32.GetSystemMetrics(0)))
            height = max(240, int(self._user32.GetSystemMetrics(1)))
            left, top = 0, 0
        return ScreenBounds(left=left, top=top, width=width, height=height)

    def get_cursor_pos(self) -> tuple[int, int]:
        pt = _POINT()
        if self._user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
        return 0, 0

    def set_cursor_pos(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(int(x), int(y))

    def _send_mouse_event(self, flags: int, mouse_data: int = 0) -> None:
        inp = _INPUT()
        inp.type = _INPUT_MOUSE
        inp.mi.dx = 0
        inp.mi.dy = 0
        inp.mi.mouseData = ctypes.c_uint32(mouse_data).value
        inp.mi.dwFlags = ctypes.c_uint32(flags).value
        inp.mi.time = 0
        inp.mi.dwExtraInfo = 0
        sent = self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent != 1:
            logger.debug("SendInput returned %s for flags=0x%x", sent, flags)

    def mouse_down(self, button: MouseButton = "left") -> None:
        flag = {
            "left": _MOUSEEVENTF_LEFTDOWN,
            "right": _MOUSEEVENTF_RIGHTDOWN,
            "middle": _MOUSEEVENTF_MIDDLEDOWN,
        }[button]
        self._send_mouse_event(flag)

    def mouse_up(self, button: MouseButton = "left") -> None:
        flag = {
            "left": _MOUSEEVENTF_LEFTUP,
            "right": _MOUSEEVENTF_RIGHTUP,
            "middle": _MOUSEEVENTF_MIDDLEUP,
        }[button]
        self._send_mouse_event(flag)

    def scroll(self, delta_notches: float, *, horizontal: bool = False) -> None:
        amount = int(round(delta_notches * _WHEEL_DELTA))
        if amount == 0:
            return
        flag = _MOUSEEVENTF_HWHEEL if horizontal else _MOUSEEVENTF_WHEEL
        self._send_mouse_event(flag, mouse_data=amount)


class VirtualPointerBackend(OSPointerBackend):
    """In-memory mock pointer backend for deterministic headless unit tests."""

    def __init__(self, bounds: ScreenBounds | None = None) -> None:
        self.bounds = bounds or ScreenBounds(0, 0, 1920, 1080)
        self.cursor_x: int = self.bounds.left + self.bounds.width // 2
        self.cursor_y: int = self.bounds.top + self.bounds.height // 2
        self.pressed: set[MouseButton] = set()
        self.events: list[tuple[str, object]] = []

    def get_screen_bounds(self) -> ScreenBounds:
        return self.bounds

    def get_cursor_pos(self) -> tuple[int, int]:
        return self.cursor_x, self.cursor_y

    def set_cursor_pos(self, x: int, y: int) -> None:
        cx, cy = self.bounds.clamp(x, y)
        self.cursor_x = cx
        self.cursor_y = cy
        self.events.append(("move", (cx, cy)))

    def mouse_down(self, button: MouseButton = "left") -> None:
        self.pressed.add(button)
        self.events.append(("down", (button, self.cursor_x, self.cursor_y)))

    def mouse_up(self, button: MouseButton = "left") -> None:
        self.pressed.discard(button)
        self.events.append(("up", (button, self.cursor_x, self.cursor_y)))

    def scroll(self, delta_notches: float, *, horizontal: bool = False) -> None:
        self.events.append(("hscroll" if horizontal else "scroll", float(delta_notches)))


def create_default_pointer_backend(*, primary_only: bool = False) -> OSPointerBackend:
    """Return WindowsPointerBackend on Windows, else VirtualPointerBackend."""
    if platform.system() == "Windows":
        return WindowsPointerBackend(primary_only=primary_only)
    return VirtualPointerBackend()
