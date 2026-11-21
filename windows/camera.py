"""Camera capture interfaces and implementations for GCS Shimeji gesture confirmation.

Supports:
- Sony PlayStation 3 Eye (explicitly non-depth 2D RGB high-FPS camera)
- Standard OpenCV webcams (2D RGB)
- Android camera frame stream adapter (2D RGB)
- Synthetic camera for deterministic automated testing
"""

from __future__ import annotations

import logging
import queue
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraCapabilities:
    """Declares physical capabilities of a camera source."""

    device_kind: str  # "ps3_eye" | "webcam" | "android" | "synthetic"
    width: int
    height: int
    fps: float
    is_depth_camera: bool = False  # Explicitly False for PS3 Eye, standard webcams, Android RGB
    supports_manual_exposure: bool = False
    supports_high_fps: bool = False


@dataclass(frozen=True)
class CameraFrame:
    """Single captured 2D BGR frame with metadata."""

    frame_bgr: np.ndarray
    timestamp: float
    frame_index: int
    source_kind: str
    is_depth: bool = False


class CameraBackend(ABC):
    """Abstract interface for all camera capture sources."""

    @property
    @abstractmethod
    def capabilities(self) -> CameraCapabilities:
        """Describe resolution, FPS, and depth capability."""

    @property
    @abstractmethod
    def is_opened(self) -> bool:
        """Return True if the camera stream is open and ready to read."""

    @abstractmethod
    def open(self) -> bool:
        """Initialize and open the camera stream."""

    @abstractmethod
    def read(self) -> CameraFrame | None:
        """Capture the next BGR frame or return None if unavailable."""

    @abstractmethod
    def close(self) -> None:
        """Release underlying camera resources."""

    def __enter__(self) -> CameraBackend:
        self.open()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


class OpenCVCamera(CameraBackend):
    """Standard 2D RGB webcam backend backed by OpenCV VideoCapture."""

    def __init__(
        self,
        device_index: int = 0,
        *,
        width: int = 640,
        height: int = 480,
        fps: float = 30.0,
        mirror: bool = True,
        backend_api: int | None = None,
        device_kind: str = "webcam",
    ) -> None:
        self._device_index = int(device_index)
        self._width = int(width)
        self._height = int(height)
        self._fps = float(fps)
        self._mirror = mirror
        self._backend_api = backend_api
        self._device_kind = device_kind
        self._capture: cv2.VideoCapture | None = None
        self._frame_index: int = 0

    @property
    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            device_kind=self._device_kind,
            width=self._width,
            height=self._height,
            fps=self._fps,
            is_depth_camera=False,
            supports_manual_exposure=False,
            supports_high_fps=False,
        )

    @property
    def is_opened(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def open(self) -> bool:
        if self.is_opened:
            return True
        if self._backend_api is not None:
            cap = cv2.VideoCapture(self._device_index, self._backend_api)
        else:
            cap = cv2.VideoCapture(self._device_index)

        if not cap.isOpened():
            cap.release()
            logger.warning("Failed to open %s at index %d", self._device_kind, self._device_index)
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._height))
        cap.set(cv2.CAP_PROP_FPS, float(self._fps))
        self._configure_capture(cap)
        self._capture = cap
        self._frame_index = 0
        logger.info(
            "Opened %s (index=%d, requested=%dx%d@%.0ffps, depth=False)",
            self._device_kind,
            self._device_index,
            self._width,
            self._height,
            self._fps,
        )
        return True

    def _configure_capture(self, cap: cv2.VideoCapture) -> None:
        """Hook for subclasses (e.g., PS3 Eye manual exposure/gain tuning)."""

    def read(self) -> CameraFrame | None:
        if not self.is_opened or self._capture is None:
            return None
        ok, frame = self._capture.read()
        if not ok or frame is None:
            return None
        if self._mirror:
            frame = cv2.flip(frame, 1)
        self._frame_index += 1
        return CameraFrame(
            frame_bgr=frame,
            timestamp=time.time(),
            frame_index=self._frame_index,
            source_kind=self._device_kind,
            is_depth=False,
        )

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class PS3EyeCamera(OpenCVCamera):
    """Sony PlayStation 3 Eye adapter.

    Important architectural note:
    The PS3 Eye is a high-framerate 2D RGB camera (NOT a depth / RGB-D sensor).
    It excels at low-latency 60fps (640x480) or 120fps (320x240) motion/hand capture
    when auto-exposure is locked down to minimize motion blur.
    """

    SUPPORTED_MODES: dict[str, tuple[int, int, float]] = {
        "vga_60": (640, 480, 60.0),
        "qvga_120": (320, 240, 120.0),
        "vga_30": (640, 480, 30.0),
    }

    def __init__(
        self,
        device_index: int = 0,
        *,
        mode: Literal["vga_60", "qvga_120", "vga_30"] = "vga_60",
        manual_exposure: float | None = -5.0,
        gain: float | None = 20.0,
        mirror: bool = True,
        backend_api: int | None = None,
    ) -> None:
        if mode not in self.SUPPORTED_MODES:
            raise ValueError(f"Unsupported PS3 Eye mode '{mode}'. Choose from {sorted(self.SUPPORTED_MODES)}.")
        width, height, fps = self.SUPPORTED_MODES[mode]
        self._mode = mode
        self._manual_exposure = manual_exposure
        self._gain = gain
        super().__init__(
            device_index=device_index,
            width=width,
            height=height,
            fps=fps,
            mirror=mirror,
            backend_api=backend_api,
            device_kind="ps3_eye",
        )

    @property
    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            device_kind="ps3_eye",
            width=self._width,
            height=self._height,
            fps=self._fps,
            is_depth_camera=False,  # PS3 Eye has NO depth sensor
            supports_manual_exposure=True,
            supports_high_fps=True,
        )

    def _configure_capture(self, cap: cv2.VideoCapture) -> None:
        # Disable auto-exposure & auto-gain where driver supports it to prevent motion blur
        if self._manual_exposure is not None:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            cap.set(cv2.CAP_PROP_EXPOSURE, float(self._manual_exposure))
        if self._gain is not None:
            cap.set(cv2.CAP_PROP_GAIN, float(self._gain))


