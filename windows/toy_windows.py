"""Crisp pixel-art PySide6 desktop toy windows, thought bubble, and companion status dialog."""

from __future__ import annotations

import ctypes
import math
import platform

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen, QPolygon
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from windows.pet_profile import PetProfile

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


def _make_click_through(widget: QWidget) -> None:
    if platform.system() != "Windows":
        return
    try:
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            _GWL_EXSTYLE,
            ex | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE,
        )
    except Exception:
        pass


_EMOTION_BORDER: dict[str, str] = {
    "neutral": "#56c6e0",
    "happy": "#ffd43b",
    "love": "#ff5c94",
    "excited": "#34eb8a",
    "scared": "#818cf8",
    "dizzy": "#c084fc",
    "angry": "#ef4444",
    "sleepy": "#93c5fd",
}


class PetThoughtBubble(QWidget):
    """Click-through floating speech/thought bubble tracking above the pet's head."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._label = QLabel("", self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(235)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 10)
        lay.addWidget(self._label)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._apply_style("neutral")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _make_click_through(self)

    def _apply_style(self, emotion: str) -> None:
        border = _EMOTION_BORDER.get(emotion, "#56c6e0")
        self._label.setStyleSheet(
            f"""
            QLabel {{
                background-color: rgba(15, 22, 36, 232);
                color: #f1f5f9;
                border: 2px solid {border};
                border-radius: 10px;
                padding: 6px 10px;
                font-family: "Segoe UI", Consolas, sans-serif;
                font-size: 12px;
                font-weight: 600;
            }}
            """
        )

    def show_message(self, text: str, *, emotion: str = "neutral", duration_ms: int = 3800) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        self._apply_style(emotion)
        self._label.setText(cleaned)
        self.adjustSize()
        if not self.isVisible():
            self.show()
            _make_click_through(self)
        self._hide_timer.start(max(1200, int(duration_ms)))

    def follow_pet(self, pet_x: float, pet_y: float, pet_w: int) -> None:
        if not self.isVisible():
            return
        bx = int(round(pet_x + pet_w / 2.0 - self.width() / 2.0))
        by = int(round(pet_y - self.height() - 4))
        if by < 8:
            by = int(round(pet_y + pet_w + 4))
        self.move(max(4, bx), by)


class BallToyWindow(QWidget):
    """Draggable bouncy pixel soccer/beach ball window for playing Fetch with the pet."""

    drag_started = Signal(float, float)
    drag_moved = Signal(float, float)
    drag_ended = Signal()
    request_close = Signal()

    def __init__(self, radius: int = 18) -> None:
        super().__init__(None)
        self._radius = max(12, int(radius))
        self._angle = 0.0
        self._dragging = False
        self._drag_offset = QPoint(0, 0)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedSize(self._radius * 2, self._radius * 2)

    def sync_position(self, x: float, y: float, angle: float) -> None:
        self._angle = angle
        ix, iy = int(round(x)), int(round(y))
        if self.x() != ix or self.y() != iy:
            self.move(ix, iy)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        r = self._radius
        # Pixel-art vibrant beach/soccer ball with rotating highlight stripe
        p.setPen(QPen(QColor("#0f172a"), 3))
        p.setBrush(QColor("#ff5c5c"))
        p.drawEllipse(2, 2, r * 2 - 4, r * 2 - 4)

        # Rotating bright yellow stripe
        cx, cy = float(r), float(r)
        sx = int(round(cx + math.cos(self._angle) * (r * 0.42)))
        sy = int(round(cy + math.sin(self._angle) * (r * 0.42)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffd43b"))
        p.drawEllipse(sx - 5, sy - 5, 10, 10)

        sx2 = int(round(cx - math.cos(self._angle) * (r * 0.42)))
        sy2 = int(round(cy - math.sin(self._angle) * (r * 0.42)))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(sx2 - 4, sy2 - 4, 8, 8)

        # Crisp white specular pixel
        p.setBrush(QColor(255, 255, 255, 210))
        p.fillRect(int(r * 0.55), int(r * 0.42), 5, 4, QColor(255, 255, 255, 210))
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            gp = event.globalPosition().toPoint()
            self.drag_started.emit(float(gp.x() - self._drag_offset.x()), float(gp.y() - self._drag_offset.y()))
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.request_close.emit()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            gp = event.globalPosition().toPoint()
            self.drag_moved.emit(float(gp.x() - self._drag_offset.x()), float(gp.y() - self._drag_offset.y()))
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.drag_ended.emit()
            event.accept()


class SnackToyWindow(QWidget):
    """Pixel-art cookie / donut / strawberry / popcorn treat dropped onto the desktop."""

    def __init__(self, size: int = 36) -> None:
        super().__init__(None)
        self._size = max(24, int(size))
        self._kind = "cookie"
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self._size, self._size)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _make_click_through(self)

    def sync_snack(self, x: float, y: float, kind: str) -> None:
        if kind != self._kind:
            self._kind = kind
            self.update()
        self.move(int(round(x)), int(round(y)))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        u = max(2, self._size // 12)
        if self._kind == "donut":
            p.setPen(QPen(QColor("#1e1b18"), 2))
            p.setBrush(QColor("#e8a85c"))
            p.drawEllipse(u, u, u * 10, u * 10)
            p.setBrush(QColor("#ff6ea8"))
            p.drawEllipse(u * 2, u * 2, u * 8, u * 7)
            p.setBrush(QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.drawEllipse(u * 4, u * 4, u * 4, u * 4)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            # Sprinkles
            p.fillRect(u * 3, u * 3, u, u, QColor("#ffd43b"))
            p.fillRect(u * 7, u * 3, u, u, QColor("#38bdf8"))
            p.fillRect(u * 8, u * 6, u, u, QColor("#ffffff"))
        elif self._kind == "strawberry":
            p.fillRect(u * 4, u * 1, u * 4, u * 2, QColor("#22c55e"))
            p.fillRect(u * 2, u * 3, u * 8, u * 5, QColor("#ef4444"))
            p.fillRect(u * 3, u * 8, u * 6, u * 2, QColor("#ef4444"))
            p.fillRect(u * 5, u * 10, u * 2, u, QColor("#dc2626"))
            for sx, sy in ((3, 4), (6, 5), (8, 4), (4, 7), (7, 7)):
                p.fillRect(u * sx, u * sy, u, u, QColor("#fde047"))
        elif self._kind == "popcorn":
            # Mini popcorn cup
            p.fillRect(u * 2, u * 5, u * 8, u * 6, QColor("#ffffff"))
            p.fillRect(u * 3, u * 5, u * 2, u * 6, QColor("#ef4444"))
            p.fillRect(u * 7, u * 5, u * 2, u * 6, QColor("#ef4444"))
            for px, py in ((2, 3), (4, 2), (6, 2), (8, 3), (5, 4)):
                p.fillRect(u * px, u * py, u * 2, u * 2, QColor("#fef08a"))
        else:
            # Classic chocolate chip cookie
            p.setPen(QPen(QColor("#3b2314"), 2))
            p.setBrush(QColor("#d99852"))
            p.drawEllipse(u, u, u * 10, u * 10)
            p.setPen(Qt.PenStyle.NoPen)
            for cx, cy in ((3, 3), (7, 4), (4, 7), (7, 7), (5, 5)):
                p.fillRect(u * cx, u * cy, u * 2 - 1, u * 2 - 1, QColor("#4a2511"))
        p.end()


class BalloonToyWindow(QWidget):
    """Clickable floating pixel balloon for the Balloon Pop mini-game."""

    clicked = Signal(int)

    def __init__(self, balloon_id: int, color_hex: str, radius: int = 22) -> None:
        super().__init__(None)
        self.balloon_id = balloon_id
        self._color = QColor(color_hex)
        self._radius = max(14, int(radius))

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self._radius * 2, self._radius * 2 + 16)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        r = self._radius
        # String
        p.setPen(QPen(QColor("#cbd5e1"), 2))
        p.drawLine(r, r * 2 - 2, r + 3, r * 2 + 7)
        p.drawLine(r + 3, r * 2 + 7, r - 2, r * 2 + 14)
        # Balloon body
        p.setPen(QPen(QColor("#0f172a"), 2))
        p.setBrush(self._color)
        p.drawEllipse(2, 2, r * 2 - 4, r * 2 - 6)
        # Knot
        p.drawPolygon(QPolygon([QPoint(r - 4, r * 2 - 2), QPoint(r + 4, r * 2 - 2), QPoint(r, r * 2 - 6)]))
        # Specular pixel shine
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(int(r * 0.55), int(r * 0.42), 6, 6, QColor(255, 255, 255, 210))
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.balloon_id)
            event.accept()


class LaserDotWindow(QWidget):
    """Click-through glowing red pixel dot following the mouse during Laser Chase game."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(26, 26)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        _make_click_through(self)

    def move_center(self, gx: int, gy: int) -> None:
        self.move(gx - 13, gy - 13)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 45, 85, 95))
        p.drawEllipse(1, 1, 24, 24)
        p.setBrush(QColor(255, 35, 65, 235))
        p.drawEllipse(6, 6, 14, 14)
        p.setBrush(QColor(255, 235, 235, 250))
        p.fillRect(11, 11, 4, 4, QColor(255, 240, 240, 250))
        p.end()


