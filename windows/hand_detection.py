"""Real-time 2D RGB hand detection, multi-gesture recognition, and confirmation-only
handoff safety gate for non-depth cameras (PS3 Eye, standard webcams, Android RGB).

Engineered specifically for low-quality / dim / noisy cameras (such as PS3 Eye)
without confusing the user's face or torso with their hand:
1. MediaPipeHandDetector (21-keypoint local neural hand landmarker loaded via
   model_asset_buffer for full Windows path compatibility, VIDEO tracking mode +
   low-light CLAHE/gamma enhancement) — never mistakes a face/body for a hand.
2. OpenCVHandDetector (offline zero-download fallback) — denoised YCrCb+HSV skin
   filter (no CLAHE wall-shift!), calibrated "Ignore Me + BG" subtraction,
   motion-persistence scoring, torso/face rejection, and optional active hand ROI.
"""

from __future__ import annotations

import logging
import math
import os
import time
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np

from shared.config_loader import PROJECT_ROOT
from windows.camera import CameraFrame

logger = logging.getLogger(__name__)

DEFAULT_MEDIAPIPE_MODEL_PATH = PROJECT_ROOT / "assets" / "models" / "hand_landmarker.task"
MEDIAPIPE_MODEL_URLS: tuple[str, ...] = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
)

# Default offline OpenCV skin thresholds (tuned to reject pale/beige/gray walls)
DEFAULT_YCRCB_LOWER = (0, 138, 77)
DEFAULT_YCRCB_UPPER = (255, 173, 127)
DEFAULT_HSV_LOWER = (0, 58, 45)
DEFAULT_HSV_UPPER = (26, 255, 255)

HAND_CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),          # Thumb
    (0, 5), (5, 6), (6, 7), (7, 8),          # Index
    (5, 9), (9, 10), (10, 11), (11, 12),     # Middle
    (9, 13), (13, 14), (14, 15), (15, 16),   # Ring
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),  # Pinky & palm
)

_CLAHE = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
_LAST_MEDIAPIPE_STATUS: str = ""


def get_last_mediapipe_status() -> str:
    """Return diagnostic status from the most recent MediaPipe initialization attempt."""
    return _LAST_MEDIAPIPE_STATUS


def enhance_low_quality_frame(frame_bgr: np.ndarray) -> np.ndarray:
    """Enhance dim, noisy, low-contrast frames for neural hand tracking (PS3 Eye indoors)."""
    if frame_bgr is None or frame_bgr.size == 0 or frame_bgr.ndim != 3:
        return frame_bgr

    denoised = cv2.GaussianBlur(frame_bgr, (3, 3), 0)
    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)

    mean_l = float(np.mean(l_ch))
    if mean_l < 95.0:
        gamma = max(0.45, min(0.88, (mean_l + 25.0) / 130.0))
        lut = np.array([min(255, int(((i / 255.0) ** gamma) * 255.0)) for i in range(256)], dtype=np.uint8)
        l_ch = cv2.LUT(l_ch, lut)

    l_ch = _CLAHE.apply(l_ch)
    enhanced_lab = cv2.merge((l_ch, a_ch, b_ch))
    return cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)


