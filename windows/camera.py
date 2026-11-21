"""Camera capture interfaces and implementations for GCS Shimeji gesture control.

Supports:
- Sony PlayStation 3 Eye (explicitly non-depth 2D RGB high-FPS camera, CAP_DSHOW priority on Windows)
- Standard OpenCV webcams (2D RGB)
- Android camera frame stream adapter (2D RGB)
- Synthetic camera for deterministic automated testing
- ThreadedCameraReader for non-blocking background frame capture
"""

from __future__ import annotations

import logging
import platform
import queue
import threading
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


def _preferred_windows_backends(explicit_api: int | None) -> list[int | None]:
    """Order OpenCV capture APIs cleanly; DirectShow is much more reliable for PS3 Eye on Windows."""
    if explicit_api is not None:
        return [explicit_api]
    if platform.system() == "Windows":
        backends: list[int | None] = []
        if hasattr(cv2, "CAP_DSHOW"):
            backends.append(int(cv2.CAP_DSHOW))
        if hasattr(cv2, "CAP_MSMF"):
            backends.append(int(cv2.CAP_MSMF))
        backends.append(None)
        return backends
    return [None]


def probe_cameras(max_index: int = 5) -> list[int]:
    """Probe local OpenCV camera indices that successfully open and return a frame."""
    available: list[int] = []
    for idx in range(max(1, max_index)):
        for api in _preferred_windows_backends(None):
            cap = cv2.VideoCapture(idx, api) if api is not None else cv2.VideoCapture(idx)
            try:
                if cap.isOpened():
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.size > 0:
                        available.append(idx)
                        break
            finally:
                cap.release()
    return available


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
    def device_index(self) -> int:
        return self._device_index

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

        for api in _preferred_windows_backends(self._backend_api):
            cap = cv2.VideoCapture(self._device_index, api) if api is not None else cv2.VideoCapture(self._device_index)
            if not cap.isOpened():
                cap.release()
                continue

            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._height))
            cap.set(cv2.CAP_PROP_FPS, float(self._fps))
            self._configure_capture(cap)

            # Verify a real frame can be grabbed (guards against ghost virtual cameras)
            ok, test_frame = cap.read()
            if not ok or test_frame is None or test_frame.size == 0:
                cap.release()
                continue

            self._capture = cap
            self._frame_index = 0
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or test_frame.shape[1])
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or test_frame.shape[0])
            actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or self._fps)
            logger.info(
                "Opened %s (index=%d, api=%s, actual=%dx%d@%.0ffps, depth=False)",
                self._device_kind,
                self._device_index,
                api,
                actual_w,
                actual_h,
                actual_fps,
            )
            return True

        logger.warning("Failed to open %s at index %d", self._device_kind, self._device_index)
        return False

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
    via DirectShow (CL-Eye / libusb-win32 / open-source PS3 Eye drivers on Windows).
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
        manual_exposure: float | None = None,
        gain: float | None = None,
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
    def mode(self) -> str:
        return self._mode

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
        # Leave auto-exposure enabled by default so rooms of any brightness work out-of-the-box;
        # apply manual overrides only if explicitly requested.
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


SyntheticScene = Literal["empty", "open_palm", "pinch", "point", "fist"]


