"""Live PySide6 camera & gesture control HUD window for GCS Shimeji."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def bgr_to_qpixmap(frame_bgr: np.ndarray, target_width: int = 480, target_height: int = 360) -> QPixmap:
    """Convert an OpenCV BGR ndarray into a scaled QPixmap preserving aspect ratio without cropping."""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888).copy()
    pix = QPixmap.fromImage(qimg)
    if target_width > 0 and target_height > 0:
        return pix.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
    return pix


ROI_PRESETS: dict[str, tuple[float, float, float, float] | None] = {
    "Full Frame": None,
    "Right Hand Zone": (0.48, 0.14, 0.48, 0.78),
    "Left Hand Zone": (0.04, 0.14, 0.48, 0.78),
    "Center Zone": (0.22, 0.16, 0.56, 0.74),
}


class CameraPreviewWindow(QWidget):
    """Toggleable live camera HUD showing hand tracking, gestures, and calibration controls."""

    request_calibrate_bg = Signal()
    request_calibrate_skin = Signal()
    request_clear_bg = Signal()
    request_toggle_pause = Signal(bool)
    request_switch_camera_index = Signal(int)
    request_set_roi = Signal(object)  # tuple[float, float, float, float] | None

    def __init__(
        self,
        *,
        camera_kind: str = "webcam",
        camera_index: int = 0,
        tracker_name: str = "opencv_2d",
        tracker_note: str = "",
    ) -> None:
        super().__init__(None)
        self._show_mask: bool = False
        self._paused: bool = False

        self.setWindowTitle(f"GCS Shimeji — Camera & Gestures ({camera_kind})")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(
            """
            QWidget {
                background-color: #111622;
                color: #e6f4ff;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
            QPushButton {
                background-color: #223047;
                color: #e6f4ff;
                border: 1px solid #3c5478;
                border-radius: 5px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #2d405e;
            }
            QPushButton#primaryBtn {
                background-color: #56c6e0;
                color: #0e141f;
                font-weight: bold;
                border: none;
            }
            QSpinBox, QComboBox {
                background-color: #1c2638;
                color: #e6f4ff;
                border: 1px solid #3c5478;
                padding: 3px 6px;
                border-radius: 4px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self._video_label = QLabel("Waiting for camera frames...")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setFixedSize(480, 360)
        self._video_label.setStyleSheet("background-color: #080b12; border: 1px solid #2b3c58; border-radius: 6px;")
        root.addWidget(self._video_label)

        init_status = f"Tracker: {tracker_name}"
        if tracker_note:
            init_status += f" ({tracker_note})"
        self._status_label = QLabel(init_status)
        self._status_label.setStyleSheet("color: #66fcf1; font-weight: bold;")
        root.addWidget(self._status_label)

        controls_row1 = QHBoxLayout()
        calib_btn = QPushButton("Ignore Me + BG")
        calib_btn.setObjectName("primaryBtn")
        calib_btn.setToolTip("Sit normally with hand lowered and click to ignore your face, body & room")
        calib_btn.clicked.connect(self.request_calibrate_bg.emit)

        skin_btn = QPushButton("Sample Palm Color")
        skin_btn.setToolTip("Hold your palm in the center/hand zone and click to adapt to camera white balance")
        skin_btn.clicked.connect(self.request_calibrate_skin.emit)

        clear_btn = QPushButton("Reset")
        clear_btn.clicked.connect(self.request_clear_bg.emit)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause_clicked)

        controls_row1.addWidget(calib_btn)
        controls_row1.addWidget(skin_btn)
        controls_row1.addWidget(clear_btn)
        controls_row1.addWidget(self._pause_btn)
        root.addLayout(controls_row1)

        controls_row2 = QHBoxLayout()
        mask_cb = QCheckBox("Hand Mask")
        mask_cb.toggled.connect(self._on_mask_toggled)

        zone_lbl = QLabel("Zone:")
        self._zone_combo = QComboBox()
        for label in ROI_PRESETS:
            self._zone_combo.addItem(label)
        self._zone_combo.currentTextChanged.connect(self._on_zone_changed)

        cam_lbl = QLabel("Cam:")
        self._cam_spin = QSpinBox()
        self._cam_spin.setRange(0, 8)
        self._cam_spin.setValue(camera_index)
        switch_btn = QPushButton("Switch")
        switch_btn.clicked.connect(lambda: self.request_switch_camera_index.emit(int(self._cam_spin.value())))

        controls_row2.addWidget(mask_cb)
        controls_row2.addWidget(zone_lbl)
        controls_row2.addWidget(self._zone_combo)
        controls_row2.addStretch(1)
        controls_row2.addWidget(cam_lbl)
        controls_row2.addWidget(self._cam_spin)
        controls_row2.addWidget(switch_btn)
        root.addLayout(controls_row2)

        hint = QLabel(
            "Tip: MediaPipe AI (21-pt) ignores your face automatically. "
            "In OpenCV mode, click 'Ignore Me + BG' with hand down or pick 'Right Hand Zone'."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8fa6c2; font-size: 11px;")
        root.addWidget(hint)
        self.adjustSize()

    @property
    def show_mask(self) -> bool:
        return self._show_mask

    def set_status_text(self, text: str) -> None:
        self._status_label.setText(text)

    def update_frame(self, annotated_bgr: np.ndarray, status_text: str | None = None) -> None:
        if not self.isVisible():
            return
        self._video_label.setPixmap(bgr_to_qpixmap(annotated_bgr, target_width=480, target_height=360))
        if status_text is not None:
            self._status_label.setText(status_text)

    def _on_mask_toggled(self, checked: bool) -> None:
        self._show_mask = bool(checked)

    def _on_zone_changed(self, label: str) -> None:
        self.request_set_roi.emit(ROI_PRESETS.get(label))

    def _on_pause_clicked(self) -> None:
        self._paused = not self._paused
        self._pause_btn.setText("Resume" if self._paused else "Pause")
        self.request_toggle_pause.emit(self._paused)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        event.ignore()
        self.hide()
