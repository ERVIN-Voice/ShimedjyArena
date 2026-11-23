"""Main control panel, camera loop, system tray, and F8 global pause hotkey
for HandMouse PC Control.

Reuses the exact 21-landmark MediaPipe + OpenCV hand tracking pipeline from GCS Shimeji
(`windows/camera.py` and `windows/hand_detection.py`).
"""

from __future__ import annotations

import ctypes
import logging
import platform
import time
from typing import Literal

import cv2
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from hand_mouse.cursor_engine import (
    HandMouseConfig,
    HandMouseController,
    HandMouseFrameState,
    PointerMode,
)
from hand_mouse.target_overlay import ClickTargetOverlayWindow
from hand_mouse.win_input import create_default_pointer_backend
from windows.camera import CameraFrame, ThreadedCameraReader, create_camera, probe_cameras
from windows.camera_preview import bgr_to_qpixmap
from windows.hand_detection import (
    GestureRecognizer,
    OpenCVHandDetector,
    create_best_hand_detector,
    draw_hand_overlay,
    get_last_mediapipe_status,
)

logger = logging.getLogger(__name__)

_VK_F8 = 0x77


def _is_f8_pressed_windows() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(_VK_F8) & 0x8000)
    except Exception:
        return False


def _make_tray_icon() -> QtGui.QIcon:
    pix = QtGui.QPixmap(32, 32)
    pix.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setPen(QtGui.QPen(QtGui.QColor("#22d3ee"), 2.5))
    p.setBrush(QtGui.QColor("#0f172a"))
    p.drawEllipse(3, 3, 26, 26)
    p.setPen(QtGui.QPen(QtGui.QColor("#34eb8a"), 2.0))
    p.drawLine(16, 7, 16, 12)
    p.drawLine(16, 20, 16, 25)
    p.drawLine(7, 16, 12, 16)
    p.drawLine(20, 16, 25, 16)
    p.setBrush(QtGui.QColor("#ffffff"))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.drawEllipse(14, 14, 4, 4)
    p.end()
    return QtGui.QIcon(pix)


