"""PySide6 frameless transparent always-on-top window for the pixel desktop companion.

Guarantees:
- No window frame, no background fill around the sprite.
- Strict nearest-neighbor rendering (SmoothPixmapTransform disabled).
- Per-frame opaque alpha QRegion mask so clicks outside the character pass
  straight through to the underlying desktop/windows.
- Full multi-monitor awareness using QGuiApplication.screens().
"""

from __future__ import annotations

import logging
import math
from typing import Callable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from windows.animations import RenderedFrame
from windows.pet_engine import DesktopBounds, ScreenRect

logger = logging.getLogger(__name__)


def query_qt_desktop_bounds() -> DesktopBounds:
    """Inspect all connected OS displays via Qt and return a DesktopBounds model."""
    qt_screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    rects: list[ScreenRect] = []
    for idx, scr in enumerate(qt_screens):
        geom = scr.availableGeometry()
        rects.append(
            ScreenRect(
                x=geom.x(),
                y=geom.y(),
                width=geom.width(),
                height=geom.height(),
                name=scr.name() or f"screen-{idx}",
                is_primary=(scr == primary),
            )
        )
    return DesktopBounds(rects)


def pil_frame_to_pixmap(frame: RenderedFrame) -> QPixmap:
    """Convert a PIL RGBA RenderedFrame into a crisp QPixmap without resampling."""
    rgba = frame.image.convert("RGBA")
    raw_bytes = rgba.tobytes("raw", "RGBA")
    qimage = QImage(
        raw_bytes,
        frame.width,
        frame.height,
        frame.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    # Copy ensures QPixmap owns its pixel buffer independently of temporary bytes.
    return QPixmap.fromImage(qimage.copy())


def build_click_through_region(frame: RenderedFrame) -> QRegion:
    """Build a QRegion covering only opaque sprite pixels so transparent areas pass clicks."""
    region = QRegion()
    for rx, ry, rw, rh in frame.opaque_rects:
        region = region.united(QRegion(QRect(rx, ry, rw, rh)))
    if region.isEmpty():
        # Safety fallback: 1x1 pixel so the widget mask stays valid during fade frames.
        region = QRegion(QRect(frame.width // 2, frame.height // 2, 1, 1))
    return region


class TransparentPetWindow(QWidget):
    """Frameless, click-through-outside-character window rendering the pixel pet."""

    drag_started = Signal(float, float)
    drag_moved = Signal(float, float)
    drag_ended = Signal()
    double_clicked = Signal()
    pet_hovered = Signal(float, float)
    request_handoff = Signal()
    request_scale_delta = Signal(int)
    request_toggle_sleep = Signal()
    request_pet_love = Signal()
    request_set_emotion = Signal(str)
    request_show_pairing = Signal()
    request_toggle_camera_hud = Signal()
    request_enable_camera = Signal(str)  # "webcam" | "ps3_eye"
    request_play_ball = Signal()
    request_drop_snack = Signal()
    request_toggle_laser = Signal()
    request_start_balloons = Signal()
    request_dance = Signal()
    request_toggle_site_reactions = Signal()
    request_preview_site_prop = Signal(str)
    request_show_profile = Signal()
    request_toggle_break_reminder = Signal()
    request_quit = Signal()

    def __init__(self, width: int, height: int) -> None:
        super().__init__(None)
        self._pixmap: QPixmap | None = None
        self._last_frame_id: int | None = None
        self._dragging_locally: bool = False
        self._drag_offset = QPoint(0, 0)
        self._is_sleeping: bool = False
        self._can_handoff: bool = False
        self._has_camera: bool = False
        self._emotion: str = "neutral"
        self._effect_phase: float = 0.0
        self._active_prop: str = ""
        self._site_reactions_enabled: bool = True
        self._laser_active: bool = False
        self._ball_active: bool = False
        self._balloons_active: bool = False
        self._break_reminder_enabled: bool = False
        self._base_region: QRegion = QRegion()

        self.setWindowTitle("GCS Shimeji")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setFixedSize(width, height)

    def set_context_state(
        self,
        *,
        is_sleeping: bool,
        can_handoff: bool,
        has_camera: bool = False,
        emotion: str = "neutral",
        effect_phase: float = 0.0,
        active_prop: str = "",
        site_reactions_enabled: bool = True,
        laser_active: bool = False,
        ball_active: bool = False,
        balloons_active: bool = False,
        break_reminder_enabled: bool = False,
    ) -> None:
        self._is_sleeping = is_sleeping
        self._can_handoff = can_handoff
        self._has_camera = has_camera
        self._site_reactions_enabled = site_reactions_enabled
        self._laser_active = laser_active
        self._ball_active = ball_active
        self._balloons_active = balloons_active
        self._break_reminder_enabled = break_reminder_enabled
        needs_repaint = (
            (emotion != self._emotion)
            or (emotion != "neutral")
            or (active_prop != self._active_prop)
            or bool(active_prop)
        )
        self._emotion = emotion
        self._effect_phase = effect_phase
        self._active_prop = active_prop
        if needs_repaint:
            self._apply_effective_mask()
            self.update()

    def _apply_effective_mask(self) -> None:
        if self._base_region.isEmpty():
            return
        if self._emotion == "neutral" and not self._active_prop:
            self.setMask(self._base_region)
            return

        region = QRegion(self._base_region)
        if self._emotion != "neutral" or self._active_prop in ("headphones", "idea", "chat", "sparkle"):
            # Top FX strip above head for hearts, stars, Zzz, headphones arch, lightbulb, notes
            region = region.united(QRegion(QRect(0, 0, self.width(), max(26, int(self.height() * 0.48)))))
        if self._active_prop in ("popcorn", "coffee", "gamepad", "book"):
            # Lower/front prop zone so popcorn bucket, coffee cup, gamepad, and book never clip
            prop_top = int(self.height() * 0.36)
            region = region.united(QRegion(QRect(0, prop_top, self.width(), self.height() - prop_top)))
        self.setMask(region)

    def set_rendered_frame(self, frame: RenderedFrame) -> None:
        """Update displayed pixel frame and refresh the per-pixel click-through window mask."""
        frame_obj_id = id(frame)
        if frame_obj_id == self._last_frame_id:
            return
        self._last_frame_id = frame_obj_id

        if self.width() != frame.width or self.height() != frame.height:
            self.setFixedSize(frame.width, frame.height)

        self._pixmap = pil_frame_to_pixmap(frame)
        self._base_region = build_click_through_region(frame)
        self._apply_effective_mask()
        self.update()

    def move_to(self, x: float, y: float) -> None:
        """Move window to integer desktop coordinates (works with negative multi-monitor coords)."""
        ix = int(round(x))
        iy = int(round(y))
        if self.x() != ix or self.y() != iy:
            self.move(ix, iy)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        if self._pixmap is None:
            return
        painter = QPainter(self)
        # Never smooth pixel art
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawPixmap(0, 0, self._pixmap)
        if self._emotion != "neutral":
            self._paint_pixel_emotion_fx(painter)
        if self._active_prop:
            self._paint_site_prop(painter)
        painter.end()

    def _paint_site_prop(self, painter: QPainter) -> None:
        """Draw crisp pixel-art props on the pet reacting to active websites/apps."""
        w = self.width()
        h = self.height()
        px = max(2, int(round(min(w, h) / 32.0)))
        phase = self._effect_phase
        prop = self._active_prop

        if prop == "popcorn":
            # Striped red & white popcorn bucket held in front + munching kernel hop!
            bx = int(w * 0.50 - px * 4.5)
            by = int(h * 0.58)
            bw = px * 9
            bh = px * 7
            # Dark pixel outline
            painter.fillRect(bx - px, by - px, bw + px * 2, bh + px * 2, QColor(20, 24, 34))
            # White base
            painter.fillRect(bx, by, bw, bh, QColor(248, 250, 252))
            # Classic cinema red vertical stripes
            for sx in (1, 4, 7):
                painter.fillRect(bx + px * sx, by, px * 2 - (1 if sx == 7 else 0), bh, QColor(235, 58, 68))
            # Rim shadow
            painter.fillRect(bx, by, bw, px, QColor(220, 226, 236))

            # Fluffy golden popcorn pile on top
            kernels = (
                (0, -2, QColor(255, 240, 145)),
                (2, -3, QColor(255, 224, 92)),
                (4, -2, QColor(255, 248, 185)),
                (6, -3, QColor(255, 218, 82)),
                (7, -1, QColor(255, 236, 130)),
            )
            for kx, ky, col in kernels:
                painter.fillRect(bx + px * kx, by + px * ky, px * 2, px * 2, col)

            # Animated munching kernel tossing toward pet's mouth!
            munch_cycle = (phase * 0.55) % 1.0
            if munch_cycle < 0.65:
                arc = math.sin((munch_cycle / 0.65) * math.pi)
                mx = int(w * 0.50 - px + math.sin(phase * 1.3) * px)
                my = int(by - px * 3 - arc * px * 5)
                painter.fillRect(mx - 1, my - 1, px * 2 + 2, px * 2 + 2, QColor(28, 24, 18))
                painter.fillRect(mx, my, px * 2, px * 2, QColor(255, 236, 110))
            # Subtle tiny screen-glow reflection in pet's eyes while watching video
            glow_a = int(120 + 60 * math.sin(phase * 1.8))
            painter.fillRect(px * 11, px * 12, px, px, QColor(145, 235, 255, glow_a))
            painter.fillRect(w - px * 12, px * 12, px, px, QColor(145, 235, 255, glow_a))

        elif prop == "headphones":
            # Neon pixel headphones band over head + earcups + floating musical notes
            bob = int(round(math.sin(phase * 2.2) * px * 0.6))
            band_col = QColor(42, 48, 66)
            neon = QColor(52, 235, 168)
            top_y = px * 4 + bob
            painter.fillRect(px * 7, top_y, w - px * 14, px * 2, band_col)
            painter.fillRect(px * 9, top_y, w - px * 18, px, neon)
            # Left & right earcups
            cup_y = px * 9 + bob
            painter.fillRect(px * 4, cup_y, px * 3, px * 6, band_col)
            painter.fillRect(px * 5, cup_y + px, px, px * 4, neon)
            painter.fillRect(w - px * 7, cup_y, px * 3, px * 6, band_col)
            painter.fillRect(w - px * 6, cup_y + px, px, px * 4, neon)
            # Floating notes
            font = QFont("Consolas", max(9, int(px * 2.6)), QFont.Weight.Bold)
            painter.setFont(font)
            for i, sym in enumerate(("♪", "♫")):
                t = (phase * 0.35 + i * 0.5) % 1.0
                nx = int(w * (0.22 if i == 0 else 0.74) + math.sin(phase + i * 2.0) * px * 2)
                ny = int(px * 8 - t * px * 7)
                alpha = max(60, min(255, int((1.0 - t) * 255)))
                painter.setPen(QColor(102, 252, 241, alpha) if i == 0 else QColor(255, 214, 70, alpha))
                painter.drawText(nx, ny, sym)

        elif prop == "coffee":
            # Smart coder pixel glasses + steaming coffee mug
            frame = QColor(26, 32, 48)
            lens = QColor(96, 200, 255, 130)
            gy = px * 11
            painter.fillRect(px * 8, gy, px * 6, px * 4, frame)
            painter.fillRect(w - px * 14, gy, px * 6, px * 4, frame)
            painter.fillRect(px * 14, gy + px, w - px * 28, px, frame)
            painter.fillRect(px * 9, gy + px, px * 4, px * 2, lens)
            painter.fillRect(w - px * 13, gy + px, px * 4, px * 2, lens)
            # Steaming mug on bottom-right
            mx = w - px * 10
            my = int(h * 0.62)
            painter.fillRect(mx - 1, my - 1, px * 6 + 2, px * 6 + 2, QColor(22, 24, 34))
            painter.fillRect(mx, my, px * 6, px * 6, QColor(245, 240, 232))
            painter.fillRect(mx + px, my + px, px * 4, px, QColor(115, 68, 38))
            painter.fillRect(mx + px * 6, my + px, px * 2, px * 4, QColor(245, 240, 232))
            painter.fillRect(mx + px * 6, my + px * 2, px, px * 2, QColor(22, 24, 34))
            # Rising steam pixels
            for i in range(2):
                st = (phase * 0.4 + i * 0.5) % 1.0
                sy = int(my - px * 2 - st * px * 5)
                sx = int(mx + px * (1 + i * 3) + math.sin(phase * 1.5 + i) * px)
                painter.fillRect(sx, sy, px, px * 2, QColor(235, 245, 255, int((1.0 - st) * 210)))

        elif prop == "gamepad":
            # Pixel retro controller in front with blinking ABXY buttons!
            gx = int(w * 0.50 - px * 6)
            gy = int(h * 0.60)
            gw = px * 12
            gh = px * 6
            painter.fillRect(gx - px, gy - px, gw + px * 2, gh + px * 2, QColor(16, 20, 30))
            painter.fillRect(gx, gy, gw, gh, QColor(48, 56, 78))
            # D-pad (left)
            painter.fillRect(gx + px * 2, gy + px, px, px * 3, QColor(205, 214, 230))
            painter.fillRect(gx + px, gy + px * 2, px * 3, px, QColor(205, 214, 230))
            # Blinking colored buttons (right)
            blink = int(phase * 3.0) % 2 == 0
            painter.fillRect(gx + px * 8, gy + px * 2, px, px, QColor(52, 235, 138) if blink else QColor(34, 150, 90))
            painter.fillRect(gx + px * 10, gy + px * 2, px, px, QColor(255, 82, 108) if not blink else QColor(170, 50, 68))
            painter.fillRect(gx + px * 9, gy + px, px, px, QColor(255, 214, 51))
            painter.fillRect(gx + px * 9, gy + px * 3, px, px, QColor(56, 189, 248))

        elif prop == "idea":
            # Glowing golden pixel lightbulb above head
            pulse = 0.6 + 0.4 * math.sin(phase * 2.4)
            lx = int(w * 0.50 - px * 2)
            ly = px * 2
            glow = QColor(255, 232, 92, int(200 + 55 * pulse))
            painter.fillRect(lx, ly, px * 4, px * 4, glow)
            painter.fillRect(lx + px, ly + px * 4, px * 2, px * 2, QColor(160, 174, 192))
            # Rays
            if pulse > 0.5:
                ray = QColor(255, 245, 150, 220)
                painter.fillRect(lx - px * 2, ly + px, px, px, ray)
                painter.fillRect(lx + px * 5, ly + px, px, px, ray)
                painter.fillRect(lx + px, ly - px * 2, px * 2, px, ray)

        elif prop == "chat":
            # Mini pixel chat bubble with animated typing dots
            cx = w - px * 10
            cy = px * 2
            painter.fillRect(cx - 1, cy - 1, px * 8 + 2, px * 5 + 2, QColor(20, 26, 40))
            painter.fillRect(cx, cy, px * 8, px * 5, QColor(240, 248, 255))
            active_dot = int(phase * 2.2) % 3
            for i in range(3):
                col = QColor(56, 189, 248) if i == active_dot else QColor(148, 163, 184)
                painter.fillRect(cx + px * (1 + i * 2), cy + px * 2, px, px, col)

        elif prop == "book":
            # Open pixel book in front
            bx = int(w * 0.50 - px * 6)
            by = int(h * 0.62)
            painter.fillRect(bx - px, by - px, px * 14, px * 7, QColor(36, 48, 72))
            painter.fillRect(bx, by, px * 5, px * 5, QColor(250, 248, 235))
            painter.fillRect(bx + px * 7, by, px * 5, px * 5, QColor(250, 248, 235))
            painter.fillRect(bx + px * 5, by, px * 2, px * 5, QColor(148, 163, 184))
            for ly in (1, 3):
                painter.fillRect(bx + px, by + px * ly, px * 3, max(1, px // 2), QColor(100, 116, 139))
                painter.fillRect(bx + px * 8, by + px * ly, px * 3, max(1, px // 2), QColor(100, 116, 139))

        elif prop == "sparkle":
            # Twinkling golden shopping stars around the pet
            for i in range(4):
                ang = phase * 0.9 + i * (math.pi / 2.0)
                sx = int(w * 0.5 + math.cos(ang) * w * 0.36)
                sy = int(px * 6 + math.sin(ang) * px * 3.0)
                col = QColor(255, 214, 51) if i % 2 == 0 else QColor(255, 118, 178)
                painter.fillRect(sx - px, sy, px * 2 + 1, px, col)
                painter.fillRect(sx, sy - px, px, px * 2 + 1, col)

    def _paint_pixel_emotion_fx(self, painter: QPainter) -> None:
        w = self.width()
        h = self.height()
        px = max(2, int(round(min(w, h) / 32.0)))
        phase = self._effect_phase
        emo = self._emotion

        if emo in ("love", "happy"):
            # Rosy pixel blush cheeks
            blush = QColor(255, 105, 165, 200) if emo == "love" else QColor(255, 130, 175, 145)
            painter.fillRect(px * 8, px * 15, px * 3, px * 2, blush)
            painter.fillRect(w - px * 11, px * 15, px * 3, px * 2, blush)

        if emo == "love":
            u = max(2, int(px * 0.85))
            for i in range(2):
                t = (phase * 0.35 + i * 0.5) % 1.0
                hx = int(w * 0.5 + (-1 if i == 0 else 1) * (px * 6 + math.sin(phase + i * 2.0) * px * 2))
                hy = int(px * 8 - t * px * 6)
                alpha = max(60, min(255, int((1.0 - t) * 255)))
                col = QColor(255, 92, 148, alpha) if i == 0 else QColor(255, 145, 196, alpha)
                painter.fillRect(hx - 2 * u, hy - u, u, u, col)
                painter.fillRect(hx + u, hy - u, u, u, col)
                painter.fillRect(hx - 2 * u, hy, 5 * u, u, col)
                painter.fillRect(hx - u, hy + u, 3 * u, u, col)
                painter.fillRect(hx, hy + 2 * u, u, u, col)
        elif emo in ("happy", "excited"):
            colors = (QColor(255, 232, 102), QColor(102, 252, 241))
            for i in range(3):
                pulse = 0.5 + 0.5 * math.sin(phase * 1.4 + i * 2.1)
                sx = int(w * (0.2 + 0.3 * i))
                sy = int(px * 4 + math.sin(i * 1.8) * px * 2)
                s = max(2, int(px * (0.7 + pulse * 0.6)))
                col = colors[i % 2]
                painter.fillRect(sx - s, sy, s * 2 + 1, max(2, s // 2), col)
                painter.fillRect(sx, sy - s, max(2, s // 2), s * 2 + 1, col)
        elif emo == "scared":
            slide = int(((phase * 0.4) % 1.0) * px * 4)
            sx = int(w * 0.78)
            sy = px * 4 + slide
            painter.fillRect(sx, sy, px, px * 3, QColor(92, 200, 255))
            painter.fillRect(sx - px // 2, sy + px, px * 2, px * 2, QColor(92, 200, 255))
        elif emo == "dizzy":
            cols = (QColor(255, 224, 92), QColor(102, 252, 241), QColor(255, 168, 92))
            for i in range(3):
                ang = phase * 1.2 + i * (math.pi * 2.0 / 3.0)
                sx = int(w * 0.5 + math.cos(ang) * w * 0.30)
                sy = int(px * 5 + math.sin(ang) * px * 2.2)
                painter.fillRect(sx - px, sy, px * 2 + 1, px, cols[i])
                painter.fillRect(sx, sy - px, px, px * 2 + 1, cols[i])
        elif emo == "angry":
            ax = int(w * 0.76)
            ay = px * 6
            u = max(2, int(px * (0.85 + 0.2 * math.sin(phase * 2.2))))
            col = QColor(255, 68, 68)
            painter.fillRect(ax - 2 * u, ay - u, u, u, col)
            painter.fillRect(ax + u, ay - u, u, u, col)
            painter.fillRect(ax - 2 * u, ay + u, u, u, col)
            painter.fillRect(ax + u, ay + u, u, u, col)
        elif emo == "sleepy":
            t1 = (phase * 0.22) % 1.0
            font = QFont("Consolas", max(8, int(px * 2.4)), QFont.Weight.Bold)
            painter.setFont(font)
            painter.setPen(QColor(154, 212, 255, max(50, int((1.0 - t1) * 255))))
            painter.drawText(int(w * 0.62 + t1 * px * 4), int(px * 9 - t1 * px * 6), "Z")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging_locally = True
            self._drag_offset = event.position().toPoint()
            global_pt = event.globalPosition().toPoint()
            target_x = float(global_pt.x() - self._drag_offset.x())
            target_y = float(global_pt.y() - self._drag_offset.y())
            self.drag_started.emit(target_x, target_y)
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging_locally and (event.buttons() & Qt.MouseButton.LeftButton):
            global_pt = event.globalPosition().toPoint()
            target_x = float(global_pt.x() - self._drag_offset.x())
            target_y = float(global_pt.y() - self._drag_offset.y())
            self.drag_moved.emit(target_x, target_y)
            event.accept()
        else:
            pos = event.position()
            self.pet_hovered.emit(float(pos.x()), float(pos.y()))
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging_locally:
            self._dragging_locally = False
            self.drag_ended.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)

        handoff_action = QAction("Send to Android Device", menu)
        handoff_action.setEnabled(self._can_handoff)
        handoff_action.triggered.connect(self.request_handoff.emit)
        menu.addAction(handoff_action)

        love_action = QAction("Погладить ♥ (Pet with Love)", menu)
        love_action.triggered.connect(self.request_pet_love.emit)
        menu.addAction(love_action)

        # ---- Play & Games submenu ----
        games_menu = menu.addMenu("🎮 Игры с Чубриком")
        ball_lbl = "⚽ Убрать мячик" if self._ball_active else "⚽ Бросить мячик (Апорт!)"
        act_ball = QAction(ball_lbl, games_menu)
        act_ball.triggered.connect(self.request_play_ball.emit)
        games_menu.addAction(act_ball)

        act_snack = QAction("🍪 Бросить вкусняшку", games_menu)
        act_snack.triggered.connect(self.request_drop_snack.emit)
        games_menu.addAction(act_snack)

        laser_lbl = "🔴 Выключить лазер" if self._laser_active else "🔴 Лазерная указка (Погоня!)"
        act_laser = QAction(laser_lbl, games_menu)
        act_laser.triggered.connect(self.request_toggle_laser.emit)
        games_menu.addAction(act_laser)

        balloon_lbl = "🎈 Стоп: Лопать шарики" if self._balloons_active else "🎈 Лопать шарики (25 сек)"
        act_balloons = QAction(balloon_lbl, games_menu)
        act_balloons.triggered.connect(self.request_start_balloons.emit)
        games_menu.addAction(act_balloons)

        act_dance = QAction("💃 Станцевать!", games_menu)
        act_dance.triggered.connect(self.request_dance.emit)
        games_menu.addAction(act_dance)

        # ---- Website Reactions submenu ----
        site_menu = menu.addMenu("🌐 Реакции на сайты")
        act_site_toggle = QAction("Реагировать на открытые сайты", site_menu)
        act_site_toggle.setCheckable(True)
        act_site_toggle.setChecked(self._site_reactions_enabled)
        act_site_toggle.triggered.connect(self.request_toggle_site_reactions.emit)
        site_menu.addAction(act_site_toggle)
        site_menu.addSeparator()
        for act_id, lbl in (
            ("video_popcorn", "🍿 Тест: YouTube (Попкорн)"),
            ("music_headphones", "🎧 Тест: Музыка (Наушники)"),
            ("coding_coffee", "☕ Тест: Код (Очки и кофе)"),
            ("gaming_gamepad", "🎮 Тест: Игры (Геймпад)"),
            ("ai_idea", "💡 Тест: Идея (Лампочка)"),
            ("none", "✖ Убрать пропс"),
        ):
            a_prev = QAction(lbl, site_menu)
            a_prev.triggered.connect(lambda _c=False, a=act_id: self.request_preview_site_prop.emit(a))
            site_menu.addAction(a_prev)

        act_profile = QAction("⭐ Профиль и настроение Чубрика", menu)
        act_profile.triggered.connect(self.request_show_profile.emit)
        menu.addAction(act_profile)

        act_break = QAction("💧 Напоминание размяться (45 мин)", menu)
        act_break.setCheckable(True)
        act_break.setChecked(self._break_reminder_enabled)
        act_break.triggered.connect(self.request_toggle_break_reminder.emit)
        menu.addAction(act_break)

        menu.addSeparator()

        emo_menu = menu.addMenu("Emotion & Color")
        for emo_id, emo_label in (
            ("neutral", "Neutral (Cyan)"),
            ("happy", "Happy (Gold Yellow)"),
            ("love", "Love (Pink ♥)"),
            ("excited", "Excited (Mint Green)"),
            ("scared", "Scared (Ice Indigo)"),
            ("dizzy", "Dizzy (Purple)"),
            ("angry", "Angry (Crimson Red)"),
            ("sleepy", "Sleepy (Lavender Zzz)"),
        ):
            act = QAction(emo_label, emo_menu)
            act.triggered.connect(lambda _checked=False, e=emo_id: self.request_set_emotion.emit(e))
            emo_menu.addAction(act)

        sleep_label = "Wake Up" if self._is_sleeping else "Sleep"
        sleep_action = QAction(sleep_label, menu)
        sleep_action.triggered.connect(self.request_toggle_sleep.emit)
        menu.addAction(sleep_action)

        menu.addSeparator()

        scale_up = QAction("Scale Up (+1x)", menu)
        scale_up.triggered.connect(lambda: self.request_scale_delta.emit(1))
        menu.addAction(scale_up)

        scale_down = QAction("Scale Down (-1x)", menu)
        scale_down.triggered.connect(lambda: self.request_scale_delta.emit(-1))
        menu.addAction(scale_down)

        menu.addSeparator()

        qr_action = QAction("Show Pairing Info / QR", menu)
        qr_action.triggered.connect(self.request_show_pairing.emit)
        menu.addAction(qr_action)

        if self._has_camera:
            cam_action = QAction("Camera & Gestures HUD", menu)
            cam_action.triggered.connect(self.request_toggle_camera_hud.emit)
            menu.addAction(cam_action)
        else:
            cam_webcam = QAction("Enable Camera Gestures (Webcam)", menu)
            cam_webcam.triggered.connect(lambda: self.request_enable_camera.emit("webcam"))
            menu.addAction(cam_webcam)

            cam_ps3 = QAction("Enable Camera Gestures (PS3 Eye)", menu)
            cam_ps3.triggered.connect(lambda: self.request_enable_camera.emit("ps3_eye"))
            menu.addAction(cam_ps3)

        quit_action = QAction("Quit GCS Shimeji", menu)
        quit_action.triggered.connect(self.request_quit.emit)
        menu.addAction(quit_action)

        menu.exec(global_pos)


class HandoffPromptOverlay(QWidget):
    """Non-intrusive always-on-top prompt shown when another device offers a handoff."""

    def __init__(
        self,
        on_accept: Callable[[str, str], None],
        on_reject: Callable[[str], None],
    ) -> None:
        super().__init__(None)
        self._on_accept = on_accept
        self._on_reject = on_reject
        self._active_handoff_id: str | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(
            """
            QWidget {
                background-color: #161b26;
                color: #e6f4ff;
                border: 2px solid #56c6e0;
                border-radius: 10px;
                font-family: Consolas, monospace;
            }
            QLabel {
                border: none;
                padding: 2px;
            }
            QPushButton#acceptBtn {
                background-color: #2ecc71;
                color: #0b141a;
                font-weight: bold;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton#acceptBtn:hover {
                background-color: #46e088;
            }
            QPushButton#rejectBtn {
                background-color: #3a4559;
                color: #e6f4ff;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
            }
            """
        )

        layout = QVBoxLayout(self)
        self._title = QLabel("Incoming Pet Handoff")
        self._title.setStyleSheet("font-size: 13px; font-weight: bold; color: #66fcf1;")
        self._subtitle = QLabel("Click Receive or show open palm gesture")
        self._subtitle.setStyleSheet("font-size: 11px; color: #b8c9dc;")

        btn_row = QHBoxLayout()
        accept_btn = QPushButton("Receive Pet")
        accept_btn.setObjectName("acceptBtn")
        accept_btn.clicked.connect(self._handle_accept)

        reject_btn = QPushButton("Decline")
        reject_btn.setObjectName("rejectBtn")
        reject_btn.clicked.connect(self._handle_reject)

        btn_row.addWidget(accept_btn)
        btn_row.addWidget(reject_btn)

        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        layout.addLayout(btn_row)
        self.adjustSize()

    def show_offer(self, handoff_id: str, source_name: str, edge: str, timeout_sec: float) -> None:
        self._active_handoff_id = handoff_id
        self._title.setText(f"Incoming Pet from {source_name}")
        self._subtitle.setText(f"Accept within {timeout_sec:.0f}s (edge: {edge})")
        self.adjustSize()

        bounds = query_qt_desktop_bounds()
        primary = bounds.primary_screen
        margin = 24
        if edge == "right":
            px = primary.left + margin
        elif edge == "left":
            px = primary.right - self.width() - margin
        else:
            px = primary.left + (primary.width - self.width()) // 2
        py = primary.top + margin
        self.move(px, py)
        self.show()
        self.raise_()

    def hide_offer(self) -> None:
        self._active_handoff_id = None
        self.hide()

    def _handle_accept(self) -> None:
        if self._active_handoff_id:
            hid = self._active_handoff_id
            self.hide_offer()
            self._on_accept(hid, "click")

    def _handle_reject(self) -> None:
        if self._active_handoff_id:
            hid = self._active_handoff_id
            self.hide_offer()
            self._on_reject(hid)