def ensure_mediapipe_model(
    target_path: Path = DEFAULT_MEDIAPIPE_MODEL_PATH,
    *,
    allow_download: bool = True,
    timeout_seconds: float = 20.0,
) -> Path | None:
    """Return local path to hand_landmarker.task, optionally fetching it once for offline use."""
    global _LAST_MEDIAPIPE_STATUS
    if target_path.exists() and target_path.stat().st_size > 100_000:
        _LAST_MEDIAPIPE_STATUS = "Local MediaPipe model ready"
        return target_path
    if not allow_download:
        _LAST_MEDIAPIPE_STATUS = "Offline mode (model not downloaded)"
        return None

    try:
        import mediapipe  # noqa: F401
    except Exception as exc:
        _LAST_MEDIAPIPE_STATUS = f"mediapipe import failed: {exc}"
        logger.warning("MediaPipe not available: %s", exc)
        return None

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".task.tmp")
    last_err = ""
    for url in MEDIAPIPE_MODEL_URLS:
        logger.info("Downloading local MediaPipe HandLandmarker model (~7.5 MB) from %s ...", url)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GCS-Shimeji/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp, tmp_path.open("wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
            if tmp_path.stat().st_size < 100_000:
                tmp_path.unlink(missing_ok=True)
                last_err = "Downloaded model file was too small"
                continue
            os.replace(tmp_path, target_path)
            _LAST_MEDIAPIPE_STATUS = "Downloaded local MediaPipe model"
            logger.info("Saved local MediaPipe model (%d bytes). Future runs are 100%% offline.", target_path.stat().st_size)
            return target_path
        except Exception as exc:
            last_err = str(exc)
            tmp_path.unlink(missing_ok=True)

    _LAST_MEDIAPIPE_STATUS = f"Model download failed: {last_err}"
    logger.warning("Could not download MediaPipe model (%s); using offline OpenCV detector.", last_err)
    return None


class GestureType(str, Enum):
    """Supported real-time hand gestures."""

    NONE = "none"
    PINCH = "pinch"              # Thumb + index pinched -> grab & drag pet
    RECEIVE_PET = "receive_pet"  # Open palm (4-5 fingers) -> confirm incoming handoff / wake
    POINT = "point"              # Single raised index finger -> call pet toward hand X
    FIST = "fist"                # Closed fist -> sleep / calm pet


@dataclass(frozen=True)
class Point2D:
    """Normalized 2D image coordinate in [0.0, 1.0]."""

    x: float
    y: float

    def distance_to(self, other: Point2D) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class HandDetectionResult:
    """2D RGB hand analysis output (depth_available is explicitly False for RGB cameras)."""

    detected: bool
    confidence: float = 0.0
    palm_center: Point2D | None = None
    bounding_box: tuple[float, float, float, float] | None = None  # normalized (x, y, w, h)
    finger_tips: tuple[Point2D, ...] = ()
    extended_fingers: int = 0
    openness_score: float = 0.0  # [0.0, 1.0]
    pinch_ratio: float = 1.0     # smaller -> tighter thumb-index pinch
    solidity: float = 0.0        # contour_area / hull_area (high for fist)
    aspect_ratio: float = 1.0    # height / width of hand bounding box
    depth_available: bool = False
    tracker_name: str = "opencv_2d"
    landmarks_21: tuple[Point2D, ...] = ()
    contour_norm: tuple[Point2D, ...] = ()
    mask_preview: np.ndarray | None = None


@dataclass(frozen=True)
class GestureEvent:
    """Recognized gesture with smoothed palm position and motion velocity."""

    gesture: GestureType
    confidence: float
    timestamp: float
    hand: HandDetectionResult
    smoothed_palm: Point2D | None = None
    palm_vx: float = 0.0  # normalized width / sec
    palm_vy: float = 0.0  # normalized height / sec (negative = moving upward)


class HandDetector(ABC):
    """Pluggable interface for 2D RGB hand detectors."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def roi_norm(self) -> tuple[float, float, float, float] | None:
        return None

    def set_roi_norm(self, roi: tuple[float, float, float, float] | None) -> None:
        """Optional active hand zone (x, y, w, h) in normalized [0, 1] coordinates."""

    def calibrate_background(self, frame_bgr: np.ndarray) -> bool:
        """Optional background reference calibration hook (used by OpenCV detector)."""
        return False

    def calibrate_skin_from_roi(self, frame_bgr: np.ndarray) -> bool:
        """Optional skin-tone calibration hook for weird lighting/white balance."""
        return False

    def clear_background(self) -> None:
        """Clear any calibrated background model and reset adaptive thresholds."""

    def close(self) -> None:
        """Release detector resources if any."""

    @abstractmethod
    def detect(self, frame: CameraFrame) -> HandDetectionResult:
        """Analyze a single 2D BGR CameraFrame and return hand features."""


def analyze_21_landmarks(
    points: Sequence[Point2D],
    confidence: float = 0.9,
    *,
    tracker_name: str = "mediapipe",
) -> HandDetectionResult:
    """Extract normalized palm center, extended fingers, openness, and pinch ratio from 21 hand landmarks."""
    if len(points) != 21:
        return HandDetectionResult(detected=False, depth_available=False, tracker_name=tracker_name)

    pts = tuple(points)
    wrist = pts[0]
    mcp_indices = (0, 5, 9, 13, 17)
    palm_x = sum(pts[i].x for i in mcp_indices) / len(mcp_indices)
    palm_y = sum(pts[i].y for i in mcp_indices) / len(mcp_indices)
    palm_center = Point2D(palm_x, palm_y)

    palm_scale = max(0.03, wrist.distance_to(pts[9]))

    finger_pairs = ((8, 6), (12, 10), (16, 14), (20, 18))
    extended_tips: list[Point2D] = []
    extended_count = 0
    extension_scores: list[float] = []

    for tip_i, pip_i in finger_pairs:
        tip_dist = wrist.distance_to(pts[tip_i])
        pip_dist = wrist.distance_to(pts[pip_i])
        ratio = tip_dist / max(0.01, pip_dist)
        extension_scores.append(min(1.0, max(0.0, (ratio - 1.0) / 0.45)))
        if ratio > 1.15 and tip_dist > palm_scale * 1.15:
            extended_count += 1
            extended_tips.append(pts[tip_i])

    thumb_spread = pts[4].distance_to(pts[17])
    thumb_ip_spread = pts[3].distance_to(pts[17])
    thumb_extended = thumb_spread > thumb_ip_spread * 1.08 and pts[4].distance_to(pts[5]) > palm_scale * 0.42
    if thumb_extended:
        extended_count += 1
        extended_tips.append(pts[4])

    thumb_index_dist = pts[4].distance_to(pts[8])
    pinch_ratio = min(1.5, thumb_index_dist / palm_scale)

    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    bw = max(0.01, max_x - min_x)
    bh = max(0.01, max_y - min_y)

    openness = min(
        1.0,
        max(0.0, 0.65 * (extended_count / 5.0) + 0.35 * (sum(extension_scores) / len(extension_scores))),
    )
    solidity = max(0.45, 0.96 - openness * 0.40)

    return HandDetectionResult(
        detected=True,
        confidence=min(1.0, max(0.0, float(confidence))),
        palm_center=palm_center,
        bounding_box=(min_x, min_y, bw, bh),
        finger_tips=tuple(extended_tips),
        extended_fingers=extended_count,
        openness_score=openness,
        pinch_ratio=pinch_ratio,
        solidity=solidity,
        aspect_ratio=bh / bw,
        depth_available=False,
        tracker_name=tracker_name,
        landmarks_21=pts,
    )


class MediaPipeHandDetector(HandDetector):
    """Offline local 21-landmark neural hand detector using MediaPipe Tasks HandLandmarker.

    Loads the model via `model_asset_buffer` (in-memory bytes) so Windows paths with
    spaces, long directories, or Unicode characters NEVER fail in MediaPipe's C++ loader.
    """

    def __init__(
        self,
        model_path: str | Path = DEFAULT_MEDIAPIPE_MODEL_PATH,
        *,
        min_detection_confidence: float = 0.35,
        min_presence_confidence: float = 0.35,
        min_tracking_confidence: float = 0.32,
        enhance_low_light: bool = True,
    ) -> None:
        self._model_path = Path(model_path)
        if not self._model_path.exists():
            raise FileNotFoundError(f"MediaPipe hand_landmarker.task not found at {self._model_path}")

        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            HandLandmarker,
            HandLandmarkerOptions,
            RunningMode,
        )

        self._mp = mp
        self._enhance_low_light = enhance_low_light
        self._roi: tuple[float, float, float, float] | None = None
        self._last_ts_ms: int = 0

        # Passing raw bytes avoids MediaPipe C++ fopen failures on Windows paths!
        model_bytes = self._model_path.read_bytes()
        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=model_bytes),
            running_mode=RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._landmarker = HandLandmarker.create_from_options(options)
        logger.info("Initialized MediaPipeHandDetector (VIDEO mode, buffer=%d bytes)", len(model_bytes))

    @property
    def name(self) -> str:
        return "mediapipe_21pt"

    @property
    def roi_norm(self) -> tuple[float, float, float, float] | None:
        return self._roi

    def set_roi_norm(self, roi: tuple[float, float, float, float] | None) -> None:
        self._roi = roi

    def detect(self, frame: CameraFrame) -> HandDetectionResult:
        bgr = frame.frame_bgr
        if bgr is None or bgr.size == 0 or bgr.ndim != 3:
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="mediapipe")

        h, w = bgr.shape[:2]
        roi = self._roi
        if roi is not None:
            rx0 = max(0, int(roi[0] * w))
            ry0 = max(0, int(roi[1] * h))
            rx1 = min(w, int((roi[0] + roi[2]) * w))
            ry1 = min(h, int((roi[1] + roi[3]) * h))
            work_bgr = bgr[ry0:ry1, rx0:rx1] if (rx1 - rx0 >= 32 and ry1 - ry0 >= 32) else bgr
        else:
            rx0, ry0, rx1, ry1 = 0, 0, w, h
            work_bgr = bgr

        if self._enhance_low_light:
            work_bgr = enhance_low_quality_frame(work_bgr)

        rgb = np.ascontiguousarray(cv2.cvtColor(work_bgr, cv2.COLOR_BGR2RGB))
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)

        ts_ms = int(frame.timestamp * 1000.0)
        if ts_ms <= self._last_ts_ms:
            ts_ms = self._last_ts_ms + 1
        self._last_ts_ms = ts_ms

        result = self._landmarker.detect_for_video(mp_image, ts_ms)
        if not result.hand_landmarks:
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="mediapipe")

        raw_lms = result.hand_landmarks[0]
        rw = max(1, rx1 - rx0)
        rh = max(1, ry1 - ry0)
        points = [
            Point2D(
                x=(rx0 + float(lm.x) * rw) / w,
                y=(ry0 + float(lm.y) * rh) / h,
            )
            for lm in raw_lms
        ]
        conf = 0.90
        if result.handedness and result.handedness[0]:
            conf = float(result.handedness[0][0].score)
        return analyze_21_landmarks(points, confidence=conf, tracker_name="mediapipe")

    def close(self) -> None:
        if hasattr(self, "_landmarker") and self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None


class OpenCVHandDetector(HandDetector):
    """Offline zero-download 2D RGB hand detector engineered to reject walls, sensor grain,
    and stationary faces/torsos.

    Notice: intentionally uses median-denoised RAW BGR (NOT CLAHE!) for color thresholding
    so beige/warm walls do not get boosted into skin tones.
    """

    def __init__(
        self,
        *,
        min_area_ratio: float = 0.015,
        max_area_ratio: float = 0.34,
        ycrcb_lower: tuple[int, int, int] = DEFAULT_YCRCB_LOWER,
        ycrcb_upper: tuple[int, int, int] = DEFAULT_YCRCB_UPPER,
        bg_diff_threshold: int = 24,
        enable_face_exclusion: bool = True,
    ) -> None:
        self._min_area_ratio = min_area_ratio
        self._max_area_ratio = max_area_ratio
        self._ycrcb_lower = np.array(ycrcb_lower, dtype=np.uint8)
        self._ycrcb_upper = np.array(ycrcb_upper, dtype=np.uint8)
        self._hsv_lower = np.array(DEFAULT_HSV_LOWER, dtype=np.uint8)
        self._hsv_upper = np.array(DEFAULT_HSV_UPPER, dtype=np.uint8)
        self._bg_diff_threshold = bg_diff_threshold
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._clean_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._motion_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

        self._bg_reference_blur: np.ndarray | None = None
        self._prev_gray: np.ndarray | None = None
        self._motion_memory: np.ndarray | None = None
        self._last_hand_center: Point2D | None = None
        self._roi: tuple[float, float, float, float] | None = None
        self._skin_calibrated: bool = False

        self._face_cascade: cv2.CascadeClassifier | None = None
        if enable_face_exclusion:
            self._face_cascade = self._load_optional_face_cascade()

    @property
    def name(self) -> str:
        return "opencv_2d"

    @property
    def roi_norm(self) -> tuple[float, float, float, float] | None:
        return self._roi

    def set_roi_norm(self, roi: tuple[float, float, float, float] | None) -> None:
        self._roi = roi
        self._last_hand_center = None

    @property
    def has_background_calibration(self) -> bool:
        return self._bg_reference_blur is not None

    @staticmethod
    def _load_optional_face_cascade() -> cv2.CascadeClassifier | None:
        try:
            haar_dir = getattr(getattr(cv2, "data", None), "haarcascades", "")
            xml_path = os.path.join(haar_dir, "haarcascade_frontalface_default.xml") if haar_dir else ""
            if xml_path and os.path.exists(xml_path):
                clf = cv2.CascadeClassifier(xml_path)
                if not clf.empty():
                    return clf
        except Exception:
            pass
        return None

    def calibrate_background(self, frame_bgr: np.ndarray) -> bool:
        """Capture seated user + room background so raising your hand isolates ONLY the hand."""
        if frame_bgr is None or frame_bgr.size == 0 or frame_bgr.ndim != 3:
            return False
        denoised = cv2.medianBlur(frame_bgr, 5)
        self._bg_reference_blur = cv2.GaussianBlur(denoised, (9, 9), 0)
        self._last_hand_center = None
        logger.info("Calibrated OpenCV background/user reference (%dx%d).", frame_bgr.shape[1], frame_bgr.shape[0])
        return True

    def calibrate_skin_from_roi(self, frame_bgr: np.ndarray) -> bool:
        """Adapt YCrCb & HSV skin bounds to the user's hand under the current camera white balance."""
        if frame_bgr is None or frame_bgr.size == 0 or frame_bgr.ndim != 3:
            return False
        work = cv2.medianBlur(frame_bgr, 5)
        h, w = work.shape[:2]
        if self._roi is not None:
            rx, ry, rw, rh = self._roi
            cx, cy = int((rx + rw * 0.5) * w), int((ry + rh * 0.5) * h)
        else:
            cx, cy = w // 2, h // 2
        patch_r = max(12, min(w, h) // 10)
        patch = work[max(0, cy - patch_r):min(h, cy + patch_r), max(0, cx - patch_r):min(w, cx + patch_r)]
        if patch.size == 0:
            return False

        ycrcb = cv2.cvtColor(patch, cv2.COLOR_BGR2YCrCb)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        cr = ycrcb[:, :, 1]
        cb = ycrcb[:, :, 2]
        h_ch = hsv[:, :, 0]
        s_ch = hsv[:, :, 1]

        cr_med, cb_med = float(np.median(cr)), float(np.median(cb))
        cr_std = max(10.0, min(22.0, float(np.std(cr)) * 2.0))
        cb_std = max(10.0, min(22.0, float(np.std(cb)) * 2.0))
        self._ycrcb_lower = np.array([0, max(0, int(cr_med - cr_std)), max(0, int(cb_med - cb_std))], dtype=np.uint8)
        self._ycrcb_upper = np.array([255, min(255, int(cr_med + cr_std)), min(255, int(cb_med + cb_std))], dtype=np.uint8)

        h_med, s_med = float(np.median(h_ch)), float(np.median(s_ch))
        h_span = max(12.0, min(25.0, float(np.std(h_ch)) * 2.0))
        self._hsv_lower = np.array([max(0, int(h_med - h_span)), max(25, int(s_med - 55)), 35], dtype=np.uint8)
        self._hsv_upper = np.array([min(179, int(h_med + h_span)), 255, 255], dtype=np.uint8)
        self._skin_calibrated = True
        logger.info("Calibrated skin bounds from camera ROI: YCrCb=[%s..%s]", self._ycrcb_lower, self._ycrcb_upper)
        return True

    def clear_background(self) -> None:
        self._bg_reference_blur = None
        self._motion_memory = None
        self._prev_gray = None
        self._last_hand_center = None
        self._skin_calibrated = False
        self._ycrcb_lower = np.array(DEFAULT_YCRCB_LOWER, dtype=np.uint8)
        self._ycrcb_upper = np.array(DEFAULT_YCRCB_UPPER, dtype=np.uint8)
        self._hsv_lower = np.array(DEFAULT_HSV_LOWER, dtype=np.uint8)
        self._hsv_upper = np.array(DEFAULT_HSV_UPPER, dtype=np.uint8)

    def _update_motion_memory(self, gray: np.ndarray) -> np.ndarray | None:
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            self._motion_memory = np.zeros(gray.shape, dtype=np.float32)
            return None

        diff = cv2.absdiff(gray, self._prev_gray)
        self._prev_gray = gray
        _, mot_bin = cv2.threshold(diff, 14, 255, cv2.THRESH_BINARY)
        mot_bin = cv2.dilate(mot_bin, self._motion_kernel, iterations=2)
        if self._motion_memory is None or self._motion_memory.shape != gray.shape:
            self._motion_memory = mot_bin.astype(np.float32)
        else:
            self._motion_memory = np.maximum(self._motion_memory * 0.90, mot_bin.astype(np.float32))
        return self._motion_memory

    def _select_hand_contour(
        self,
        contours: Sequence[np.ndarray],
        w: int,
        h: int,
        motion_memory: np.ndarray | None,
    ) -> np.ndarray | None:
        frame_area = float(max(1, w * h))
        candidates: list[tuple[float, np.ndarray]] = []
        has_any_motion = bool(motion_memory is not None and float(np.max(motion_memory)) > 40.0)

        for cnt in contours:
            area = float(cv2.contourArea(cnt))
            area_ratio = area / frame_area
            if area_ratio < self._min_area_ratio or area_ratio > self._max_area_ratio:
                continue

            bx, by, bw, bh = cv2.boundingRect(cnt)
            cx_norm = (bx + bw * 0.5) / w
            cy_norm = (by + bh * 0.5) / h
            w_norm = bw / w
            h_norm = bh / h

            roi_bottom_px = int((self._roi[1] + self._roi[3]) * h) if self._roi is not None else h
            touches_bottom = (by + bh) >= int(roi_bottom_px - max(4, int(h * 0.04)))
            aspect = bh / max(1.0, float(bw))
            if touches_bottom and (w_norm > 0.42 or aspect < 0.70):
                continue

            motion_ratio = 0.0
            if motion_memory is not None:
                patch = motion_memory[by:by + bh, bx:bx + bw]
                if patch.size > 0:
                    motion_ratio = float(np.mean(patch > 40.0))

            size_score = 1.0 - min(1.0, abs(area_ratio - 0.08) / 0.24)
            score = size_score * 1.2

            if has_any_motion:
                score += motion_ratio * 3.2
                if motion_ratio < 0.03:
                    score -= 1.5

            if (0.28 <= cx_norm <= 0.72) and (cy_norm <= 0.44) and (0.75 <= (h_norm / max(0.01, w_norm)) <= 1.55):
                if motion_ratio < 0.12 and len(contours) > 1:
                    score -= 1.2

            if self._last_hand_center is not None:
                dist = math.hypot(cx_norm - self._last_hand_center.x, cy_norm - self._last_hand_center.y)
                score += max(0.0, 0.9 - dist * 2.0)

            candidates.append((score, cnt))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def detect(self, frame: CameraFrame) -> HandDetectionResult:
        bgr = frame.frame_bgr
        if bgr is None or bgr.size == 0 or bgr.ndim != 3:
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="opencv_2d")

        h, w = bgr.shape[:2]
        frame_area = float(max(1, w * h))
        # Median blur removes PS3 Eye salt-and-pepper noise WITHOUT shifting wall colors into skin tones
        work = cv2.medianBlur(bgr, 5)

        gray = cv2.GaussianBlur(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        motion_memory = self._update_motion_memory(gray)

        # Dual-space skin mask (YCrCb + HSV)
        ycrcb = cv2.cvtColor(work, cv2.COLOR_BGR2YCrCb)
        mask_ycrcb = cv2.inRange(ycrcb, self._ycrcb_lower, self._ycrcb_upper)
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        mask_hsv = cv2.inRange(hsv, self._hsv_lower, self._hsv_upper)
        mask = cv2.bitwise_and(mask_ycrcb, mask_hsv)

        # Reject bright low-saturation beige/cream indoor walls (unless user calibrated custom skin color)
        if not self._skin_calibrated:
            b_ch, _g_ch, r_ch = cv2.split(work)
            pale_wall = (r_ch > 160) & (b_ch > 135) & (hsv[:, :, 1] < 72)
            mask[pale_wall] = 0

        if self._roi is not None:
            rx0 = max(0, int(self._roi[0] * w))
            ry0 = max(0, int(self._roi[1] * h))
            rx1 = min(w, int((self._roi[0] + self._roi[2]) * w))
            ry1 = min(h, int((self._roi[1] + self._roi[3]) * h))
            roi_mask = np.zeros_like(mask)
            roi_mask[ry0:ry1, rx0:rx1] = 255
            mask = cv2.bitwise_and(mask, roi_mask)

        if self._bg_reference_blur is not None and self._bg_reference_blur.shape == work.shape:
            cur_blur = cv2.GaussianBlur(work, (9, 9), 0)
            diff = cv2.absdiff(cur_blur, self._bg_reference_blur)
            diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, fg_mask = cv2.threshold(diff_gray, self._bg_diff_threshold, 255, cv2.THRESH_BINARY)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self._clean_kernel, iterations=1)
            fg_mask = cv2.dilate(fg_mask, self._kernel, iterations=2)
            mask = cv2.bitwise_and(mask, fg_mask)

        if self._face_cascade is not None:
            faces = self._face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=4, minSize=(48, 48))
            for fx, fy, fw_box, fh_box in faces:
                pad_x = int(fw_box * 0.20)
                pad_y = int(fh_box * 0.30)
                mask[max(0, fy - pad_y):min(h, fy + fh_box + pad_y), max(0, fx - pad_x):min(w, fx + fw_box + pad_x)] = 0

        # Suppress speckle noise cleanly while keeping finger gaps sharp
        mask = cv2.medianBlur(mask, 5)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel, iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            self._last_hand_center = None
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="opencv_2d", mask_preview=mask)

        chosen = self._select_hand_contour(contours, w, h, motion_memory)
        if chosen is None:
            self._last_hand_center = None
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="opencv_2d", mask_preview=mask)

        area = float(cv2.contourArea(chosen))
        area_ratio = area / frame_area
        bx, by, bw, bh = cv2.boundingRect(chosen)
        moments = cv2.moments(chosen)
        if moments["m00"] > 0:
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
        else:
            cx = bx + bw / 2.0
            cy = by + bh / 2.0

        self._last_hand_center = Point2D(cx / w, cy / h)

        hull_pts = cv2.convexHull(chosen, returnPoints=True)
        hull_area = float(cv2.contourArea(hull_pts))
        solidity = area / max(1.0, hull_area)

        hull_indices = cv2.convexHull(chosen, returnPoints=False)
        finger_valleys = 0
        tip_points: list[tuple[float, float]] = []

        if hull_indices is not None and len(hull_indices) > 3 and len(chosen) > 3:
            try:
                defects = cv2.convexityDefects(chosen, hull_indices)
            except cv2.error:
                defects = None

            if defects is not None:
                defects_2d = defects.reshape(-1, 4)
                min_depth_px = max(8.0, bh * 0.14)
                for row in defects_2d:
                    s_idx, e_idx, f_idx, depth_fixed = int(row[0]), int(row[1]), int(row[2]), int(row[3])
                    depth_px = depth_fixed / 256.0
                    start = tuple(chosen[s_idx][0])
                    end = tuple(chosen[e_idx][0])
                    far = tuple(chosen[f_idx][0])

                    a = math.hypot(end[0] - start[0], end[1] - start[1])
                    b = math.hypot(far[0] - start[0], far[1] - start[1])
                    c = math.hypot(end[0] - far[0], end[1] - far[1])
                    if b * c <= 1e-6:
                        continue
                    cos_angle = max(-1.0, min(1.0, (b * b + c * c - a * a) / (2.0 * b * c)))
                    angle_deg = math.degrees(math.acos(cos_angle))

                    if angle_deg <= 88.0 and depth_px >= min_depth_px:
                        finger_valleys += 1
                        tip_points.append((float(start[0]), float(start[1])))
                        tip_points.append((float(end[0]), float(end[1])))

        dedup_tips: list[Point2D] = []
        min_tip_sep = max(10.0, bw * 0.12)
        for tx, ty in tip_points:
            if all(math.hypot(tx - p.x * w, ty - p.y * h) > min_tip_sep for p in dedup_tips):
                dedup_tips.append(Point2D(tx / w, ty / h))

        aspect_ratio = float(bh) / max(1.0, float(bw))

        top_band_y = by + max(4, int(bh * 0.16))
        top_pts = chosen[chosen[:, 0, 1] <= top_band_y]
        if len(top_pts) >= 2:
            top_min_x = float(np.min(top_pts[:, 0, 0]))
            top_max_x = float(np.max(top_pts[:, 0, 0]))
            top_span_ratio = (top_max_x - top_min_x) / max(1.0, float(bw))
        else:
            top_span_ratio = 0.5

        if finger_valleys > 0:
            extended_fingers = min(5, finger_valleys + 1)
        elif solidity >= 0.88 and aspect_ratio < 1.25:
            extended_fingers = 0
        else:
            extended_fingers = 1

        openness_score = min(
            1.0,
            max(
                0.0,
                0.55 * min(1.0, finger_valleys / 3.0)
                + 0.25 * max(0.0, (0.90 - solidity) / 0.35)
                + 0.20 * min(1.0, top_span_ratio / 0.75),
            ),
        )

        pinch_ratio = min(1.0, max(0.0, top_span_ratio))
        confidence = min(1.0, max(0.35, 0.5 + min(0.5, area_ratio * 4.0)))

        approx = cv2.approxPolyDP(chosen, epsilon=max(2.0, 0.008 * cv2.arcLength(chosen, True)), closed=True)
        contour_norm = tuple(Point2D(float(pt[0][0]) / w, float(pt[0][1]) / h) for pt in approx)

        return HandDetectionResult(
            detected=True,
            confidence=confidence,
            palm_center=Point2D(cx / w, cy / h),
            bounding_box=(bx / w, by / h, bw / w, bh / h),
            finger_tips=tuple(dedup_tips),
            extended_fingers=extended_fingers,
            openness_score=openness_score,
            pinch_ratio=pinch_ratio,
            solidity=solidity,
            aspect_ratio=aspect_ratio,
            depth_available=False,
            tracker_name="opencv_2d",
            contour_norm=contour_norm,
            mask_preview=mask,
        )