class HandMouseMainWindow(QtWidgets.QWidget):
    """Control panel + real-time hand gesture loop + click-through screen reticle."""

    def __init__(
        self,
        *,
        camera_kind: Literal["webcam", "ps3_eye"] = "webcam",
        camera_index: int = 0,
        start_enabled: bool = True,
        minimized_to_tray: bool = False,
        primary_monitor_only: bool = False,
        auto_download_model: bool = True,
    ) -> None:
        super().__init__(None)
        self._camera_kind: Literal["webcam", "ps3_eye"] = camera_kind
        self._camera_index: int = max(0, int(camera_index))
        self._mirror_preview_and_input: bool = True
        self._show_mask: bool = False
        self._last_frame_seq: int = 0
        self._f8_was_down: bool = False
        self._quitting: bool = False

        # Core pointer backend & anti-misclick engine (mirror handled cleanly on camera frame)
        self._pointer = create_default_pointer_backend(primary_only=primary_monitor_only)
        self._config = HandMouseConfig(
            enabled=start_enabled,
            mirror_x=False,  # Frame itself is flipped when mirror is ON so HUD text stays readable!
        )
        self._controller = HandMouseController(self._pointer, self._config)

        # Reuse exact Shimeji hand detector (MediaPipe 21-pt AI + OpenCV fallback)
        self._detector = create_best_hand_detector(
            prefer_mediapipe=True,
            auto_download_model=auto_download_model,
        )
        self._tracker_status = get_last_mediapipe_status() or self._detector.name
        self._recognizer = GestureRecognizer(
            detector=self._detector,
            hold_frames=2,
            pinch_ratio_threshold=0.40,
            pinch_release_threshold=0.52,
            miss_tolerance=1,
        )
        self._reader: ThreadedCameraReader | None = None

        # On-screen targeting reticle overlay
        self._overlay = ClickTargetOverlayWindow(size_px=196)

        self._build_ui()
        self._setup_tray()
        self._start_camera(self._camera_kind, self._camera_index)

        # 60 Hz camera processing + overlay update timer
        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        if minimized_to_tray and self._tray is not None:
            self.hide()
        else:
            self.show()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("HandMouse PC Control — Управление ПК жестами (без мискликов)")
        self.setWindowIcon(_make_tray_icon())
        self.resize(860, 620)
        self.setStyleSheet(
            """
            QWidget {
                background-color: #0e1420;
                color: #e6f4ff;
                font-family: "Segoe UI", Consolas, sans-serif;
                font-size: 12px;
            }
            QGroupBox {
                border: 1px solid #26354d;
                border-radius: 8px;
                margin-top: 10px;
                padding: 10px 10px 8px 10px;
                font-weight: bold;
                color: #93c5fd;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QPushButton {
                background-color: #1e293b;
                color: #e6f4ff;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2c3e5a;
            }
            QPushButton#startBtn {
                background-color: #16a34a;
                border-color: #22c55e;
                color: #ffffff;
                font-size: 13px;
                padding: 9px 16px;
            }
            QPushButton#pauseBtn {
                background-color: #d97706;
                border-color: #f59e0b;
                color: #ffffff;
                font-size: 13px;
                padding: 9px 16px;
            }
            QComboBox, QSpinBox {
                background-color: #162033;
                border: 1px solid #334155;
                border-radius: 5px;
                padding: 4px 8px;
                color: #e6f4ff;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #1e293b;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #22d3ee;
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QLabel#statusPill {
                background-color: #162033;
                border: 1px solid #26354d;
                border-radius: 6px;
                padding: 6px 10px;
            }
            """
        )

        root = QHBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(12, 12, 12, 12)

        # ---- Left column: Live Camera + Gesture Guide ----
        left_col = QVBoxLayout()
        left_col.setSpacing(8)

        self._preview_label = QtWidgets.QLabel("Подключение камеры...")
        self._preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(460, 345)
        self._preview_label.setStyleSheet(
            "background-color: #070b12; border: 1px solid #26354d; border-radius: 8px; color: #94a3b8;"
        )
        left_col.addWidget(self._preview_label, stretch=3)

        self._status_label = QtWidgets.QLabel(f"Трекер: {self._tracker_status}")
        self._status_label.setObjectName("statusPill")
        left_col.addWidget(self._status_label)

        guide_box = QtWidgets.QGroupBox("Жесты управления ПК (как у Чубрика — 21 точка)")
        guide_lay = QVBoxLayout(guide_box)
        guide_lay.setSpacing(4)
        guide_lines = [
            "☝️ <b>1 палец (Point)</b> — плавное наведение курсора + экранный прицел",
            "🤏 <b>Щипок (Pinch)</b> — заморозка точки + <b>ЛКМ</b> (2 щипка = <b>2×ЛКМ</b>, удержать и тянуть = <b>Drag</b>)",
            "✌️ <b>2 пальца (Peace)</b> — заморозка точки + удержание 0.3с = <b>ПКМ</b> (без случайных нажатий!)",
            "🖐️ <b>Ладонь (5 пальцев)</b> — плавный <b>Скролл</b> вверх / вниз движением руки",
            "✊ <b>Кулак (Fist)</b> — <b>Пауза / Сцепление</b> (курсор замирает, чтобы удобно переставить руку)",
            "⌨️ <b>Клавиша F8</b> — мгновенная Пауза / Старт из любого окна",
        ]
        for line in guide_lines:
            lbl = QtWidgets.QLabel(line)
            lbl.setWordWrap(True)
            guide_lay.addWidget(lbl)
        left_col.addWidget(guide_box)

        root.addLayout(left_col, stretch=3)

        # ---- Right column: Controls & Anti-Misclick Settings ----
        right_col = QVBoxLayout()
        right_col.setSpacing(8)

        # Main Enable/Pause button
        self._toggle_btn = QtWidgets.QPushButton()
        self._toggle_btn.clicked.connect(self._toggle_enabled)
        right_col.addWidget(self._toggle_btn)
        self._refresh_toggle_button()

        # Camera group
        cam_box = QtWidgets.QGroupBox("Камера")
        cam_lay = QVBoxLayout(cam_box)
        row_cam = QHBoxLayout()
        self._cam_kind_combo = QtWidgets.QComboBox()
        self._cam_kind_combo.addItem("Веб-камера (RGB)", "webcam")
        self._cam_kind_combo.addItem("PS3 Eye (60 FPS)", "ps3_eye")
        self._cam_kind_combo.setCurrentIndex(1 if self._camera_kind == "ps3_eye" else 0)
        row_cam.addWidget(self._cam_kind_combo, stretch=2)

        self._cam_index_spin = QtWidgets.QSpinBox()
        self._cam_index_spin.setRange(0, 8)
        self._cam_index_spin.setPrefix("#")
        self._cam_index_spin.setValue(self._camera_index)
        row_cam.addWidget(self._cam_index_spin, stretch=1)

        apply_cam_btn = QtWidgets.QPushButton("Подключить")
        apply_cam_btn.clicked.connect(self._on_apply_camera)
        row_cam.addWidget(apply_cam_btn)
        cam_lay.addLayout(row_cam)

        row_cam2 = QHBoxLayout()
        probe_btn = QtWidgets.QPushButton("Найти камеры")
        probe_btn.clicked.connect(self._on_probe_cameras)
        row_cam2.addWidget(probe_btn)

        self._mirror_chk = QtWidgets.QCheckBox("Зеркало")
        self._mirror_chk.setChecked(self._mirror_preview_and_input)
        self._mirror_chk.toggled.connect(self._on_mirror_toggled)
        row_cam2.addWidget(self._mirror_chk)

        if isinstance(self._detector, OpenCVHandDetector):
            cal_btn = QtWidgets.QPushButton("Калибр. фон")
            cal_btn.clicked.connect(self._on_calibrate_bg)
            row_cam2.addWidget(cal_btn)
        cam_lay.addLayout(row_cam2)
        right_col.addWidget(cam_box)

        # Anti-misclick & Precision group
        prec_box = QtWidgets.QGroupBox("Точность и защита от мискликов")
        prec_lay = QVBoxLayout(prec_box)

        # Aim point mode
        row_aim = QHBoxLayout()
        row_aim.addWidget(QtWidgets.QLabel("Точка наведения:"))
        self._aim_combo = QtWidgets.QComboBox()
        self._aim_combo.addItem("Якорь ладони (не съезжает при щипке!)", "anchor")
        self._aim_combo.addItem("Кончик указательного пальца", "index_tip")
        self._aim_combo.currentIndexChanged.connect(self._on_aim_mode_changed)
        row_aim.addWidget(self._aim_combo, stretch=1)
        prec_lay.addLayout(row_aim)

        # Freeze on pre-pinch
        self._freeze_chk = QtWidgets.QCheckBox("Замораживать точку при сближении пальцев (анти-мисклик)")
        self._freeze_chk.setChecked(self._config.freeze_on_pre_pinch)
        self._freeze_chk.toggled.connect(lambda v: setattr(self._config, "freeze_on_pre_pinch", bool(v)))
        prec_lay.addWidget(self._freeze_chk)

        # LMB confirm hold time slider (0..300 ms)
        self._confirm_lbl = QtWidgets.QLabel()
        prec_lay.addWidget(self._confirm_lbl)
        self._confirm_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._confirm_slider.setRange(0, 300)
        self._confirm_slider.setSingleStep(10)
        self._confirm_slider.setValue(int(round(self._config.left_click_confirm_sec * 1000)))
        self._confirm_slider.valueChanged.connect(self._on_confirm_changed)
        prec_lay.addWidget(self._confirm_slider)
        self._on_confirm_changed(self._confirm_slider.value())

        # Anti-jitter deadzone slider (1..12 px)
        self._smooth_lbl = QtWidgets.QLabel()
        prec_lay.addWidget(self._smooth_lbl)
        self._smooth_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._smooth_slider.setRange(1, 12)
        self._smooth_slider.setValue(int(round(self._config.deadzone_px)))
        self._smooth_slider.valueChanged.connect(self._on_smooth_changed)
        prec_lay.addWidget(self._smooth_slider)
        self._on_smooth_changed(self._smooth_slider.value())

        # Active zone margin slider (reach corners easily)
        self._zone_lbl = QtWidgets.QLabel()
        prec_lay.addWidget(self._zone_lbl)
        self._zone_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._zone_slider.setRange(6, 30)
        self._zone_slider.setValue(int(round(self._config.active_zone_x0 * 100)))
        self._zone_slider.valueChanged.connect(self._on_zone_changed)
        prec_lay.addWidget(self._zone_slider)
        self._on_zone_changed(self._zone_slider.value())

        right_col.addWidget(prec_box)

        # Behavior & Reticle Overlay group
        ov_box = QtWidgets.QGroupBox("Экранный прицел и поведение")
        ov_lay = QVBoxLayout(ov_box)

        self._overlay_chk = QtWidgets.QCheckBox("Показывать прицел поверх всех окон (куда нажму)")
        self._overlay_chk.setChecked(True)
        self._overlay_chk.toggled.connect(self._overlay.set_overlay_enabled)
        ov_lay.addWidget(self._overlay_chk)

        self._labels_chk = QtWidgets.QCheckBox("Подписи действий на прицеле (ЛКМ / ПКМ / ТЯНУ / СКРОЛЛ)")
        self._labels_chk.setChecked(True)
        self._labels_chk.toggled.connect(self._overlay.set_show_label)
        ov_lay.addWidget(self._labels_chk)

        self._reticle_size_lbl = QtWidgets.QLabel("Размер прицела: 100%")
        ov_lay.addWidget(self._reticle_size_lbl)
        self._reticle_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._reticle_slider.setRange(70, 160)
        self._reticle_slider.setValue(100)
        self._reticle_slider.valueChanged.connect(self._on_reticle_scale_changed)
        ov_lay.addWidget(self._reticle_slider)

        self._scroll_chk = QtWidgets.QCheckBox("Открытая ладонь (5 пальцев) = плавный скролл")
        self._scroll_chk.setChecked(self._config.palm_scroll_enabled)
        self._scroll_chk.toggled.connect(lambda v: setattr(self._config, "palm_scroll_enabled", bool(v)))
        ov_lay.addWidget(self._scroll_chk)

        self._yield_chk = QtWidgets.QCheckBox("Уступать обычной мышке (если двинул мышь)")
        self._yield_chk.setChecked(self._config.yield_to_physical_mouse)
        self._yield_chk.toggled.connect(lambda v: setattr(self._config, "yield_to_physical_mouse", bool(v)))
        ov_lay.addWidget(self._yield_chk)

        right_col.addWidget(ov_box)

        # Bottom utility row
        bottom_row = QHBoxLayout()
        hide_btn = QtWidgets.QPushButton("Свернуть в трей")
        hide_btn.clicked.connect(self.hide)
        bottom_row.addWidget(hide_btn)

        quit_btn = QtWidgets.QPushButton("Выход")
        quit_btn.clicked.connect(self.quit_app)
        bottom_row.addWidget(quit_btn)
        right_col.addLayout(bottom_row)
        right_col.addStretch(1)

        root.addLayout(right_col, stretch=2)

    def _setup_tray(self) -> None:
        self._tray: QtWidgets.QSystemTrayIcon | None = None
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QtWidgets.QSystemTrayIcon(_make_tray_icon(), self)
        tray.setToolTip("HandMouse PC Control (F8 — Пауза/Старт)")
        menu = QtWidgets.QMenu()

        act_show = menu.addAction("Открыть панель")
        act_show.triggered.connect(self._show_and_raise)

        act_toggle = menu.addAction("Пауза / Старт управления (F8)")
        act_toggle.triggered.connect(self._toggle_enabled)

        act_reticle = menu.addAction("Вкл/Выкл прицел на экране")
        act_reticle.triggered.connect(lambda: self._overlay_chk.setChecked(not self._overlay_chk.isChecked()))

        menu.addSeparator()
        act_quit = menu.addAction("Выход")
        act_quit.triggered.connect(self.quit_app)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray = tray

    def _show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible():
                self.hide()
            else:
                self._show_and_raise()

    # ------------------------------------------------------------------
    # Settings callbacks
    # ------------------------------------------------------------------

    def _refresh_toggle_button(self) -> None:
        if self._config.enabled:
            self._toggle_btn.setObjectName("pauseBtn")
            self._toggle_btn.setText("⏸ Пауза управления ПК (F8)")
        else:
            self._toggle_btn.setObjectName("startBtn")
            self._toggle_btn.setText("▶ Включить управление ПК жестами (F8)")
        self._toggle_btn.style().unpolish(self._toggle_btn)
        self._toggle_btn.style().polish(self._toggle_btn)

    def _toggle_enabled(self) -> None:
        self._config.enabled = not self._config.enabled
        if not self._config.enabled:
            self._controller.release_all()
        self._refresh_toggle_button()

    def _on_mirror_toggled(self, checked: bool) -> None:
        self._mirror_preview_and_input = bool(checked)
        self._recognizer.reset()

    def _on_aim_mode_changed(self) -> None:
        data = self._aim_combo.currentData()
        if data in ("anchor", "index_tip"):
            self._config.aim_source = data
            self._controller.apply_filter_settings()

    def _on_confirm_changed(self, ms: int) -> None:
        self._config.left_click_confirm_sec = ms / 1000.0
        self._confirm_lbl.setText(f"Подтверждение ЛКМ (защита от мисклика): {ms} мс")

    def _on_smooth_changed(self, px: int) -> None:
        self._config.deadzone_px = float(px)
        # Higher deadzone also lowers 1-Euro min_cutoff slightly for rock-steady hovering
        self._config.filter_min_cutoff = max(0.65, 1.75 - px * 0.09)
        self._controller.apply_filter_settings()
        self._smooth_lbl.setText(f"Стабилизация / анти-дрожание: {px} px")

    def _on_zone_changed(self, pct: int) -> None:
        m = pct / 100.0
        self._config.active_zone_x0 = m
        self._config.active_zone_x1 = 1.0 - m
        self._config.active_zone_y0 = m
        self._config.active_zone_y1 = max(m + 0.25, 0.98 - m)
        self._zone_lbl.setText(f"Рабочая зона камеры (отступ краёв): {pct}% (легче дотянуться до углов)")

    def _on_reticle_scale_changed(self, pct: int) -> None:
        self._reticle_size_lbl.setText(f"Размер прицела: {pct}%")
        self._overlay.set_reticle_scale(pct / 100.0)

    def _on_apply_camera(self) -> None:
        kind = self._cam_kind_combo.currentData()
        idx = int(self._cam_index_spin.value())
        if kind in ("webcam", "ps3_eye"):
            self._camera_kind = kind
            self._camera_index = idx
            self._start_camera(kind, idx)

    def _on_probe_cameras(self) -> None:
        found = probe_cameras(max_index=5)
        if found:
            self._cam_index_spin.setValue(found[0])
            self._status_label.setText(f"Найдены камеры: {found} | Трекер: {self._tracker_status}")
            self._start_camera(self._camera_kind, found[0])
        else:
            self._status_label.setText("Камеры не найдены. Подключите веб-камеру и попробуйте снова.")

    def _on_calibrate_bg(self) -> None:
        if self._reader is None or not isinstance(self._detector, OpenCVHandDetector):
            return
        frame, _ = self._reader.get_latest(0)
        if frame is not None:
            bgr = cv2.flip(frame.frame_bgr, 1) if self._mirror_preview_and_input else frame.frame_bgr
            self._detector.calibrate_background(bgr)
            self._status_label.setText("Фон откалиброван (OpenCV fallback).")

    # ------------------------------------------------------------------
    # Camera lifecycle & main 60 Hz tracking tick
    # ------------------------------------------------------------------

    def _stop_camera(self) -> None:
        self._controller.release_all()
        if self._reader is not None:
            self._reader.stop()
            self._reader = None

    def _start_camera(self, kind: Literal["webcam", "ps3_eye"], index: int) -> None:
        self._stop_camera()
        self._recognizer.reset()
        self._last_frame_seq = 0
        try:
            cam = create_camera(kind, device_index=index)
            reader = ThreadedCameraReader(cam, max_poll_hz=60.0)
            if reader.start():
                self._reader = reader
                self._status_label.setText(
                    f"Камера #{index} ({kind}) активна | Трекер: {self._tracker_status}"
                )
            else:
                self._preview_label.setText(
                    f"Не удалось открыть камеру #{index} ({kind}).\nВыберите другой индекс или нажмите «Найти камеры»."
                )
                self._status_label.setText(f"Камера #{index} недоступна | Трекер: {self._tracker_status}")
        except Exception as exc:
            logger.exception("Failed to start camera %s #%s", kind, index)
            self._preview_label.setText(f"Ошибка камеры: {exc}")

    def _on_tick(self) -> None:
        # Global F8 hotkey toggle (works from any focused Windows application!)
        f8_down = _is_f8_pressed_windows()
        if f8_down and not self._f8_was_down:
            self._toggle_enabled()
        self._f8_was_down = f8_down

        if self._reader is None or not self._reader.is_running:
            return

        frame, seq = self._reader.get_latest(self._last_frame_seq)
        if frame is None:
            return
        self._last_frame_seq = seq

        # Flip frame BEFORE recognition if mirror is ON so preview HUD text stays crisp & unmirrored
        if self._mirror_preview_and_input:
            bgr = cv2.flip(frame.frame_bgr, 1)
            proc_frame = CameraFrame(
                frame_bgr=bgr,
                timestamp=frame.timestamp,
                frame_index=frame.frame_index,
                source_kind=frame.source_kind,
                is_depth=frame.is_depth,
            )
        else:
            proc_frame = frame

        event = self._recognizer.process_frame(proc_frame)
        state = self._controller.update(event, now=time.monotonic())

        # Update click-through targeting reticle over the exact screen click pixel!
        self._overlay.update_state(state, use_qt_cursor_pos=True)

        # Update live camera HUD only when panel is visible (saves CPU when minimized to tray!)
        if self.isVisible():
            self._render_preview(proc_frame, event, state)

    def _render_preview(
        self,
        frame: CameraFrame,
        event,
        state: HandMouseFrameState,
    ) -> None:
        mode_tag = state.mode.value.upper()
        lock_tag = " [LOCKED]" if state.target_locked else ""
        action_text = f"{mode_tag}{lock_tag} ({state.target_x}, {state.target_y})"
        canvas = draw_hand_overlay(
            frame.frame_bgr,
            event,
            active_action=action_text,
            fps=self._reader.fps if self._reader is not None else 0.0,
            show_mask=self._show_mask,
            control_zone_norm=self._config.active_zone_rect(),
        )
        pix = bgr_to_qpixmap(canvas, self._preview_label.width() - 4, self._preview_label.height() - 4)
        self._preview_label.setPixmap(pix)

        lock_badge = "🔒 ЗАФИКСИРОВАНО" if state.target_locked else "Свободно"
        fps_val = self._reader.fps if self._reader is not None else 0.0
        self._status_label.setText(
            f"Трекер: {event.hand.tracker_name} | {fps_val:.0f} FPS | "
            f"Жест: {event.gesture.value} | Точка: ({state.target_x}, {state.target_y}) | {lock_badge}"
        )

    # ------------------------------------------------------------------
    # Clean exit / close to tray
    # ------------------------------------------------------------------

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        if not self._quitting and self._tray is not None and self._tray.isVisible():
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "HandMouse PC Control",
                "Работает в фоне в трее. Нажмите F8 для паузы/старта.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            return
        self._cleanup()
        event.accept()

    def _cleanup(self) -> None:
        self._tick_timer.stop()
        self._stop_camera()
        self._overlay.hide()
        self._overlay.close()
        if self._tray is not None:
            self._tray.hide()

    def quit_app(self) -> None:
        self._quitting = True
        self._cleanup()
        QtWidgets.QApplication.quit()