class SyntheticCamera(CameraBackend):
    """Procedural 2D RGB camera generating realistic test silhouettes for automated tests."""

    def __init__(self, *, width: int = 320, height: int = 240, fps: float = 60.0) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._opened = False
        self._frame_index = 0
        self._scene: SyntheticScene = "empty"
        self._hand_nx: float = 0.5
        self._hand_ny: float = 0.62

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

    def set_scene(
        self,
        scene: SyntheticScene,
        *,
        hand_nx: float | None = None,
        hand_ny: float | None = None,
    ) -> None:
        self._scene = scene
        if hand_nx is not None:
            self._hand_nx = min(0.85, max(0.15, float(hand_nx)))
        if hand_ny is not None:
            self._hand_ny = min(0.85, max(0.25, float(hand_ny)))

    def open(self) -> bool:
        self._opened = True
        self._frame_index = 0
        return True

    def read(self) -> CameraFrame | None:
        if not self._opened:
            return None
        img = np.full((self._height, self._width, 3), (45, 32, 24), dtype=np.uint8)
        skin_bgr = (128, 168, 224)

        cx = int(self._width * self._hand_nx)
        cy = int(self._height * self._hand_ny)

        if self._scene == "open_palm":
            cv2.circle(img, (cx, cy), 30, skin_bgr, thickness=-1)
            finger_specs = [
                ((-22, -4), (-64, -30)),
                ((-18, -22), (-42, -76)),
                ((0, -26), (0, -86)),
                ((18, -22), (42, -76)),
                ((22, -6), (62, -40)),
            ]
            for (sx, sy), (ex, ey) in finger_specs:
                cv2.line(img, (cx + sx, cy + sy), (cx + ex, cy + ey), skin_bgr, thickness=9)
                cv2.circle(img, (cx + ex, cy + ey), 5, skin_bgr, thickness=-1)
        elif self._scene == "pinch":
            cv2.circle(img, (cx, cy + 8), 30, skin_bgr, thickness=-1)
            cv2.line(img, (cx - 14, cy - 6), (cx - 3, cy - 52), skin_bgr, thickness=9)
            cv2.line(img, (cx + 14, cy - 6), (cx + 3, cy - 52), skin_bgr, thickness=9)
            cv2.circle(img, (cx, cy - 54), 6, skin_bgr, thickness=-1)
        elif self._scene == "point":
            # Palm + single tall straight index finger pointing upward
            cv2.circle(img, (cx, cy + 12), 26, skin_bgr, thickness=-1)
            cv2.line(img, (cx, cy - 10), (cx, cy - 84), skin_bgr, thickness=10)
            cv2.circle(img, (cx, cy - 86), 5, skin_bgr, thickness=-1)
        elif self._scene == "fist":
            # Compact closed fist (high solidity, near-square aspect ratio, no extended fingers)
            cv2.ellipse(img, (cx, cy), (34, 30), 0, 0, 360, skin_bgr, thickness=-1)

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


class ThreadedCameraReader:
    """Reads frames from any CameraBackend on a dedicated daemon thread so Qt never blocks."""

    def __init__(self, camera: CameraBackend, *, max_poll_hz: float = 60.0) -> None:
        self._camera = camera
        self._min_interval = 1.0 / max(1.0, float(max_poll_hz))
        self._lock = threading.Lock()
        self._latest_frame: CameraFrame | None = None
        self._latest_seq: int = 0
        self._fps: float = 0.0
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def camera(self) -> CameraBackend:
        return self._camera

    @property
    def is_running(self) -> bool:
        return self._running and self._camera.is_opened

    @property
    def fps(self) -> float:
        with self._lock:
            return self._fps

    def start(self) -> bool:
        if self._running:
            return True
        if not self._camera.is_opened and not self._camera.open():
            return False
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="gcs-camera-reader", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        self._camera.close()

    def get_latest(self, last_seen_seq: int = 0) -> tuple[CameraFrame | None, int]:
        """Return `(frame, seq)` if a newer frame arrived since `last_seen_seq`, else `(None, seq)`."""
        with self._lock:
            if self._latest_frame is None or self._latest_seq <= last_seen_seq:
                return None, self._latest_seq
            return self._latest_frame, self._latest_seq

    def _loop(self) -> None:
        fps_frames = 0
        fps_window_start = time.monotonic()
        while self._running:
            t0 = time.monotonic()
            frame = self._camera.read()
            if frame is not None:
                fps_frames += 1
                now = time.monotonic()
                elapsed_win = now - fps_window_start
                new_fps: float | None = None
                if elapsed_win >= 0.5:
                    new_fps = fps_frames / elapsed_win
                    fps_frames = 0
                    fps_window_start = now
                with self._lock:
                    self._latest_frame = frame
                    self._latest_seq += 1
                    if new_fps is not None:
                        self._fps = new_fps
            else:
                time.sleep(0.01)
                continue

            elapsed = time.monotonic() - t0
            sleep_for = self._min_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)


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