class PetStatusDialog(QDialog):
    """Cozy companion status & friendship card."""

    def __init__(self, profile: PetProfile, *, active_site: str = "Нет", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Чубрик — Профиль и достижения")
        self.setMinimumWidth(330)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #101827;
                color: #e6f4ff;
                font-family: "Segoe UI", Consolas, sans-serif;
            }
            QProgressBar {
                border: 1px solid #334155;
                border-radius: 5px;
                background: #1e293b;
                text-align: center;
                color: #f8fafc;
                height: 18px;
            }
            QProgressBar::chunk {
                border-radius: 4px;
                background-color: #38bdf8;
            }
            QPushButton {
                background-color: #223047;
                color: #e6f4ff;
                border: 1px solid #3c5478;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: bold;
            }
            """
        )

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        title = QLabel(f"⭐ Уровень {profile.level} — {profile.title}")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffd43b;")
        lay.addWidget(title)

        xp_bar = QProgressBar()
        xp_bar.setRange(0, 100)
        xp_bar.setValue(profile.xp_into_level)
        xp_bar.setFormat(f"Опыт дружбы: {profile.xp_into_level} / 100 XP")
        lay.addWidget(xp_bar)

        for label, val, color in (
            ("😊 Настроение", int(round(profile.mood)), "#34eb8a"),
            ("🍪 Сытость", int(round(profile.hunger)), "#fb923c"),
            ("⚡ Энергия", int(round(profile.energy)), "#38bdf8"),
        ):
            lay.addWidget(QLabel(f"{label}: {val}%"))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(val)
            bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}")
            lay.addWidget(bar)

        stats = QLabel(
            f"🌐 Сейчас смотрит: <b>{active_site}</b><br>"
            f"⚽ Мячей поймано: <b>{profile.balls_caught}</b><br>"
            f"🍪 Вкусняшек съедено: <b>{profile.snacks_eaten}</b><br>"
            f"🎈 Шариков лопнуто: <b>{profile.balloons_popped}</b><br>"
            f"♥ Поглаживаний: <b>{profile.pets_given}</b>"
        )
        stats.setStyleSheet("background: #162033; border: 1px solid #26354d; border-radius: 8px; padding: 8px;")
        lay.addWidget(stats)

        ok_btn = QPushButton("Закрыть")
        ok_btn.clicked.connect(self.accept)
        lay.addWidget(ok_btn)
