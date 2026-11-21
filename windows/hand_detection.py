"""Real-time 2D RGB hand detection, multi-gesture recognition, and confirmation-only
handoff safety gate for non-depth cameras (PS3 Eye, standard webcams, Android RGB).

Supports two offline local tracking engines:
1. MediaPipeHandDetector (21-keypoint neural hand landmark tracking via local .task file)
2. OpenCVHandDetector (zero-download YCrCb/HSV + optional background-calibrated 2D contour tracker)
"""

from __future__ import annotations

import logging
import math
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

import cv2
import numpy as np

from shared.config_loader import PROJECT_ROOT
from windows.camera import CameraFrame

logger = logging.getLogger(__name__)

DEFAULT_MEDIAPIPE_MODEL_PATH = PROJECT_ROOT / "assets" / "models" / "hand_landmarker.task"

# Standard MediaPipe 21-landmark hand skeleton connections for HUD rendering
HAND_CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),          # Thumb
    (0, 5), (5, 6), (6, 7), (7, 8),          # Index
    (5, 9), (9, 10), (10, 11), (11, 12),     # Middle
    (9, 13), (13, 14), (14, 15), (15, 16),   # Ring
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),  # Pinky & palm
)


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

    def calibrate_background(self, frame_bgr: np.ndarray) -> bool:
        """Optional background reference calibration hook (used by OpenCV detector)."""
        return False

    def clear_background(self) -> None:
        """Clear any calibrated background model."""

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
    """Extract normalized palm center, extended fingers, openness, and pinch ratio from 21 hand landmarks.

    Standard MediaPipe indices:
      0=wrist, 1-4=thumb (4=tip), 5-8=index (6=PIP, 8=tip),
      9-12=middle (10=PIP, 12=tip), 13-16=ring (14=PIP, 16=tip),
      17-20=pinky (18=PIP, 20=tip).
    """
    if len(points) != 21:
        return HandDetectionResult(detected=False, depth_available=False, tracker_name=tracker_name)

    pts = tuple(points)
    wrist = pts[0]
    mcp_indices = (0, 5, 9, 13, 17)
    palm_x = sum(pts[i].x for i in mcp_indices) / len(mcp_indices)
    palm_y = sum(pts[i].y for i in mcp_indices) / len(mcp_indices)
    palm_center = Point2D(palm_x, palm_y)

    palm_scale = max(0.03, wrist.distance_to(pts[9]))

    # Check 4 non-thumb fingers: tip significantly farther from wrist than PIP joint
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

    # Thumb extension: distance from thumb tip (#4) to pinky MCP (#17) vs thumb IP (#3) to pinky MCP
    thumb_spread = pts[4].distance_to(pts[17])
    thumb_ip_spread = pts[3].distance_to(pts[17])
    thumb_extended = thumb_spread > thumb_ip_spread * 1.08 and pts[4].distance_to(pts[5]) > palm_scale * 0.42
    if thumb_extended:
        extended_count += 1
        extended_tips.append(pts[4])

    # Exact thumb-index pinch ratio normalized by palm size
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
    # Approximate solidity high when fist (all fingers curled)
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
    """Offline local 21-landmark neural hand detector using MediaPipe Tasks HandLandmarker."""

    def __init__(
        self,
        model_path: str | Path = DEFAULT_MEDIAPIPE_MODEL_PATH,
        *,
        min_detection_confidence: float = 0.55,
        min_tracking_confidence: float = 0.50,
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
        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(self._model_path)),
            running_mode=RunningMode.IMAGE,
            num_hands=1,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._landmarker = HandLandmarker.create_from_options(options)
        logger.info("Initialized MediaPipeHandDetector using local model: %s", self._model_path)

    @property
    def name(self) -> str:
        return "mediapipe"

    def detect(self, frame: CameraFrame) -> HandDetectionResult:
        bgr = frame.frame_bgr
        if bgr is None or bgr.size == 0 or bgr.ndim != 3:
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="mediapipe")

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)
        if not result.hand_landmarks:
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="mediapipe")

        raw_lms = result.hand_landmarks[0]
        points = [Point2D(float(lm.x), float(lm.y)) for lm in raw_lms]
        conf = 0.90
        if result.handedness and result.handedness[0]:
            conf = float(result.handedness[0][0].score)
        return analyze_21_landmarks(points, confidence=conf, tracker_name="mediapipe")

    def close(self) -> None:
        if hasattr(self, "_landmarker") and self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None


