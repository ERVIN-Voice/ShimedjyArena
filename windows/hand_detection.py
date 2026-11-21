"""2D RGB hand detection, gesture recognition (pinch & receive_pet), and
confirmation-only handoff safety gate for non-depth cameras (PS3 Eye, webcams, Android).
"""

from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import cv2
import numpy as np

from windows.camera import CameraFrame

logger = logging.getLogger(__name__)


class GestureType(str, Enum):
    """Supported camera hand gestures."""

    NONE = "none"
    PINCH = "pinch"
    RECEIVE_PET = "receive_pet"


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
    pinch_ratio: float = 1.0  # smaller -> tighter pinch between top two extremities
    depth_available: bool = False


@dataclass(frozen=True)
class GestureEvent:
    """Recognized gesture with confidence and underlying hand detection."""

    gesture: GestureType
    confidence: float
    timestamp: float
    hand: HandDetectionResult


class HandDetector(ABC):
    """Pluggable interface for 2D RGB hand detectors (OpenCV heuristic, MediaPipe, ONNX, etc.)."""

    @abstractmethod
    def detect(self, frame: CameraFrame) -> HandDetectionResult:
        """Analyze a single 2D BGR CameraFrame and return hand features."""


class OpenCVHandDetector(HandDetector):
    """Zero-cloud, non-depth 2D RGB hand detector using YCrCb skin mask & convex hull defects.

    Designed to work smoothly with PS3 Eye (60/120fps RGB), standard webcams,
    and Android RGB camera streams without requiring any depth channel.
    """

    def __init__(
        self,
        *,
        min_area_ratio: float = 0.02,
        max_area_ratio: float = 0.65,
        ycrcb_lower: tuple[int, int, int] = (0, 133, 77),
        ycrcb_upper: tuple[int, int, int] = (255, 173, 127),
    ) -> None:
        self._min_area_ratio = min_area_ratio
        self._max_area_ratio = max_area_ratio
        self._ycrcb_lower = np.array(ycrcb_lower, dtype=np.uint8)
        self._ycrcb_upper = np.array(ycrcb_upper, dtype=np.uint8)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    def detect(self, frame: CameraFrame) -> HandDetectionResult:
        bgr = frame.frame_bgr
        if bgr is None or bgr.size == 0 or bgr.ndim != 3:
            return HandDetectionResult(detected=False, depth_available=False)

        h, w = bgr.shape[:2]
        frame_area = float(max(1, w * h))

        ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, self._ycrcb_lower, self._ycrcb_upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel, iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return HandDetectionResult(detected=False, depth_available=False)

        largest = max(contours, key=cv2.contourArea)
        area = float(cv2.contourArea(largest))
        area_ratio = area / frame_area
        if area_ratio < self._min_area_ratio or area_ratio > self._max_area_ratio:
            return HandDetectionResult(detected=False, depth_available=False)

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
            if all(math.hypot(tx - px * w, ty - py * h) > min_tip_sep for px, py in [(p.x, p.y) for p in dedup_tips]):
                dedup_tips.append(Point2D(tx / w, ty / h))

        # Estimate extended fingers & openness
        extended_fingers = min(5, finger_valleys + 1) if finger_valleys > 0 else 1

        # Measure top-region width vs bounding width to distinguish pinched tips vs spread palm
        top_band_y = by + max(4, int(bh * 0.16))
        top_pts = largest[largest[:, 0, 1] <= top_band_y]
        if len(top_pts) >= 2:
            top_min_x = float(np.min(top_pts[:, 0, 0]))
            top_max_x = float(np.max(top_pts[:, 0, 0]))
            top_span_ratio = (top_max_x - top_min_x) / max(1.0, float(bw))
        else:
            top_span_ratio = 0.5

        # Lower solidity (more space between fingers) + >=3 valleys => high openness
        openness_score = min(
            1.0,
            max(
                0.0,
                0.55 * min(1.0, finger_valleys / 3.0)
                + 0.25 * max(0.0, (0.90 - solidity) / 0.35)
                + 0.20 * min(1.0, top_span_ratio / 0.75),
            ),
        )

        # Compact top apex + small number of valleys => pinch silhouette
        pinch_ratio = min(1.0, max(0.0, top_span_ratio))
        confidence = min(1.0, max(0.35, 0.5 + min(0.5, area_ratio * 4.0)))

        return HandDetectionResult(
            detected=True,
            confidence=confidence,
            palm_center=Point2D(cx / w, cy / h),
            bounding_box=(bx / w, by / h, bw / w, bh / h),
            finger_tips=tuple(dedup_tips),
            extended_fingers=extended_fingers,
            openness_score=openness_score,
            pinch_ratio=pinch_ratio,
            depth_available=False,
        )


