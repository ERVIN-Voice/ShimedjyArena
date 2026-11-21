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
from typing import Callable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QAction,
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
    request_handoff = Signal()
    request_scale_delta = Signal(int)
    request_toggle_sleep = Signal()
    request_show_pairing = Signal()
    request_toggle_camera_hud = Signal()
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

        self.setWindowTitle("GCS Shimeji")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(False)
        self.setFixedSize(width, height)

    def set_context_state(self, *, is_sleeping: bool, can_handoff: bool, has_camera: bool = False) -> None:
        self._is_sleeping = is_sleeping
        self._can_handoff = can_handoff
        self._has_camera = has_camera

    def set_rendered_frame(self, frame: RenderedFrame) -> None:
        """Update displayed pixel frame and refresh the per-pixel click-through window mask."""
        frame_obj_id = id(frame)
        if frame_obj_id == self._last_frame_id:
            return
        self._last_frame_id = frame_obj_id

        if self.width() != frame.width or self.height() != frame.height:
            self.setFixedSize(frame.width, frame.height)

        self._pixmap = pil_frame_to_pixmap(frame)
        self.setMask(build_click_through_region(frame))
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
        painter.end()

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
