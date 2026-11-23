"""Click-through always-on-top screen targeting reticle (`ClickTargetOverlayWindow`).

Draws a razor-sharp, high-contrast crosshair + dynamic confirmation ring directly over
the exact screen pixel where a click will land, so you can visually confirm the target
BEFORE the click fires and eliminate misclicks.
"""

from __future__ import annotations

import ctypes
import platform
import time

from PySide6 import QtCore, QtGui, QtWidgets

from hand_mouse.cursor_engine import ClickFlash, HandMouseFrameState, PointerMode

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


def _apply_windows_click_through(widget: QtWidgets.QWidget) -> None:
    """Enforce Win32 WS_EX_TRANSPARENT | WS_EX_NOACTIVATE so clicks ALWAYS pass straight through."""
    if platform.system() != "Windows":
        return
    try:
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            _GWL_EXSTYLE,
            ex_style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE,
        )
    except Exception:
        pass


class ClickTargetOverlayWindow(QtWidgets.QWidget):
    """Floating 100% click-through targeting reticle centered on the exact OS click point."""

    def __init__(self, *, size_px: int = 196) -> None:
        super().__init__(None)
        self._size = max(120, int(size_px))
        self._state: HandMouseFrameState | None = None
        self._overlay_enabled: bool = True
        self._show_label: bool = True
        self._reticle_scale: float = 1.0
        self._click_flash: ClickFlash | None = None

        flags = (
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowTransparentForInput
            | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self._size, self._size)

        # Animation timer keeps click ripple smooth at 60 FPS
        self._anim_timer = QtCore.QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_timer.start()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        _apply_windows_click_through(self)

    def set_overlay_enabled(self, enabled: bool) -> None:
        self._overlay_enabled = bool(enabled)
        if not self._overlay_enabled:
            self.hide()

    def set_show_label(self, show: bool) -> None:
        self._show_label = bool(show)
        self.update()

    def set_reticle_scale(self, scale: float) -> None:
        self._reticle_scale = max(0.65, min(1.8, float(scale)))
        self.update()

    def _on_anim_tick(self) -> None:
        if self._click_flash is not None:
            age = time.monotonic() - self._click_flash.timestamp
            if age <= 0.35:
                self.update()
            else:
                self._click_flash = None
                if self._state is not None and self._state.mode == PointerMode.IDLE:
                    self.hide()
                else:
                    self.update()

    def update_state(self, state: HandMouseFrameState, *, use_qt_cursor_pos: bool = True) -> None:
        """Update targeting reticle state and lock its center onto the exact OS click pixel."""
        self._state = state
        if state.last_click is not None:
            if self._click_flash is None or state.last_click.timestamp > self._click_flash.timestamp:
                self._click_flash = state.last_click

        if not self._overlay_enabled:
            if self.isVisible():
                self.hide()
            return

        now = time.monotonic()
        has_active_flash = self._click_flash is not None and (now - self._click_flash.timestamp) <= 0.34
        if state.mode == PointerMode.IDLE and not has_active_flash:
            if self.isVisible():
                self.hide()
            return

        # QCursor.pos() returns exact DPI-mapped logical Qt coords of the physical Win32 SetCursorPos pixel!
        if use_qt_cursor_pos:
            pt = QtGui.QCursor.pos()
            lx, ly = int(pt.x()), int(pt.y())
        else:
            lx, ly = int(state.target_x), int(state.target_y)

        half = self._size // 2
        self.move(lx - half, ly - half)
        if not self.isVisible():
            self.show()
            _apply_windows_click_through(self)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        if self._state is None:
            return
        state = self._state
        mode = state.mode

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        s = self._reticle_scale

        # Color palette by pointer state
        if mode == PointerMode.LEFT_HOLD:
            accent = QtGui.QColor(34, 230, 128, 240)
            ring_r = (19.0 - 4.0 * state.confirm_progress) * s
        elif mode == PointerMode.PRE_CLICK_LOCK:
            accent = QtGui.QColor(255, 214, 51, 240)
            ring_r = (23.0 - 5.0 * state.confirm_progress) * s
        elif mode == PointerMode.RIGHT_HOLD:
            accent = QtGui.QColor(196, 118, 255, 245)
            ring_r = (21.0 - 4.0 * state.confirm_progress) * s
        elif mode == PointerMode.DRAG:
            accent = QtGui.QColor(255, 146, 43, 245)
            ring_r = 21.0 * s
        elif mode == PointerMode.SCROLL:
            accent = QtGui.QColor(56, 189, 248, 240)
            ring_r = 23.0 * s
        elif mode in (PointerMode.CLUTCH, PointerMode.PAUSED):
            accent = QtGui.QColor(156, 163, 175, 210)
            ring_r = 18.0 * s
        else:
            # AIM
            accent = QtGui.QColor(34, 211, 238, 225)
            ring_r = 22.0 * s

        # 1) Expanding click-confirmation ripple wave
        now = time.monotonic()
        if self._click_flash is not None:
            age = now - self._click_flash.timestamp
            if 0.0 <= age <= 0.34:
                k = age / 0.34
                alpha = int(235 * (1.0 - k))
                if self._click_flash.kind == "right":
                    ripple_col = QtGui.QColor(196, 118, 255, alpha)
                elif self._click_flash.kind == "double":
                    ripple_col = QtGui.QColor(255, 214, 51, alpha)
                else:
                    ripple_col = QtGui.QColor(52, 235, 138, alpha)
                ripple_r = (14.0 + 38.0 * k) * s
                painter.setPen(QtGui.QPen(ripple_col, max(1.5, 4.0 * (1.0 - k))))
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QtCore.QPointF(cx, cy), ripple_r, ripple_r)
                if self._click_flash.kind == "double":
                    inner_r = (8.0 + 26.0 * k) * s
                    painter.drawEllipse(QtCore.QPointF(cx, cy), inner_r, inner_r)

        if mode == PointerMode.IDLE:
            return

        # 2) Subtle dark shadow ring for crisp contrast on bright white AND dark backgrounds
        shadow_pen = QtGui.QPen(QtGui.QColor(10, 14, 24, 175), 4.6)
        painter.setPen(shadow_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QtCore.QPointF(cx, cy), ring_r, ring_r)

        # 3) Main outer targeting ring
        ring_pen = QtGui.QPen(accent, 2.2)
        if mode in (PointerMode.CLUTCH, PointerMode.PAUSED):
            ring_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        painter.setPen(ring_pen)
        painter.drawEllipse(QtCore.QPointF(cx, cy), ring_r, ring_r)

        # 4) Locked-target confirmation arc (fills 0 -> 360 deg while confirming LMB/RMB)
        if state.confirm_progress > 0.01 and mode in (
            PointerMode.PRE_CLICK_LOCK,
            PointerMode.LEFT_HOLD,
            PointerMode.RIGHT_HOLD,
        ):
            arc_r = (ring_r + 5.0 * s)
            arc_rect = QtCore.QRectF(cx - arc_r, cy - arc_r, arc_r * 2.0, arc_r * 2.0)
            arc_pen = QtGui.QPen(accent, 4.2, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            span = int(round(-360.0 * 16.0 * max(0.0, min(1.0, state.confirm_progress))))
            painter.drawArc(arc_rect, 90 * 16, span)

        # 5) 4 precision crosshair ticks pointing directly to the click pixel
        gap = 5.5 * s
        tick_len = 8.0 * s
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p1 = QtCore.QPointF(cx + dx * gap, cy + dy * gap)
            p2 = QtCore.QPointF(cx + dx * (gap + tick_len), cy + dy * (gap + tick_len))
            painter.setPen(QtGui.QPen(QtGui.QColor(10, 14, 24, 200), 3.6, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
            painter.drawLine(p1, p2)
            painter.setPen(QtGui.QPen(accent, 1.8, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
            painter.drawLine(p1, p2)

        # 6) Exact click pixel center dot (white core + dark outline -> impossible to miss!)
        painter.setPen(QtGui.QPen(QtGui.QColor(10, 14, 24, 230), 2.0))
        dot_col = QtGui.QColor(255, 255, 255, 250) if not state.target_locked else accent
        painter.setBrush(dot_col)
        dot_r = (3.2 if state.target_locked else 2.4) * s
        painter.drawEllipse(QtCore.QPointF(cx, cy), dot_r, dot_r)

        # 7) Scroll arrows indicator above/below ring in SCROLL mode
        if mode == PointerMode.SCROLL:
            arrow_y_up = cy - ring_r - 12.0 * s
            arrow_y_dn = cy + ring_r + 12.0 * s
            up_col = QtGui.QColor(125, 249, 255, 255) if state.scroll_direction > 0 else QtGui.QColor(56, 189, 248, 130)
            dn_col = QtGui.QColor(125, 249, 255, 255) if state.scroll_direction < 0 else QtGui.QColor(56, 189, 248, 130)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(up_col)
            painter.drawPolygon(
                QtGui.QPolygonF([
                    QtCore.QPointF(cx, arrow_y_up - 6 * s),
                    QtCore.QPointF(cx - 6 * s, arrow_y_up + 3 * s),
                    QtCore.QPointF(cx + 6 * s, arrow_y_up + 3 * s),
                ])
            )
            painter.setBrush(dn_col)
            painter.drawPolygon(
                QtGui.QPolygonF([
                    QtCore.QPointF(cx, arrow_y_dn + 6 * s),
                    QtCore.QPointF(cx - 6 * s, arrow_y_dn - 3 * s),
                    QtCore.QPointF(cx + 6 * s, arrow_y_dn - 3 * s),
                ])
            )

        # 8) Action pill badge below reticle (e.g. "🔒 ПРИЦЕЛ", "ЛКМ", "ПКМ", "ТЯНУ", "ПАУЗА")
        if self._show_label and state.pending_action:
            label = state.pending_action
            if state.target_locked and mode == PointerMode.PRE_CLICK_LOCK:
                label = "● " + label
            font = QtGui.QFont("Segoe UI", max(8, int(round(9 * s))), QtGui.QFont.Weight.Bold)
            painter.setFont(font)
            fm = QtGui.QFontMetrics(font)
            tw = fm.horizontalAdvance(label)
            th = fm.height()
            pad_x = 8
            pad_y = 3
            pill_w = tw + pad_x * 2
            pill_h = th + pad_y * 2
            pill_y = cy + ring_r + (22.0 if mode == PointerMode.SCROLL else 11.0) * s
            pill_rect = QtCore.QRectF(cx - pill_w / 2.0, pill_y, pill_w, pill_h)

            painter.setPen(QtGui.QPen(accent, 1.5))
            painter.setBrush(QtGui.QColor(15, 20, 32, 220))
            painter.drawRoundedRect(pill_rect, pill_h / 2.0, pill_h / 2.0)
            painter.setPen(QtGui.QColor(248, 250, 252, 250))
            painter.drawText(pill_rect, QtCore.Qt.AlignmentFlag.AlignCenter, label)