class GestureRecognizer:
    """Temporal gesture classifier with frame-dwell filtering for PINCH and RECEIVE_PET."""

    def __init__(
        self,
        detector: HandDetector | None = None,
        *,
        hold_frames: int = 3,
        receive_openness_threshold: float = 0.62,
        receive_min_fingers: int = 4,
        pinch_ratio_threshold: float = 0.38,
    ) -> None:
        self.detector = detector or OpenCVHandDetector()
        self._hold_frames = max(1, int(hold_frames))
        self._receive_openness_threshold = receive_openness_threshold
        self._receive_min_fingers = receive_min_fingers
        self._pinch_ratio_threshold = pinch_ratio_threshold

        self._candidate_gesture: GestureType = GestureType.NONE
        self._streak: int = 0

    def reset(self) -> None:
        self._candidate_gesture = GestureType.NONE
        self._streak = 0

    def classify_hand(self, hand: HandDetectionResult) -> tuple[GestureType, float]:
        """Classify a single frame's HandDetectionResult before temporal smoothing."""
        if not hand.detected:
            return GestureType.NONE, 0.0

        if hand.extended_fingers >= self._receive_min_fingers and hand.openness_score >= self._receive_openness_threshold:
            conf = min(1.0, 0.5 * hand.confidence + 0.5 * hand.openness_score)
            return GestureType.RECEIVE_PET, conf

        if hand.extended_fingers <= 2 and hand.pinch_ratio <= self._pinch_ratio_threshold:
            tightness = 1.0 - (hand.pinch_ratio / max(0.01, self._pinch_ratio_threshold))
            closedness = 1.0 - hand.openness_score
            conf = min(1.0, 0.45 * hand.confidence + 0.30 * closedness + 0.25 * max(0.2, tightness))
            return GestureType.PINCH, conf

        return GestureType.NONE, 0.0

    def process_frame(self, frame: CameraFrame) -> GestureEvent:
        """Detect hand and apply multi-frame dwell filtering to suppress transient noise."""
        hand = self.detector.detect(frame)
        raw_gesture, raw_conf = self.classify_hand(hand)

        if raw_gesture == GestureType.NONE:
            self._candidate_gesture = GestureType.NONE
            self._streak = 0
            return GestureEvent(GestureType.NONE, 0.0, frame.timestamp, hand)

        if raw_gesture == self._candidate_gesture:
            self._streak += 1
        else:
            self._candidate_gesture = raw_gesture
            self._streak = 1

        if self._streak >= self._hold_frames:
            return GestureEvent(raw_gesture, raw_conf, frame.timestamp, hand)

        return GestureEvent(GestureType.NONE, 0.0, frame.timestamp, hand)


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

    def process_frame(self, frame: CameraFrame) -> str | None:
        """Inspect a camera frame; returns confirmed handoff_id ONLY if an offer is armed."""
        if self._pending_handoff_id is None:
            # Architectural invariant: camera NEVER initiates a handoff on its own.
            return None

        if time.time() > self._offer_expires_at:
            self.disarm()
            return None

        event = self.recognizer.process_frame(frame)
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