def create_best_hand_detector(
    model_path: str | Path | None = None,
    *,
    prefer_mediapipe: bool = True,
    auto_download_model: bool = False,
) -> HandDetector:
    """Select MediaPipeHandDetector (21-pt neural, ignores face/body) if available, else OpenCVHandDetector."""
    global _LAST_MEDIAPIPE_STATUS
    if prefer_mediapipe:
        candidate = Path(model_path) if model_path is not None else DEFAULT_MEDIAPIPE_MODEL_PATH
        if not candidate.exists() and auto_download_model:
            resolved = ensure_mediapipe_model(candidate, allow_download=True)
            if resolved is not None:
                candidate = resolved
        if candidate.exists():
            try:
                det = MediaPipeHandDetector(model_path=candidate)
                _LAST_MEDIAPIPE_STATUS = "Active (MediaPipe 21-pt AI)"
                return det
            except Exception as exc:
                _LAST_MEDIAPIPE_STATUS = f"MediaPipe init error: {exc}"
                logger.warning("MediaPipeHandDetector unavailable (%s); falling back to OpenCVHandDetector.", exc)
        elif not _LAST_MEDIAPIPE_STATUS:
            _LAST_MEDIAPIPE_STATUS = f"Model missing: {candidate.name}"
    return OpenCVHandDetector()