class OpenCVHandDetector(HandDetector):
    """Zero-download, offline 2D RGB hand detector using YCrCb+HSV skin mask,
    optional calibrated background subtraction, optional Haar face rejection,
    and convex hull defect analysis.

    Designed for PS3 Eye (60/120fps RGB), standard webcams, and Android RGB streams.
    """

    def __init__(
        self,
        *,
        min_area_ratio: float = 0.02,
        max_area_ratio: float = 0.65,
        ycrcb_lower: tuple[int, int, int] = (0, 133, 77),
        ycrcb_upper: tuple[int, int, int] = (255, 173, 127),
        bg_diff_threshold: int = 24,
        enable_face_exclusion: bool = True,
    ) -> None:
        self._min_area_ratio = min_area_ratio
        self._max_area_ratio = max_area_ratio
        self._ycrcb_lower = np.array(ycrcb_lower, dtype=np.uint8)
        self._ycrcb_upper = np.array(ycrcb_upper, dtype=np.uint8)
        self._hsv_lower = np.array((0, 28, 45), dtype=np.uint8)
        self._hsv_upper = np.array((35, 255, 255), dtype=np.uint8)
        self._bg_diff_threshold = bg_diff_threshold
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._bg_reference_blur: np.ndarray | None = None

        self._face_cascade: cv2.CascadeClassifier | None = None
        if enable_face_exclusion:
            self._face_cascade = self._load_optional_face_cascade()

    @property
    def name(self) -> str:
        return "opencv_2d"

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
        """Store a blurred reference frame of the empty room background to suppress walls/desk."""
        if frame_bgr is None or frame_bgr.size == 0 or frame_bgr.ndim != 3:
            return False
        self._bg_reference_blur = cv2.GaussianBlur(frame_bgr, (9, 9), 0)
        logger.info("Calibrated OpenCV hand detector background reference (%dx%d).", frame_bgr.shape[1], frame_bgr.shape[0])
        return True

    def clear_background(self) -> None:
        self._bg_reference_blur = None

    def detect(self, frame: CameraFrame) -> HandDetectionResult:
        bgr = frame.frame_bgr
        if bgr is None or bgr.size == 0 or bgr.ndim != 3:
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="opencv_2d")

        h, w = bgr.shape[:2]
        frame_area = float(max(1, w * h))

        # Dual-space skin mask (YCrCb + HSV) rejects cool/neutral backgrounds cleanly
        ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
        mask_ycrcb = cv2.inRange(ycrcb, self._ycrcb_lower, self._ycrcb_upper)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask_hsv = cv2.inRange(hsv, self._hsv_lower, self._hsv_upper)
        mask = cv2.bitwise_and(mask_ycrcb, mask_hsv)

        # If background reference is calibrated, keep only foreground pixels that differ from background
        if self._bg_reference_blur is not None and self._bg_reference_blur.shape == bgr.shape:
            cur_blur = cv2.GaussianBlur(bgr, (9, 9), 0)
            diff = cv2.absdiff(cur_blur, self._bg_reference_blur)
            diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, fg_mask = cv2.threshold(diff_gray, self._bg_diff_threshold, 255, cv2.THRESH_BINARY)
            fg_mask = cv2.dilate(fg_mask, self._kernel, iterations=2)
            mask = cv2.bitwise_and(mask, fg_mask)

        # Suppress detected face regions so the tracker focuses on the hand instead of the user's face
        if self._face_cascade is not None:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            faces = self._face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=4, minSize=(48, 48))
            for fx, fy, fw_box, fh_box in faces:
                pad_x = int(fw_box * 0.15)
                pad_y = int(fh_box * 0.25)
                x0 = max(0, fx - pad_x)
                y0 = max(0, fy - pad_y)
                x1 = min(w, fx + fw_box + pad_x)
                y1 = min(h, fy + fh_box + pad_y)
                mask[y0:y1, x0:x1] = 0

        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel, iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="opencv_2d", mask_preview=mask)

        largest = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(largest))
        area_ratio = area / frame_area
        if area_ratio < self._min_area_ratio or area_ratio > self._max_area_ratio:
            return HandDetectionResult(detected=False, depth_available=False, tracker_name="opencv_2d", mask_preview=mask)

        bx, by, bw, bh = cv2.boundingRect(largest)
        moments = cv2.moments(largest)
        if moments["m00"] > 0:
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
        else:
            cx = bx + bw / 2.0
            cy = by + bh / 2.0

        hull_pts = cv2.convexHull(largest, returnPoints=True)
        hull_area = float(cv2.contourArea(hull_pts))
        solidity = area / max(1.0, hull_area)

        # Convexity defects count deep valleys between spread fingers
        hull_indices = cv2.convexHull(largest, returnPoints=False)
        finger_valleys = 0
        tip_points: list[tuple[float, float]] = []

        if hull_indices is not None and len(hull_indices) > 3 and len(largest) > 3:
            try:
                defects = cv2.convexityDefects(largest, hull_indices)
            except cv2.error:
                defects = None

            if defects is not None:
                defects_2d = defects.reshape(-1, 4)
                min_depth_px = max(8.0, bh * 0.14)
                for row in defects_2d:
                    s_idx, e_idx, f_idx, depth_fixed = int(row[0]), int(row[1]), int(row[2]), int(row[3])
                    depth_px = depth_fixed / 256.0
                    start = tuple(largest[s_idx][0])
                    end = tuple(largest[e_idx][0])
                    far = tuple(largest[f_idx][0])

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

        # Deduplicate nearby fingertip candidates
        dedup_tips: list[Point2D] = []
        min_tip_sep = max(10.0, bw * 0.12)
        for tx, ty in tip_points:
            if all(math.hypot(tx - p.x * w, ty - p.y * h) > min_tip_sep for p in dedup_tips):
                dedup_tips.append(Point2D(tx / w, ty / h))

        aspect_ratio = float(bh) / max(1.0, float(bw))

        # Measure top-region width vs bounding width to distinguish pinched/pointing apex vs fist/palm
        top_band_y = by + max(4, int(bh * 0.16))
        top_pts = largest[largest[:, 0, 1] <= top_band_y]
        if len(top_pts) >= 2:
            top_min_x = float(np.min(top_pts[:, 0, 0]))
            top_max_x = float(np.max(top_pts[:, 0, 0]))
            top_span_ratio = (top_max_x - top_min_x) / max(1.0, float(bw))
        else:
            top_span_ratio = 0.5

        if finger_valleys > 0:
            extended_fingers = min(5, finger_valleys + 1)
        elif solidity >= 0.88 and aspect_ratio < 1.25:
            extended_fingers = 0  # Compact closed fist
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

        # Downsampled normalized contour for lightweight HUD drawing
        approx = cv2.approxPolyDP(largest, epsilon=max(2.0, 0.008 * cv2.arcLength(largest, True)), closed=True)
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
) -> HandDetector:
    """Select MediaPipeHandDetector if mediapipe & local model are available, else OpenCVHandDetector."""
    if prefer_mediapipe:
        candidate = Path(model_path) if model_path is not None else DEFAULT_MEDIAPIPE_MODEL_PATH
        if candidate.exists():
            try:
                return MediaPipeHandDetector(model_path=candidate)
            except Exception as exc:
                logger.warning("MediaPipeHandDetector unavailable (%s); falling back to OpenCVHandDetector.", exc)
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
        """Classify a single frame's HandDetectionResult before temporal smoothing."""
        if not hand.detected:
            return GestureType.NONE, 0.0

        # 1) Open palm (4-5 extended fingers) -> RECEIVE_PET
        if hand.extended_fingers >= self._receive_min_fingers and hand.openness_score >= self._receive_openness_threshold:
            conf = min(1.0, 0.5 * hand.confidence + 0.5 * hand.openness_score)
            return GestureType.RECEIVE_PET, conf

        # 2) MediaPipe 21-landmark high-precision rules
        if hand.tracker_name == "mediapipe" and hand.landmarks_21:
            # Closed fist has all fingers curled tightly into the palm (check before pinch
            # because thumb and curled index also sit close together in a fist).
            if hand.extended_fingers == 0 and hand.openness_score < 0.15:
                return GestureType.FIST, min(1.0, 0.65 * hand.confidence + 0.35 * (1.0 - hand.openness_score))
            if hand.pinch_ratio <= self._pinch_ratio_threshold:
                tightness = 1.0 - (hand.pinch_ratio / max(0.01, self._pinch_ratio_threshold))
                conf = min(1.0, 0.55 * hand.confidence + 0.45 * max(0.25, tightness))
                return GestureType.PINCH, conf
            if hand.extended_fingers in (1, 2):
                return GestureType.POINT, min(1.0, 0.70 * hand.confidence + 0.25)
            return GestureType.NONE, 0.0

        # 3) OpenCV 2D contour rules (zero-download offline fallback)
        # Compact closed fist: zero extended fingers, high solidity, roughly square
        if hand.extended_fingers == 0 and hand.solidity >= 0.88 and hand.aspect_ratio < 1.25:
            conf = min(1.0, 0.55 * hand.confidence + 0.45 * hand.solidity)
            return GestureType.FIST, conf

        # Tall single-finger pointing silhouette (aspect_ratio >= 2.0, very narrow tip)
        if hand.extended_fingers == 1 and hand.aspect_ratio >= 2.0 and hand.pinch_ratio <= 0.32:
            conf = min(1.0, 0.55 * hand.confidence + 0.35)
            return GestureType.POINT, conf

        # Pinch silhouette (compact upper apex, 1-2 fingers, not a tall single-finger point)
        if hand.extended_fingers <= 2 and hand.pinch_ratio <= self._pinch_ratio_threshold:
            tightness = 1.0 - (hand.pinch_ratio / max(0.01, self._pinch_ratio_threshold))
            closedness = 1.0 - hand.openness_score
            conf = min(1.0, 0.45 * hand.confidence + 0.30 * closedness + 0.25 * max(0.2, tightness))
            return GestureType.PINCH, conf

        return GestureType.NONE, 0.0

    def process_frame(self, frame: CameraFrame) -> GestureEvent:
        """Detect hand, smooth palm coordinates, compute velocity, and apply multi-frame dwell filter."""
        hand = self.detector.detect(frame)
        raw_gesture, raw_conf = self.classify_hand(hand)

        # Update smoothed palm & velocity
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
    """Enforces the safety rule: camera gestures ONLY confirm an incoming handoff.

    This controller NEVER initiates a handoff on its own. Without an active
    incoming `pending_handoff_id` from the GCS Local Hub, all camera gestures
    are safely ignored.
    """

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
        """Arm gesture confirmation for a specific incoming handoff offer."""
        self._pending_handoff_id = handoff_id
        self._offer_expires_at = expires_at
        self.recognizer.reset()
        logger.info("Camera gesture confirmation armed for incoming handoff '%s'", handoff_id)

    def disarm(self) -> None:
        """Clear any armed handoff offer (on completion, cancellation, or timeout)."""
        self._pending_handoff_id = None
        self._offer_expires_at = 0.0
        self.recognizer.reset()

    def process_event(self, event: GestureEvent) -> str | None:
        """Evaluate an already-computed GestureEvent against an armed incoming handoff."""
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
        """Inspect a camera frame; returns confirmed handoff_id ONLY if an offer is armed."""
        if self._pending_handoff_id is None:
            # Architectural invariant: camera NEVER initiates a handoff on its own.
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
) -> np.ndarray:
    """Render real-time hand skeleton/contour, palm crosshair, and gesture HUD onto a BGR frame."""
    hand = event.hand
    if show_mask and hand.mask_preview is not None:
        canvas = cv2.cvtColor(hand.mask_preview, cv2.COLOR_GRAY2BGR)
    else:
        canvas = frame_bgr.copy()

    h, w = canvas.shape[:2]

    if hand.detected:
        # Draw bounding box
        if hand.bounding_box is not None:
            bx, by, bw, bh = hand.bounding_box
            x0, y0 = int(bx * w), int(by * h)
            x1, y1 = int((bx + bw) * w), int((by + bh) * h)
            box_color = (80, 235, 130) if event.gesture != GestureType.NONE else (220, 190, 90)
            cv2.rectangle(canvas, (x0, y0), (x1, y1), box_color, 2)

        # Draw 21-point skeleton (MediaPipe) or contour polygon (OpenCV 2D)
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

        # Draw smoothed palm crosshair
        palm = event.smoothed_palm or hand.palm_center
        if palm is not None:
            px, py = int(palm.x * w), int(palm.y * h)
            cv2.circle(canvas, (px, py), 8, (86, 198, 224), 2)
            cv2.line(canvas, (px - 12, py), (px + 12, py), (86, 198, 224), 1)
            cv2.line(canvas, (px, py - 12), (px, py + 12), (86, 198, 224), 1)

    # Top telemetry bar
    cv2.rectangle(canvas, (0, 0), (w, 46), (20, 24, 36), -1)
    gesture_label = event.gesture.value.upper()
    header = f"{hand.tracker_name} | {fps:4.1f} FPS | Gesture: {gesture_label} ({int(event.confidence * 100)}%)"
    cv2.putText(canvas, header, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 244, 255), 1, cv2.LINE_AA)

    status_text = active_action or ("ARMED: Show Open Palm to Receive Pet" if handoff_armed else "Pinch=Grab | Point=Call | Palm=Wake | Fist=Sleep")
    status_color = (114, 245, 166) if handoff_armed else (160, 215, 245)
    cv2.putText(canvas, status_text, (8, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.42, status_color, 1, cv2.LINE_AA)
    return canvas