class AndroidCameraStreamAdapter(CameraBackend):
    """Receives 2D RGB frames (JPEG bytes or BGR arrays) pushed from an Android camera."""

    def __init__(self, *, width: int = 480, height: int = 640, fps: float = 30.0, max_queue: int = 4) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._opened = False
        self._queue: queue.Queue[CameraFrame] = queue.Queue(maxsize=max_queue)
        self._frame_index = 0

    @property
    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            device_kind="android",
            width=self._width,
            height=self._height,
            fps=self._fps,
            is_depth_camera=False,
            supports_manual_exposure=False,
            supports_high_fps=False,
        )

    @property
    def is_opened(self) -> bool:
        return self._opened

    def open(self) -> bool:
        self._opened = True
        return True

    def push_jpeg_frame(self, jpeg_bytes: bytes, timestamp: float | None = None) -> bool:
        """Decode and enqueue an incoming JPEG frame from the Android camera."""
        if not self._opened:
            return False
        buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame_bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame_bgr is None:
            return False
        return self.push_bgr_frame(frame_bgr, timestamp=timestamp)

    def push_bgr_frame(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> bool:
        if not self._opened:
            return False
        self._frame_index += 1
        item = CameraFrame(
            frame_bgr=frame_bgr,
            timestamp=timestamp if timestamp is not None else time.time(),
            frame_index=self._frame_index,
            source_kind="android",
            is_depth=False,
        )
        # Drop oldest frame if consumer is slower than producer to stay low-latency
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
        self._queue.put_nowait(item)
        return True

    def read(self) -> CameraFrame | None:
        if not self._opened:
            return None
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self._opened = False
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


class SyntheticCamera(CameraBackend):
    """Procedural 2D RGB camera generating realistic test silhouettes for automated tests."""

    def __init__(self, *, width: int = 320, height: int = 240, fps: float = 60.0) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._opened = False
        self._frame_index = 0
        self._scene: Literal["empty", "open_palm", "pinch"] = "empty"

    @property
    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            device_kind="synthetic",
            width=self._width,
            height=self._height,
            fps=self._fps,
            is_depth_camera=False,
            supports_manual_exposure=True,
            supports_high_fps=True,
        )

    @property
    def is_opened(self) -> bool:
        return self._opened

    def set_scene(self, scene: Literal["empty", "open_palm", "pinch"]) -> None:
        self._scene = scene

    def open(self) -> bool:
        self._opened = True
        self._frame_index = 0
        return True

    def read(self) -> CameraFrame | None:
        if not self._opened:
            return None
        # Dark blue-gray studio background (non-skin in YCrCb)
        img = np.full((self._height, self._width, 3), (45, 32, 24), dtype=np.uint8)
        # Warm skin tone in BGR that cleanly falls in standard YCrCb skin bounds
        skin_bgr = (128, 168, 224)

        cx = self._width // 2
        cy = int(self._height * 0.62)

        if self._scene == "open_palm":
            # Palm circle + 5 radially spread fingers creating 4 clear convexity defects
            cv2.circle(img, (cx, cy), 30, skin_bgr, thickness=-1)
            finger_specs = [
                ((-22, -4), (-64, -30)),   # thumb
                ((-18, -22), (-42, -76)),  # index
                ((0, -26), (0, -86)),      # middle
                ((18, -22), (42, -76)),    # ring
                ((22, -6), (62, -40)),     # pinky
            ]
            for (sx, sy), (ex, ey) in finger_specs:
                cv2.line(img, (cx + sx, cy + sy), (cx + ex, cy + ey), skin_bgr, thickness=9)
                cv2.circle(img, (cx + ex, cy + ey), 5, skin_bgr, thickness=-1)
        elif self._scene == "pinch":
            # Compact palm with thumb and index pinched together into a narrow top apex
            cv2.circle(img, (cx, cy + 8), 30, skin_bgr, thickness=-1)
            cv2.line(img, (cx - 14, cy - 6), (cx - 3, cy - 52), skin_bgr, thickness=9)
            cv2.line(img, (cx + 14, cy - 6), (cx + 3, cy - 52), skin_bgr, thickness=9)
            cv2.circle(img, (cx, cy - 54), 6, skin_bgr, thickness=-1)

        self._frame_index += 1
        return CameraFrame(
            frame_bgr=img,
            timestamp=time.time(),
            frame_index=self._frame_index,
            source_kind="synthetic",
            is_depth=False,
        )

    def close(self) -> None:
        self._opened = False


def create_camera(
    kind: Literal["webcam", "ps3_eye", "android", "synthetic"] = "webcam",
    *,
    device_index: int = 0,
    ps3_mode: Literal["vga_60", "qvga_120", "vga_30"] = "vga_60",
) -> CameraBackend:
    """Factory creating the requested non-depth 2D camera backend."""
    if kind == "ps3_eye":
        return PS3EyeCamera(device_index=device_index, mode=ps3_mode)
    if kind == "webcam":
        return OpenCVCamera(device_index=device_index)
    if kind == "android":
        return AndroidCameraStreamAdapter()
    if kind == "synthetic":
        return SyntheticCamera()
    raise ValueError(f"Unsupported camera kind: '{kind}'")