class GestureRecognizer:
    """Temporal gesture classifier with EMA palm smoothing, velocity tracking, and dwell filtering."""

    def __init__(
        self,
        detector: HandDetector | None = None,
        *,
        hold_frames: int = 3,
        receive_openness_threshold: float = 0.62,
        receive_min_fingers: int = 4,
        pinch_ratio_threshold: float = 0.38,
        smoothing_alpha: float = 0.45,
    ) -> None:
        self.detector = detector or OpenCVHandDetector()
        self._hold_frames = max(1, int(hold_frames))
        self._receive_openness_threshold = receive_openness_threshold
        self._receive_min_fingers = receive_min_fingers
        self._pinch_ratio_threshold = pinch_ratio_threshold
        self._smoothing_alpha = min(1.0, max(0.05, smoothing_alpha))

        self._candidate_gesture: GestureType = GestureType.NONE
        self._streak: int = 0
        self._smoothed_palm: Point2D | None = None
        self._last_timestamp: float | None = None

    def reset(self) -> None:
        self._candidate_gesture = GestureType.NONE
        self._streak = 0
        self._smoothed_palm = None
        self._last_timestamp = None

    def classify_hand(self, hand: HandDetectionResult) -> tuple[GestureType, float]:
        if not hand.detected:
            return GestureType.NONE, 0.0

        if hand.extended_fingers >= self._receive_min_fingers and hand.openness_score >= self._receive_openness_threshold:
            conf = min(1.0, 0.5 * hand.confidence + 0.5 * hand.openness_score)
            return GestureType.RECEIVE_PET, conf

        if hand.tracker_name == "mediapipe" and hand.landmarks_21:
            if hand.extended_fingers == 0 and hand.openness_score < 0.15:
                return GestureType.FIST, min(1.0, 0.65 * hand.confidence + 0.35 * (1.0 - hand.openness_score))
            if hand.pinch_ratio <= self._pinch_ratio_threshold:
                tightness = 1.0 - (hand.pinch_ratio / max(0.01, self._pinch_ratio_threshold))
                conf = min(1.0, 0.55 * hand.confidence + 0.45 * max(0.25, tightness))
                return GestureType.PINCH, conf
            if hand.extended_fingers in (1, 2):
                return GestureType.POINT, min(1.0, 0.70 * hand.confidence + 0.25)
            return GestureType.NONE, 0.0

        if hand.extended_fingers == 0 and hand.solidity >= 0.88 and hand.aspect_ratio < 1.25:
            conf = min(1.0, 0.55 * hand.confidence + 0.45 * hand.solidity)
            return GestureType.FIST, conf

        if hand.extended_fingers == 1 and hand.aspect_ratio >= 2.0 and hand.pinch_ratio <= 0.32:
            conf = min(1.0, 0.55 * hand.confidence + 0.35)
            return GestureType.POINT, conf

        if hand.extended_fingers <= 2 and hand.pinch_ratio <= self._pinch_ratio_threshold:
            tightness = 1.0 - (hand.pinch_ratio / max(0.01, self._pinch_ratio_threshold))
            closedness = 1.0 - hand.openness_score
            conf = min(1.0, 0.45 * hand.confidence + 0.30 * closedness + 0.25 * max(0.2, tightness))
            return GestureType.PINCH, conf

        return GestureType.NONE, 0.0

    def process_frame(self, frame: CameraFrame) -> GestureEvent:
        hand = self.detector.detect(frame)
        raw_gesture, raw_conf = self.classify_hand(hand)

        vx, vy = 0.0, 0.0
        if hand.detected and hand.palm_center is not None:
            if self._smoothed_palm is None:
                self._smoothed_palm = hand.palm_center
            else:
                prev = self._smoothed_palm
                a = self._smoothing_alpha
                new_palm = Point2D(
                    x=prev.x + (hand.palm_center.x - prev.x) * a,
                    y=prev.y + (hand.palm_center.y - prev.y) * a,
                )
                if self._last_timestamp is not None:
                    dt = frame.timestamp - self._last_timestamp
                    if dt > 1e-4:
                        vx = (new_palm.x - prev.x) / dt
                        vy = (new_palm.y - prev.y) / dt
                self._smoothed_palm = new_palm
            self._last_timestamp = frame.timestamp
        else:
            self._smoothed_palm = None
            self._last_timestamp = None

        if raw_gesture == GestureType.NONE:
            self._candidate_gesture = GestureType.NONE
            self._streak = 0
            return GestureEvent(
                GestureType.NONE,
                0.0,
                frame.timestamp,
                hand,
                smoothed_palm=self._smoothed_palm,
                palm_vx=vx,
                palm_vy=vy,
            )

        if raw_gesture == self._candidate_gesture:
            self._streak += 1
        else:
            self._candidate_gesture = raw_gesture
            self._streak = 1

        if self._streak >= self._hold_frames:
            return GestureEvent(
                raw_gesture,
                raw_conf,
                frame.timestamp,
                hand,
                smoothed_palm=self._smoothed_palm,
                palm_vx=vx,
                palm_vy=vy,
            )

        return GestureEvent(
            GestureType.NONE,
            0.0,
            frame.timestamp,
            hand,
            smoothed_palm=self._smoothed_palm,
            palm_vx=vx,
            palm_vy=vy,
        )


class HandoffGestureConfirmationController:
    """Enforces the safety rule: camera gestures ONLY confirm an incoming handoff."""

    def __init__(
        self,
        recognizer: GestureRecognizer | None = None,
        *,
        min_confidence: float = 0.65,
        on_confirm_handoff: Callable[[str, str], None] | None = None,
    ) -> None:
        self.recognizer = recognizer or GestureRecognizer()
        self._min_confidence = min_confidence
        self._on_confirm_handoff = on_confirm_handoff
        self._pending_handoff_id: str | None = None
        self._offer_expires_at: float = 0.0

    @property
    def pending_handoff_id(self) -> str | None:
        return self._pending_handoff_id

    def arm_for_incoming_offer(self, handoff_id: str, expires_at: float) -> None:
        self._pending_handoff_id = handoff_id
        self._offer_expires_at = expires_at
        self.recognizer.reset()
        logger.info("Camera gesture confirmation armed for incoming handoff '%s'", handoff_id)

    def disarm(self) -> None:
        self._pending_handoff_id = None
        self._offer_expires_at = 0.0
        self.recognizer.reset()

    def process_event(self, event: GestureEvent) -> str | None:
        if self._pending_handoff_id is None:
            return None

        if time.time() > self._offer_expires_at:
            self.disarm()
            return None

        if event.gesture == GestureType.RECEIVE_PET and event.confidence >= self._min_confidence:
            confirmed_id = self._pending_handoff_id
            self.disarm()
            logger.info(
                "Confirmed incoming handoff '%s' via camera gesture (confidence=%.2f)",
                confirmed_id,
                event.confidence,
            )
            if self._on_confirm_handoff is not None:
                self._on_confirm_handoff(confirmed_id, "gesture")
            return confirmed_id

        return None

    def process_frame(self, frame: CameraFrame) -> str | None:
        if self._pending_handoff_id is None:
            return None

        if time.time() > self._offer_expires_at:
            self.disarm()
            return None

        event = self.recognizer.process_frame(frame)
        return self.process_event(event)


def draw_hand_overlay(
    frame_bgr: np.ndarray,
    event: GestureEvent,
    *,
    active_action: str = "",
    fps: float = 0.0,
    show_mask: bool = False,
    handoff_armed: bool = False,
    roi_norm: tuple[float, float, float, float] | None = None,
) -> np.ndarray:
    """Render real-time hand skeleton/contour, ROI box, palm crosshair, and gesture HUD onto a BGR frame."""
    hand = event.hand
    if show_mask and hand.mask_preview is not None:
        canvas = cv2.cvtColor(hand.mask_preview, cv2.COLOR_GRAY2BGR)
    else:
        canvas = frame_bgr.copy()

    h, w = canvas.shape[:2]

    if roi_norm is not None:
        rx0, ry0 = int(roi_norm[0] * w), int(roi_norm[1] * h)
        rx1, ry1 = int((roi_norm[0] + roi_norm[2]) * w), int((roi_norm[1] + roi_norm[3]) * h)
        cv2.rectangle(canvas, (rx0, ry0), (rx1, ry1), (224, 198, 86), 2)
        cv2.putText(canvas, "HAND ZONE", (rx0 + 6, ry0 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (224, 198, 86), 1, cv2.LINE_AA)

    if hand.detected:
        if hand.bounding_box is not None:
            bx, by, bw, bh = hand.bounding_box
            x0, y0 = int(bx * w), int(by * h)
            x1, y1 = int((bx + bw) * w), int((by + bh) * h)
            box_color = (80, 235, 130) if event.gesture != GestureType.NONE else (220, 190, 90)
            cv2.rectangle(canvas, (x0, y0), (x1, y1), box_color, 2)

        if hand.landmarks_21:
            px_pts = [(int(p.x * w), int(p.y * h)) for p in hand.landmarks_21]
            for a, b in HAND_CONNECTIONS:
                cv2.line(canvas, px_pts[a], px_pts[b], (240, 200, 70), 2)
            for idx, pt in enumerate(px_pts):
                color = (90, 255, 160) if idx in (4, 8, 12, 16, 20) else (255, 255, 255)
                cv2.circle(canvas, pt, 4 if idx in (4, 8) else 3, color, -1)
        elif hand.contour_norm:
            poly = np.array([[[int(p.x * w), int(p.y * h)]] for p in hand.contour_norm], dtype=np.int32)
            cv2.polylines(canvas, [poly], isClosed=True, color=(240, 200, 70), thickness=2)
            for tip in hand.finger_tips:
                cv2.circle(canvas, (int(tip.x * w), int(tip.y * h)), 5, (90, 255, 160), -1)

        palm = event.smoothed_palm or hand.palm_center
        if palm is not None:
            px, py = int(palm.x * w), int(palm.y * h)
            cv2.circle(canvas, (px, py), 8, (86, 198, 224), 2)
            cv2.line(canvas, (px - 12, py), (px + 12, py), (86, 198, 224), 1)
            cv2.line(canvas, (px, py - 12), (px, py + 12), (86, 198, 224), 1)

    cv2.rectangle(canvas, (0, 0), (w, 46), (20, 24, 36), -1)
    gesture_label = event.gesture.value.upper()
    tracker_tag = "AI-21pt" if hand.tracker_name == "mediapipe" else "OpenCV-2D"
    header = f"{tracker_tag} | {fps:4.1f} FPS | Gesture: {gesture_label} ({int(event.confidence * 100)}%)"
    cv2.putText(canvas, header, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 244, 255), 1, cv2.LINE_AA)

    status_text = active_action or ("ARMED: Show Open Palm to Receive Pet" if handoff_armed else "Pinch=Grab | Point=Call | Palm=Wake | Fist=Sleep")
    status_color = (114, 245, 166) if handoff_armed else (160, 215, 245)
    cv2.putText(canvas, status_text, (8, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.42, status_color, 1, cv2.LINE_AA)
    return canvas
