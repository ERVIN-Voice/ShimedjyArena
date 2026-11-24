"""High-level Windows Desktop Pet coordinator.

Connects:
- Stage 1: PetEngine + AnimationController + TransparentPetWindow (frameless, pixel-perfect, multi-monitor)
- Stage 2 & 4: GCS Local Hub WebSocket client + two-phase cross-device handoff UI & edge drag
- Stage 6: Real-time 2D RGB camera gesture control (pinch-drag, point-call, palm-wake/jump, fist-sleep,
           and confirmation-only incoming handoff acceptance) + live Camera & Gestures HUD
"""

from __future__ import annotations

import logging
import math
import random
import time
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout

from server.hub import build_pairing_uri, detect_local_ip, render_pairing_qr_ascii, save_pairing_qr_png
from shared.config_loader import AvatarConfig, get_user_data_dir, load_avatar_config, load_protocol_config
from windows.animations import AnimationController
from windows.camera import CameraBackend, CameraFrame, PS3EyeCamera, ThreadedCameraReader, create_camera
from windows.camera_preview import CameraPreviewWindow
from windows.gesture_controller import GesturePetController
from windows.hand_detection import (
    GestureRecognizer,
    HandoffGestureConfirmationController,
    create_best_hand_detector,
    draw_hand_overlay,
    get_last_mediapipe_status,
)
from windows.network_client import EmbeddedHubRunner, HubClientEvent, WindowsHubClient
from windows.pet_engine import EdgeHandoffEvent, PetEngine
from windows.pet_profile import PetProfile
from windows.site_reactor import (
    SiteActivity,
    SiteReactor,
    detect_foreground_window_title,
)
from windows.toy_physics import (
    SNACK_KINDS,
    BalloonPopGame,
    FetchBallState,
    PettingStrokeDetector,
    SnackDropState,
)
from windows.toy_windows import (
    BalloonToyWindow,
    BallToyWindow,
    LaserDotWindow,
    PetStatusDialog,
    PetThoughtBubble,
    SnackToyWindow,
)
from windows.transparent_window import (
    HandoffPromptOverlay,
    TransparentPetWindow,
    pil_frame_to_pixmap,
    query_qt_desktop_bounds,
)

logger = logging.getLogger(__name__)


class PairingDialog(QDialog):
    """Displays local LAN WebSocket URL (for Shimeji Hand Cam Android app), Web URL, and QR code."""

    def __init__(self, pairing_uri: str, web_url: str, qr_png_path: Path, ws_url: str = "") -> None:
        super().__init__(None)
        self.setWindowTitle("GCS Shimeji — Connect Android")
        layout = QVBoxLayout(self)

        title = QLabel("Connect Android Shimeji Hand Cam app (auto-discovered via Wi-Fi, or enter Hub WS URL):")
        ws_lbl = QLabel(f"Android App Hub URL:  {ws_url or pairing_uri}")
        ws_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #0b6e4f;")
        ws_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        web_lbl = QLabel(f"Browser Web Client :  {web_url}")
        web_lbl.setStyleSheet("font-size: 12px; color: #2b5b84;")
        web_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        uri_lbl = QLabel(f"Pairing URI:  {pairing_uri}")
        uri_lbl.setWordWrap(True)
        uri_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        qr_lbl = QLabel()
        pix = QPixmap(str(qr_png_path))
        if not pix.isNull():
            qr_lbl.setPixmap(pix)

        layout.addWidget(title)
        layout.addWidget(ws_lbl)
        layout.addWidget(web_lbl)
        layout.addWidget(qr_lbl)
        layout.addWidget(uri_lbl)
        self.adjustSize()


class DesktopPetApp(QObject):
    """Coordinates the Windows desktop pet window, physics, Hub sync, handoff, and live gestures."""

    def __init__(
        self,
        qt_app: QApplication,
        *,
        avatar_config: AvatarConfig | None = None,
        scale: int | None = None,
        standalone: bool = False,
        hub_url: str | None = None,
        hub_port: int = 8765,
        enable_mdns: bool = True,
        camera: CameraBackend | None = None,
        show_camera_preview: bool = True,
        hand_model_path: Path | None = None,
        auto_download_hand_model: bool = True,
    ) -> None:
        super().__init__()
        self.qt_app = qt_app
        self.config = avatar_config or load_avatar_config()
        self.protocol = load_protocol_config()
        self.standalone = standalone
        self.hub_port = hub_port

        self._anim = AnimationController(
            self.config,
            scale=scale,
            on_animation_complete=self._on_animation_complete,
        )
        bounds = query_qt_desktop_bounds()
        fw, fh = self._anim.frame_size

        self._engine = PetEngine(
            self.config,
            bounds,
            pet_width=fw,
            pet_height=fh,
            on_edge_handoff=self._on_edge_handoff,
        )
        saved = self._engine.load_saved_position()
        if saved and isinstance(saved.get("scale"), int):
            self._anim.set_scale(int(saved["scale"]))
            self._engine.update_pet_size(*self._anim.frame_size)

        # Transparent click-through window
        self._window = TransparentPetWindow(*self._anim.frame_size)
        self._window.drag_started.connect(self._on_drag_started)
        self._window.drag_moved.connect(self._engine.update_drag)
        self._window.drag_ended.connect(self._on_drag_ended)
        self._window.double_clicked.connect(self._engine.trigger_jump)
        self._window.pet_hovered.connect(self._on_pet_hovered)
        self._window.request_handoff.connect(self._trigger_manual_handoff)
        self._window.request_scale_delta.connect(self._adjust_scale)
        self._window.request_toggle_sleep.connect(self._toggle_sleep)
        self._window.request_pet_love.connect(self._pet_with_love)
        self._window.request_set_emotion.connect(self._set_manual_emotion)
        self._window.request_show_pairing.connect(self.show_pairing_dialog)
        self._window.request_toggle_camera_hud.connect(self.toggle_camera_preview)
        self._window.request_enable_camera.connect(self._enable_camera_runtime)
        self._window.request_play_ball.connect(self._toggle_fetch_ball)
        self._window.request_drop_snack.connect(self._drop_snack)
        self._window.request_toggle_laser.connect(self._toggle_laser_chase)
        self._window.request_start_balloons.connect(self._toggle_balloon_game)
        self._window.request_dance.connect(self._start_dance)
        self._window.request_toggle_site_reactions.connect(self._toggle_site_reactions)
        self._window.request_preview_site_prop.connect(self._preview_site_prop)
        self._window.request_show_profile.connect(self._show_pet_profile)
        self._window.request_toggle_break_reminder.connect(self._toggle_break_reminder)
        self._window.request_quit.connect(self.shutdown)

        # Website reactions, cozy companion profile, thought bubble & interactive desktop games
        self._fun_rng = random.Random()
        self._site_reactor = SiteReactor(enabled=True, debounce_polls=2, quip_interval_sec=28.0)
        self._last_site_poll: float = 0.0
        self._active_prop: str = ""
        self._profile = PetProfile.load()
        self._petting_detector = PettingStrokeDetector()
        self._thought_bubble = PetThoughtBubble()

        self._ball_state: FetchBallState | None = None
        self._ball_win: BallToyWindow | None = None
        self._snack_state: SnackDropState | None = None
        self._snack_win: SnackToyWindow | None = None
        self._balloon_game = BalloonPopGame()
        self._balloon_wins: dict[int, BalloonToyWindow] = {}
        self._laser_active: bool = False
        self._laser_win: LaserDotWindow | None = None
        self._laser_catch_cd: float = 0.0
        self._dance_steps_left: int = 0
        self._dance_timer = QTimer(self)
        self._dance_timer.setInterval(480)
        self._dance_timer.timeout.connect(self._on_dance_step)

        self._break_reminder_enabled: bool = False
        self._last_break_reminder_t: float = time.monotonic()

        # Incoming handoff prompt overlay
        self._handoff_overlay = HandoffPromptOverlay(
            on_accept=self._confirm_incoming_handoff,
            on_reject=self._decline_incoming_handoff,
        )

        # Multi-monitor topology change listeners
        self.qt_app.screenAdded.connect(self._refresh_monitors)
        self.qt_app.screenRemoved.connect(self._refresh_monitors)

        # Network & embedded Hub
        self._embedded_hub: EmbeddedHubRunner | None = None
        self._ws_client: WindowsHubClient | None = None
        self._peer_devices: list[dict[str, Any]] = []
        self._incoming_offer: dict[str, Any] | None = None
        self._last_sync_sent: float = 0.0
        self._last_save_time: float = time.monotonic()

        # Real-time camera capture & gesture control pipeline
        self._camera_reader: ThreadedCameraReader | None = None
        self._camera_kind: str = camera.capabilities.device_kind if camera is not None else "none"
        self._ps3_mode: Literal["vga_60", "qvga_120", "vga_30"] = (
            camera.mode if isinstance(camera, PS3EyeCamera) else "vga_60"  # type: ignore[assignment]
        )
        self._last_camera_seq: int = 0
        self._last_camera_frame: CameraFrame | None = None
        self._last_preview_draw: float = 0.0
        self._camera_preview: CameraPreviewWindow | None = None
        self._hand_model_path: Path | None = hand_model_path
        self._auto_download_hand_model: bool = auto_download_hand_model

        self._hand_detector = create_best_hand_detector(
            model_path=hand_model_path,
            auto_download_model=(auto_download_hand_model and camera is not None),
        )
        self._gesture_recognizer = GestureRecognizer(detector=self._hand_detector, hold_frames=3)
        self._gesture_confirm = HandoffGestureConfirmationController(
            recognizer=self._gesture_recognizer,
            on_confirm_handoff=self._confirm_incoming_handoff,
        )
        self._gesture_controller = GesturePetController(
            self._engine,
            self._gesture_recognizer,
            self._gesture_confirm,
            on_pet_state_changed=lambda: self._push_state_to_hub(force=True),
        )

        if camera is not None:
            self._attach_camera_backend(camera, show_preview=show_camera_preview)

        if not self.standalone:
            resolved_url = hub_url
            if resolved_url is None:
                self._embedded_hub = EmbeddedHubRunner(
                    host="0.0.0.0",
                    port=self.hub_port,
                    handoff_timeout_seconds=self.protocol.handoff_default_timeout_seconds,
                    enable_mdns=enable_mdns,
                )
                self._embedded_hub.start()
                resolved_url = self._embedded_hub.hub.ws_url
                web_url = self._embedded_hub.hub.web_url
                uri = self._embedded_hub.hub.pairing_uri
                logger.info("Android Web Client URL: %s", web_url)
                logger.info("Android Pairing URI   : %s", uri)
                logger.info("\n%s", render_pairing_qr_ascii(web_url))

            primary = bounds.primary_screen
            capabilities = ["desktop_pet", "mouse_drag", "click_confirm"]
            if self._camera_reader is not None:
                capabilities.extend(["camera_gesture_control", "camera_gesture_confirm"])

            self._ws_client = WindowsHubClient(
                resolved_url,
                screen_width=primary.width,
                screen_height=primary.height,
                capabilities=capabilities,
                protocol=self.protocol,
            )
            self._ws_client.start_background()

        # System tray icon
        self._tray: QSystemTrayIcon | None = self._create_tray_icon()

        # Main 60Hz animation/physics loop
        self._last_tick = time.monotonic()
        interval_ms = max(8, int(round(1000.0 / max(15, self.config.behavior.tick_hz))))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(interval_ms)

        # Render initial frame and show
        self._render_current_frame()
        self._window.move_to(self._engine.x, self._engine.y)
        self._window.show()

    def _create_tray_icon(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon = QIcon(pil_frame_to_pixmap(self._anim.current_frame()))
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("GCS Shimeji — Pixel Desktop Companion")

        menu = QMenu()
        games_tray = menu.addMenu("🎮 Игры с Чубриком")
        act_t_ball = QAction("⚽ Бросить / убрать мячик", games_tray)
        act_t_ball.triggered.connect(self._toggle_fetch_ball)
        games_tray.addAction(act_t_ball)

        act_t_snack = QAction("🍪 Бросить вкусняшку", games_tray)
        act_t_snack.triggered.connect(self._drop_snack)
        games_tray.addAction(act_t_snack)

        act_t_laser = QAction("🔴 Лазерная указка (Погоня)", games_tray)
        act_t_laser.triggered.connect(self._toggle_laser_chase)
        games_tray.addAction(act_t_laser)

        act_t_balloons = QAction("🎈 Лопать шарики (25с)", games_tray)
        act_t_balloons.triggered.connect(self._toggle_balloon_game)
        games_tray.addAction(act_t_balloons)

        act_t_dance = QAction("💃 Станцевать!", games_tray)
        act_t_dance.triggered.connect(self._start_dance)
        games_tray.addAction(act_t_dance)

        act_t_prof = QAction("⭐ Профиль и настроение", menu)
        act_t_prof.triggered.connect(self._show_pet_profile)
        menu.addAction(act_t_prof)

        act_t_sites = QAction("🌐 Вкл/Выкл реакции на сайты", menu)
        act_t_sites.triggered.connect(self._toggle_site_reactions)
        menu.addAction(act_t_sites)

        menu.addSeparator()

        pair_action = QAction("Show Pairing QR / URL", menu)
        pair_action.triggered.connect(self.show_pairing_dialog)
        menu.addAction(pair_action)

        handoff_action = QAction("Send Pet to Android", menu)
        handoff_action.triggered.connect(self._trigger_manual_handoff)
        menu.addAction(handoff_action)

        if self._camera_preview is not None:
            cam_action = QAction("Show Camera & Gestures HUD", menu)
            cam_action.triggered.connect(self.toggle_camera_preview)
            menu.addAction(cam_action)
        else:
            cam_webcam = QAction("Enable Camera Gestures (Webcam)", menu)
            cam_webcam.triggered.connect(lambda: self._enable_camera_runtime("webcam"))
            menu.addAction(cam_webcam)

            cam_ps3 = QAction("Enable Camera Gestures (PS3 Eye)", menu)
            cam_ps3.triggered.connect(lambda: self._enable_camera_runtime("ps3_eye"))
            menu.addAction(cam_ps3)

        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        tray.show()
        return tray

    def _attach_camera_backend(self, camera: CameraBackend, *, show_preview: bool = True) -> bool:
        reader = ThreadedCameraReader(camera, max_poll_hz=30.0)
        if not reader.start():
            logger.warning(
                "Could not open camera (%s). Try --list-cameras or --camera-index 1. Continuing without camera.",
                camera.capabilities.device_kind,
            )
            return False

        self._camera_reader = reader
        self._camera_kind = camera.capabilities.device_kind
        self._last_camera_seq = 0
        cam_idx = getattr(camera, "device_index", 0)
        self._camera_preview = CameraPreviewWindow(
            camera_kind=self._camera_kind,
            camera_index=cam_idx,
            tracker_name=self._hand_detector.name,
            tracker_note=get_last_mediapipe_status(),
        )
        self._camera_preview.request_calibrate_bg.connect(self._calibrate_camera_background)
        self._camera_preview.request_calibrate_skin.connect(self._calibrate_camera_skin)
        self._camera_preview.request_clear_bg.connect(self._clear_camera_background)
        self._camera_preview.request_toggle_pause.connect(self._set_gestures_paused)
        self._camera_preview.request_switch_camera_index.connect(self._switch_camera_index)
        self._camera_preview.request_set_roi.connect(self._set_camera_roi)
        if show_preview:
            self._camera_preview.show()
        logger.info(
            "Live gesture control active (camera=%s, tracker=%s, note=%s)",
            self._camera_kind,
            self._hand_detector.name,
            get_last_mediapipe_status(),
        )
        return True

    def _enable_camera_runtime(self, kind: str) -> None:
        if self._camera_preview is not None:
            self._camera_preview.show()
            self._camera_preview.raise_()
            return
        if kind not in ("webcam", "ps3_eye"):
            return
        # Ensure MediaPipe 21-pt detector is loaded if model exists or can be fetched
        if self._hand_detector.name != "mediapipe_21pt":
            upgraded = create_best_hand_detector(
                model_path=self._hand_model_path,
                auto_download_model=self._auto_download_hand_model,
            )
            if upgraded.name == "mediapipe_21pt":
                self._hand_detector.close()
                self._hand_detector = upgraded
                self._gesture_recognizer.detector = upgraded

        cam = create_camera(kind, device_index=0, ps3_mode=self._ps3_mode)  # type: ignore[arg-type]
        if not self._attach_camera_backend(cam, show_preview=True):
            QMessageBox.warning(
                None,
                "GCS Shimeji — Camera",
                f"Could not open {kind} camera at index 0.\n"
                "Make sure your webcam is plugged in and not used by another app.",
            )

    def toggle_camera_preview(self) -> None:
        if self._camera_preview is None:
            return
        if self._camera_preview.isVisible():
            self._camera_preview.hide()
        else:
            self._camera_preview.show()
            self._camera_preview.raise_()

    def _calibrate_camera_background(self) -> None:
        if self._last_camera_frame is None:
            return
        if self._hand_detector.calibrate_background(self._last_camera_frame.frame_bgr):
            if self._camera_preview is not None:
                self._camera_preview.set_status_text("Seated user + room ignored! Raise your palm to control pet.")
        elif self._camera_preview is not None:
            self._camera_preview.set_status_text(f"Tracker '{self._hand_detector.name}' ignores faces automatically.")

    def _calibrate_camera_skin(self) -> None:
        if self._last_camera_frame is None:
            return
        if self._hand_detector.calibrate_skin_from_roi(self._last_camera_frame.frame_bgr):
            if self._camera_preview is not None:
                self._camera_preview.set_status_text("Adapted hand skin color to current camera lighting!")
        elif self._camera_preview is not None:
            self._camera_preview.set_status_text(f"Tracker '{self._hand_detector.name}' does not require skin calibration.")

    def _set_camera_roi(self, roi: object) -> None:
        if roi is None or (isinstance(roi, tuple) and len(roi) == 4):
            self._hand_detector.set_roi_norm(roi)  # type: ignore[arg-type]
            if self._camera_preview is not None:
                self._camera_preview.set_status_text(
                    "Restricted tracking to Hand Zone box." if roi is not None else "Using full camera frame."
                )

    def _clear_camera_background(self) -> None:
        self._hand_detector.clear_background()
        if self._camera_preview is not None:
            self._camera_preview.set_status_text("Background calibration cleared.")

    def _set_gestures_paused(self, paused: bool) -> None:
        self._gesture_controller.paused = paused

    def _switch_camera_index(self, new_index: int) -> None:
        if self._camera_kind not in ("webcam", "ps3_eye"):
            return
        if self._camera_reader is not None:
            self._camera_reader.stop()
            self._camera_reader = None
        new_cam = create_camera(
            self._camera_kind,  # type: ignore[arg-type]
            device_index=new_index,
            ps3_mode=self._ps3_mode,
        )
        new_reader = ThreadedCameraReader(new_cam, max_poll_hz=30.0)
        if new_reader.start():
            self._camera_reader = new_reader
            self._last_camera_seq = 0
            self._hand_detector.clear_background()
            if self._camera_preview is not None:
                self._camera_preview.set_status_text(f"Switched to {self._camera_kind} index {new_index}")
        elif self._camera_preview is not None:
            self._camera_preview.set_status_text(f"Failed to open camera index {new_index}")

    def _refresh_monitors(self, *_args: object) -> None:
        self._engine.update_desktop_bounds(query_qt_desktop_bounds())

    def _on_drag_started(self, x: float, y: float) -> None:
        self._engine.start_drag(x, y)
        self._push_state_to_hub(force=True)

    def _on_drag_ended(self) -> None:
        self._engine.end_drag()
        self._engine.save_position(scale=self._anim.sprite_sheet.scale)
        self._push_state_to_hub(force=True)

    def _on_animation_complete(self, completed_anim: str) -> None:
        if completed_anim in ("landing", "receive") and self._engine.state == "ACTIVE":
            self._engine.animation = "idle"

    def _has_online_peer(self) -> bool:
        if self._ws_client is None:
            return False
        my_id = self._ws_client.device_id
        return any(d.get("connected") and d.get("device_id") != my_id for d in self._peer_devices)

    def _on_edge_handoff(self, event: EdgeHandoffEvent) -> None:
        if self.standalone or self._ws_client is None or not self._ws_client.is_connected:
            return
        if not self._has_online_peer():
            logger.debug("Edge reached (%s) but no peer device is connected.", event.edge)
            return
        logger.info("Edge drag triggered Windows -> Peer handoff (edge=%s, y=%.2f)", event.edge, event.normalized_y)
        self._push_state_to_hub(force=True)
        self._engine.enter_handoff_pending()
        self._ws_client.send_handoff_request(edge=event.edge, normalized_y=event.normalized_y)

    def _trigger_manual_handoff(self) -> None:
        if self.standalone or self._ws_client is None or not self._ws_client.is_connected:
            return
        if not self._engine.is_owner or not self._has_online_peer():
            return
        self._push_state_to_hub(force=True)
        self._engine.enter_handoff_pending()
        self._ws_client.send_handoff_request(edge="right", normalized_y=0.5)

    def _confirm_incoming_handoff(self, handoff_id: str, source: str) -> None:
        self._gesture_confirm.disarm()
        self._handoff_overlay.hide_offer()
        offer = self._incoming_offer
        self._incoming_offer = None
        edge = str(offer.get("edge", "right")) if offer else "right"
        norm_y = float(offer.get("normalized_y", 0.5)) if offer else 0.5
        spawn_x, spawn_y = self._engine.receive_from_edge(edge, norm_y)
        if self._ws_client is not None:
            self._ws_client.send_handoff_confirm(
                handoff_id=handoff_id,
                accepted=True,
                confirmation_source=source,
                landing_x=spawn_x,
                landing_y=spawn_y,
            )

    def _decline_incoming_handoff(self, handoff_id: str) -> None:
        self._gesture_confirm.disarm()
        self._handoff_overlay.hide_offer()
        self._incoming_offer = None
        if self._ws_client is not None:
            self._ws_client.send_handoff_confirm(
                handoff_id=handoff_id,
                accepted=False,
                confirmation_source="click",
            )

    def _adjust_scale(self, delta: int) -> None:
        new_scale = self._anim.sprite_sheet.scale + delta
        self._anim.set_scale(new_scale)
        self._engine.update_pet_size(*self._anim.frame_size)
        self._engine.save_position(scale=self._anim.sprite_sheet.scale)
        self._render_current_frame()

    def _toggle_sleep(self) -> None:
        if self._engine.state == "SLEEPING":
            self._engine.wake_up()
        else:
            self._engine.trigger_sleep()
        self._push_state_to_hub(force=True)

    def _pet_with_love(self) -> None:
        self._engine.pet_with_love()
        leveled = self._profile.record_petting()
        if leveled:
            self._say(f"Ура! Уровень {self._profile.level}: {self._profile.title}! ★", emotion="love", duration_ms=4200)
        else:
            self._say(self._fun_rng.choice(("Мурр~ ♥", "Ещё погладь! ♥", "Обожаю тебя! ♥")), emotion="love", duration_ms=2600)
        self._push_state_to_hub(force=True)

    # ------------------------------------------------------------------
    # Website reactions, petting, cozy profile & interactive desktop games
    # ------------------------------------------------------------------

    def _say(self, text: str, *, emotion: str | None = None, duration_ms: int = 3600) -> None:
        if not self._engine.is_visible or not self._engine.is_owner:
            return
        self._thought_bubble.show_message(
            text,
            emotion=emotion or self._engine.emotion,
            duration_ms=duration_ms,
        )
        self._thought_bubble.follow_pet(self._engine.x, self._engine.y, self._engine.pet_width)

    def _on_pet_hovered(self, local_x: float, local_y: float) -> None:
        if self._engine.is_dragging or not self._engine.is_owner:
            return
        if self._petting_detector.feed(local_x, local_y, self._engine.pet_height, now=time.monotonic()):
            self._pet_with_love()

    def _toggle_site_reactions(self) -> None:
        self._site_reactor.enabled = not self._site_reactor.enabled
        if not self._site_reactor.enabled:
            self._site_reactor.clear_manual_preview()
            self._active_prop = ""
            self._say("Реакции на сайты выключены", emotion="neutral", duration_ms=2400)
        else:
            self._say("Снова смотрю, на какие сайты ты заходишь! 👀", emotion="happy", duration_ms=3000)

    def _preview_site_prop(self, activity_id: str) -> None:
        try:
            act = SiteActivity(activity_id)
        except ValueError:
            act = SiteActivity.NONE
        upd = self._site_reactor.set_manual_preview(act, now=time.monotonic(), duration_sec=25.0)
        if upd.reaction is None:
            self._active_prop = ""
            self._say("Убрал пропс!", emotion="neutral", duration_ms=2000)
        else:
            self._active_prop = upd.reaction.prop_name
            if upd.reaction.prefer_sit:
                self._engine.sit_and_watch(upd.reaction.emotion)
            else:
                self._engine.set_emotion(upd.reaction.emotion, duration_seconds=6.0)
            if upd.new_phrase:
                self._say(upd.new_phrase, emotion=upd.reaction.emotion, duration_ms=4200)

    def _show_pet_profile(self) -> None:
        cur = self._site_reactor.current_reaction
        site_name = cur.site_label if cur is not None else "Просто гуляет"
        dlg = PetStatusDialog(self._profile, active_site=site_name, parent=None)
        dlg.exec()

    def _toggle_break_reminder(self) -> None:
        self._break_reminder_enabled = not self._break_reminder_enabled
        self._last_break_reminder_t = time.monotonic()
        if self._break_reminder_enabled:
            self._say("Напомню попить воды через 45 мин! 💧", emotion="happy", duration_ms=3200)
        else:
            self._say("Напоминалка разминки выключена", emotion="neutral", duration_ms=2400)

    def _has_active_game(self) -> bool:
        return (
            (self._ball_state is not None and self._ball_state.active)
            or (self._snack_state is not None and self._snack_state.active)
            or self._balloon_game.active
            or self._laser_active
            or self._dance_steps_left > 0
        )

    def _toggle_fetch_ball(self) -> None:
        if self._ball_state is not None and self._ball_state.active:
            self._close_fetch_ball()
            self._say("Убрал мячик! Хорошо поиграли ⚽", emotion="happy", duration_ms=2600)
            return
        if self._engine.state == "SLEEPING":
            self._engine.wake_up()
        screen = self._engine.bounds.screen_for_point(
            self._engine.x + self._engine.pet_width / 2.0,
            self._engine.y + self._engine.pet_height / 2.0,
        )
        start_x = min(screen.right - 60.0, max(screen.left + 60.0, self._engine.x + self._fun_rng.choice((-260.0, 260.0))))
        start_y = max(screen.top + 80.0, screen.bottom - 340.0)
        self._ball_state = FetchBallState(
            x=start_x,
            y=start_y,
            vx=self._fun_rng.choice((-240.0, 240.0)),
            vy=-180.0,
            radius=18.0,
        )
        win = BallToyWindow(radius=18)
        win.drag_started.connect(self._on_ball_drag_started)
        win.drag_moved.connect(self._on_ball_drag_moved)
        win.drag_ended.connect(self._on_ball_drag_ended)
        win.request_close.connect(self._close_fetch_ball)
        win.sync_position(start_x, start_y, 0.0)
        win.show()
        self._ball_win = win
        self._say("Ура, мячик! Кидай его мышкой, я поймаю! ⚽", emotion="excited", duration_ms=3600)

    def _on_ball_drag_started(self, x: float, y: float) -> None:
        if self._ball_state is not None:
            self._ball_state.start_drag(x, y, time.monotonic())

    def _on_ball_drag_moved(self, x: float, y: float) -> None:
        if self._ball_state is not None:
            self._ball_state.update_drag(x, y, time.monotonic())

    def _on_ball_drag_ended(self) -> None:
        if self._ball_state is not None:
            self._ball_state.release_drag()

    def _close_fetch_ball(self) -> None:
        self._ball_state = None
        if self._ball_win is not None:
            self._ball_win.hide()
            self._ball_win.close()
            self._ball_win.deleteLater()
            self._ball_win = None

    def _drop_snack(self) -> None:
        if self._engine.state == "SLEEPING":
            self._engine.wake_up()
        self._close_snack()
        screen = self._engine.bounds.screen_for_point(
            self._engine.x + self._engine.pet_width / 2.0,
            self._engine.y + self._engine.pet_height / 2.0,
        )
        kind = self._fun_rng.choice(SNACK_KINDS)
        sx = min(screen.right - 70.0, max(screen.left + 70.0, self._engine.x + self._fun_rng.choice((-220.0, 220.0))))
        sy = max(screen.top + 80.0, screen.bottom - 300.0)
        self._snack_state = SnackDropState(x=sx, y=sy, kind=kind, size=36)
        win = SnackToyWindow(size=36)
        win.sync_snack(sx, sy, kind)
        win.show()
        self._snack_win = win
        names = {"cookie": "печенька 🍪", "donut": "пончик 🍩", "strawberry": "клубничка 🍓", "popcorn": "попкорн 🍿"}
        self._say(f"Ого, {names.get(kind, 'вкусняшка')}! Бегу!", emotion="happy", duration_ms=2800)

    def _close_snack(self) -> None:
        self._snack_state = None
        if self._snack_win is not None:
            self._snack_win.hide()
            self._snack_win.close()
            self._snack_win.deleteLater()
            self._snack_win = None

    def _toggle_laser_chase(self) -> None:
        self._laser_active = not self._laser_active
        if self._laser_active:
            if self._engine.state == "SLEEPING":
                self._engine.wake_up()
            if self._laser_win is None:
                self._laser_win = LaserDotWindow()
            pt = QCursor.pos()
            self._laser_win.move_center(pt.x(), pt.y())
            self._laser_win.show()
            self._say("Красная точка!! Сейчас поймаю! 🔴", emotion="excited", duration_ms=3200)
        else:
            if self._laser_win is not None:
                self._laser_win.hide()
            self._say("Фух, ушла красная точка! 😅", emotion="happy", duration_ms=2400)

    def _toggle_balloon_game(self) -> None:
        if self._balloon_game.active:
            self._stop_balloon_game()
            self._say(f"Игра остановлена! Лопнуто: {self._balloon_game.score} 🎈", emotion="happy", duration_ms=3000)
            return
        if self._engine.state == "SLEEPING":
            self._engine.wake_up()
        screen = self._engine.bounds.screen_for_point(
            self._engine.x + self._engine.pet_width / 2.0,
            self._engine.y + self._engine.pet_height / 2.0,
        )
        self._balloon_game.start(screen)
        self._sync_balloon_windows()
        self._say("Лопай шарики кликом — я помогу! 🎈", emotion="excited", duration_ms=3400)

    def _on_balloon_clicked(self, balloon_id: int) -> None:
        popped = self._balloon_game.pop_balloon(balloon_id)
        if popped is not None:
            self._profile.record_balloon_pop()
            self._engine.set_emotion("happy", duration_seconds=1.6)
            self._say(f"Бах! 🎈 Счёт: {self._balloon_game.score}", emotion="happy", duration_ms=1600)
            win = self._balloon_wins.pop(balloon_id, None)
            if win is not None:
                win.hide()
                win.close()
                win.deleteLater()

    def _sync_balloon_windows(self) -> None:
        active_ids = {b.balloon_id for b in self._balloon_game.balloons if not b.popped}
        for stale_id in list(self._balloon_wins.keys()):
            if stale_id not in active_ids:
                w = self._balloon_wins.pop(stale_id)
                w.hide()
                w.close()
                w.deleteLater()
        for b in self._balloon_game.balloons:
            if b.popped:
                continue
            w = self._balloon_wins.get(b.balloon_id)
            if w is None:
                w = BalloonToyWindow(b.balloon_id, b.color_hex, radius=int(b.radius))
                w.clicked.connect(self._on_balloon_clicked)
                w.show()
                self._balloon_wins[b.balloon_id] = w
            w.move(int(round(b.x)), int(round(b.y)))

    def _stop_balloon_game(self) -> None:
        self._balloon_game.stop()
        for w in self._balloon_wins.values():
            w.hide()
            w.close()
            w.deleteLater()
        self._balloon_wins.clear()

    def _start_dance(self) -> None:
        if self._engine.state == "SLEEPING":
            self._engine.wake_up()
        self._dance_steps_left = 7
        self._engine.set_emotion("excited", duration_seconds=4.5)
        self._say("Время танцев! 💃✨", emotion="excited", duration_ms=3000)
        self._on_dance_step()
        self._dance_timer.start()

    def _on_dance_step(self) -> None:
        if self._dance_steps_left <= 0 or self._engine.is_dragging:
            self._dance_timer.stop()
            self._dance_steps_left = 0
            return
        self._dance_steps_left -= 1
        self._engine.facing = "left" if (self._dance_steps_left % 2 == 0) else "right"
        self._engine.trigger_jump()
        self._engine.set_emotion("love" if (self._dance_steps_left % 2 == 0) else "excited", duration_seconds=1.6)

    def _tick_site_and_games(self, dt: float, now: float) -> None:
        # 1) Cozy profile drift
        self._profile.tick(dt, sleeping=(self._engine.state == "SLEEPING"))

        # 2) Break / hydration reminder (every 45 minutes if opted in)
        if self._break_reminder_enabled and (now - self._last_break_reminder_t) >= 45.0 * 60.0:
            self._last_break_reminder_t = now
            self._engine.trigger_jump()
            self._say("Эй, попей водички и разомни спину! 💧", emotion="happy", duration_ms=6500)

        # 3) Website / active window reactions (~every 0.75s)
        if (now - self._last_site_poll) >= 0.75:
            self._last_site_poll = now
            title = detect_foreground_window_title()
            upd = self._site_reactor.update(title, now=now)
            reaction = upd.reaction
            if reaction is not None:
                self._active_prop = reaction.prop_name
                if upd.activity_changed:
                    self._profile.record_site_watched()
                if not self._has_active_game() and not self._engine.is_dragging:
                    if reaction.prefer_sit:
                        self._engine.sit_and_watch(reaction.emotion)
                    elif upd.activity_changed:
                        self._engine.set_emotion(reaction.emotion, duration_seconds=4.5)
                if upd.new_phrase and not self._has_active_game():
                    self._say(upd.new_phrase, emotion=reaction.emotion, duration_ms=4000)
            else:
                self._active_prop = ""

        # Keep pet seated & watching during video/study/coding reactions if no game is running
        cur_reaction = self._site_reactor.current_reaction
        if (
            cur_reaction is not None
            and cur_reaction.prefer_sit
            and not self._has_active_game()
            and not self._engine.is_dragging
            and self._engine.state == "ACTIVE"
        ):
            self._engine.sit_and_watch(cur_reaction.emotion)

        pet_cx = self._engine.x + self._engine.pet_width / 2.0
        pet_cy = self._engine.y + self._engine.pet_height / 2.0

        # 4) Snack Drop game
        if self._snack_state is not None and self._snack_state.active:
            self._snack_state.tick(dt, self._engine.bounds)
            if self._snack_win is not None:
                self._snack_win.sync_snack(self._snack_state.x, self._snack_state.y, self._snack_state.kind)
            if not self._engine.is_dragging:
                self._engine.guide_toward_x(self._snack_state.center_x)
                if self._snack_state.is_eaten_by_pet(pet_cx, pet_cy, self._engine.pet_width, self._engine.pet_height):
                    self._close_snack()
                    leveled = self._profile.record_snack()
                    self._engine.pet_with_love()
                    if leveled:
                        self._say(f"Ням! Уровень {self._profile.level}: {self._profile.title}! ★", emotion="love", duration_ms=4200)
                    else:
                        self._say(self._fun_rng.choice(("Хрум-хрум! Вкуснятина! ♥", "Ммм, обожаю! ♥")), emotion="love", duration_ms=3000)

        # 5) Fetch Ball game
        if self._ball_state is not None and self._ball_state.active:
            self._ball_state.tick(dt, self._engine.bounds)
            if self._ball_win is not None:
                self._ball_win.sync_position(self._ball_state.x, self._ball_state.y, self._ball_state.angle)
            if not self._engine.is_dragging and not self._ball_state.being_dragged:
                self._engine.guide_toward_x(self._ball_state.center_x)
                # Jump playfully if ball is above the pet nearby
                if (
                    abs(self._ball_state.center_x - pet_cx) < 85.0
                    and self._ball_state.center_y < self._engine.y + 10.0
                    and self._ball_state.catch_cooldown <= 0.0
                ):
                    self._engine.trigger_jump()
                if self._ball_state.is_caught_by_pet(pet_cx, pet_cy, self._engine.pet_width, self._engine.pet_height):
                    self._ball_state.kick_by_pet(self._fun_rng)
                    leveled = self._profile.record_ball_catch()
                    self._engine.celebrate_peace()
                    if leveled:
                        self._say(f"Поймал! ★ Новый уровень {self._profile.level}!", emotion="love", duration_ms=3600)
                    else:
                        self._say(f"Поймал! ⚽ ({self._ball_state.catches})", emotion="excited", duration_ms=2200)

        # 6) Balloon Pop mini-game
        if self._balloon_game.active:
            screen = self._engine.bounds.screen_for_point(pet_cx, pet_cy)
            pet_popped, finished = self._balloon_game.tick(
                dt, screen, pet_cx, pet_cy, self._engine.pet_width, self._engine.pet_height
            )
            for _b in pet_popped:
                self._profile.record_balloon_pop()
                self._engine.set_emotion("excited", duration_seconds=1.6)
                self._say(f"Я лопнул! 🎈 ({self._balloon_game.score})", emotion="excited", duration_ms=1600)
            if finished:
                self._stop_balloon_game()
                self._engine.celebrate_peace()
                self._say(
                    f"Финиш! Лопнуто: {self._balloon_game.score} 🎈 (рекорд: {self._balloon_game.best_score})",
                    emotion="love",
                    duration_ms=4800,
                )
            else:
                self._sync_balloon_windows()
                target_b = self._balloon_game.nearest_reachable_balloon(pet_cx, float(screen.bottom - self._engine.pet_height))
                if target_b is not None and not self._engine.is_dragging:
                    self._engine.guide_toward_x(target_b.center_x)
                    if abs(target_b.center_x - pet_cx) < 70.0 and target_b.center_y < self._engine.y + 30.0:
                        self._engine.trigger_jump()

        # 7) Laser Pointer chase game
        if self._laser_active:
            if self._laser_catch_cd > 0.0:
                self._laser_catch_cd = max(0.0, self._laser_catch_cd - dt)
            pt = QCursor.pos()
            if self._laser_win is not None:
                self._laser_win.move_center(pt.x(), pt.y())
            if not self._engine.is_dragging:
                self._engine.guide_toward_x(float(pt.x()))
                if math.hypot(pt.x() - pet_cx, pt.y() - pet_cy) < 75.0 and self._laser_catch_cd <= 0.0:
                    self._laser_catch_cd = 1.6
                    self._engine.trigger_jump()
                    self._engine.set_emotion("excited", duration_seconds=2.0)
                    self._say("Поймал точку! 🔴✨", emotion="excited", duration_ms=1800)

    def _set_manual_emotion(self, emotion: str) -> None:
        dur = 0.0 if emotion == "neutral" else 8.0
        self._engine.set_emotion(emotion, duration_seconds=dur)
        self._push_state_to_hub(force=True)

    def show_pairing_dialog(self) -> None:
        lan_ip = detect_local_ip()
        if self._embedded_hub is not None:
            uri = self._embedded_hub.hub.pairing_uri
            web_url = self._embedded_hub.hub.web_url
            ws_url = f"ws://{lan_ip}:{self._embedded_hub.hub.actual_port}"
        else:
            web_port = self.hub_port + 1
            web_url = f"http://{lan_ip}:{web_port}"
            ws_url = f"ws://{lan_ip}:{self.hub_port}"
            uri = build_pairing_uri("0.0.0.0", self.hub_port, self.protocol, web_port=web_port)
        qr_path = save_pairing_qr_png(web_url, get_user_data_dir() / "pairing_qr.png")
        dialog = PairingDialog(uri, web_url, qr_path, ws_url=ws_url)
        dialog.exec()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self._last_tick))
        self._last_tick = now

        # Process inbound Hub WebSocket events
        if self._ws_client is not None:
            for event in self._ws_client.drain_events():
                self._handle_hub_event(event)

        # Real-time camera & gesture processing on every new frame
        if self._camera_reader is not None:
            cam_frame, new_seq = self._camera_reader.get_latest(self._last_camera_seq)
            if cam_frame is not None:
                self._last_camera_seq = new_seq
                self._last_camera_frame = cam_frame
                update = self._gesture_controller.process_frame(cam_frame)

                # Update live HUD preview window (~30 FPS)
                if (
                    self._camera_preview is not None
                    and self._camera_preview.isVisible()
                    and (now - self._last_preview_draw) >= 0.032
                ):
                    self._last_preview_draw = now
                    annotated = draw_hand_overlay(
                        cam_frame.frame_bgr,
                        update.event,
                        active_action=update.active_action,
                        fps=self._camera_reader.fps,
                        show_mask=self._camera_preview.show_mask,
                        handoff_armed=(self._gesture_confirm.pending_handoff_id is not None),
                        roi_norm=self._hand_detector.roi_norm,
                        control_zone_norm=self._gesture_controller.control_zone_norm,
                        pet_emotion=self._engine.emotion,
                    )
                    self._camera_preview.update_frame(annotated, status_text=update.active_action)

        # Advance site reactions, cozy profile & interactive desktop games
        self._tick_site_and_games(dt, now)

        # Advance physics & animations
        self._engine.tick(dt)
        self._anim.facing = self._engine.facing
        self._anim.emotion = self._engine.emotion
        self._anim.play(self._engine.animation)
        self._anim.update(dt)

        # Sync visibility, position, and crisp pixel frame
        if self._engine.is_visible and self._engine.is_owner:
            if not self._window.isVisible():
                self._window.show()
            self._window.set_context_state(
                is_sleeping=(self._engine.state == "SLEEPING"),
                can_handoff=self._has_online_peer(),
                has_camera=(self._camera_preview is not None),
                emotion=self._engine.emotion,
                effect_phase=self._engine.effect_phase,
                active_prop=self._active_prop,
                site_reactions_enabled=self._site_reactor.enabled,
                laser_active=self._laser_active,
                ball_active=(self._ball_state is not None and self._ball_state.active),
                balloons_active=self._balloon_game.active,
                break_reminder_enabled=self._break_reminder_enabled,
            )
            self._render_current_frame()
            self._window.move_to(self._engine.x, self._engine.y)
            self._thought_bubble.follow_pet(self._engine.x, self._engine.y, self._engine.pet_width)
        else:
            if self._window.isVisible():
                self._window.hide()
            if self._thought_bubble.isVisible():
                self._thought_bubble.hide()

        # Throttled Hub state updates (~10 Hz while active)
        if now - self._last_sync_sent >= 0.1:
            self._push_state_to_hub(force=False)

        # Periodic position & profile persistence every 15 seconds
        if now - self._last_save_time >= 15.0 and self._engine.is_owner:
            self._engine.save_position(scale=self._anim.sprite_sheet.scale)
            self._profile.save()
            self._last_save_time = now

    def _render_current_frame(self) -> None:
        self._window.set_rendered_frame(self._anim.current_frame())

    def _push_state_to_hub(self, *, force: bool) -> None:
        if self.standalone or self._ws_client is None or not self._ws_client.is_connected:
            return
        if not self._engine.is_owner or self._engine.state == "HANDOFF_PENDING":
            return
        now = time.monotonic()
        if not force and (now - self._last_sync_sent) < 0.1:
            return
        self._last_sync_sent = now
        self._ws_client.send_state_update(
            x=self._engine.x,
            y=self._engine.y,
            animation=self._engine.animation,
            state=self._engine.state,
            facing=self._engine.facing,
            emotion=self._engine.emotion,
        )

    def _handle_hub_event(self, event: HubClientEvent) -> None:
        if event.event_type != "message":
            return
        envelope = event.payload
        msg_type = envelope.get("type")
        payload = envelope.get("payload", {})
        if not isinstance(payload, dict) or self._ws_client is None:
            return
        my_id = self._ws_client.device_id

        if msg_type == "register_ack":
            self._peer_devices = list(payload.get("devices", []))
            self._apply_authoritative_state(payload.get("avatar_state", {}))
        elif msg_type == "device_list":
            self._peer_devices = list(payload.get("devices", []))
        elif msg_type == "state_sync":
            self._apply_authoritative_state(payload)
        elif msg_type == "handoff_pending":
            if payload.get("source_device_id") == my_id:
                self._engine.enter_handoff_pending()
                self._engine.is_visible = False
        elif msg_type == "handoff_offer":
            self._incoming_offer = payload
            handoff_id = str(payload.get("handoff_id", ""))
            source_name = str(payload.get("source_device_name") or payload.get("source_device_id") or "Android")
            edge = str(payload.get("edge", "right"))
            timeout_sec = float(payload.get("timeout_seconds", 6.0))
            # Always compute local deadline from relative timeout to avoid cross-device clock skew
            expires_at = time.time() + timeout_sec

            # Arm camera gesture confirmation (never initiates handoff on its own!)
            self._gesture_confirm.arm_for_incoming_offer(handoff_id, expires_at)
            self._handoff_overlay.show_offer(handoff_id, source_name, edge, timeout_sec)
        elif msg_type == "handoff_complete":
            self._gesture_confirm.disarm()
            self._handoff_overlay.hide_offer()
            self._incoming_offer = None
            new_owner = payload.get("new_owner")
            if new_owner == my_id:
                edge = str(payload.get("edge", "right"))
                norm_y = float(payload.get("normalized_y", 0.5))
                if not self._engine.is_owner or not self._engine.is_visible:
                    self._engine.receive_from_edge(edge, norm_y)
                avatar_state = payload.get("avatar_state")
                if isinstance(avatar_state, dict) and avatar_state.get("emotion"):
                    self._engine.set_emotion(str(avatar_state["emotion"]), duration_seconds=4.0)
                self._anim.play("receive", restart=True)
            else:
                self._engine.complete_outgoing_handoff()
        elif msg_type == "handoff_cancel":
            self._gesture_confirm.disarm()
            self._handoff_overlay.hide_offer()
            self._incoming_offer = None
            if payload.get("restored_owner") == my_id:
                state_dict = payload.get("avatar_state", {})
                pos = state_dict.get("position", {}) if isinstance(state_dict, dict) else {}
                self._engine.cancel_handoff_restore(
                    x=pos.get("x") if isinstance(pos, dict) else None,
                    y=pos.get("y") if isinstance(pos, dict) else None,
                )
        elif msg_type == "error":
            logger.warning("Hub error [%s]: %s", payload.get("code"), payload.get("message"))

    def _apply_authoritative_state(self, state_dict: dict[str, Any]) -> None:
        if not state_dict or self._ws_client is None:
            return
        owner = state_dict.get("owner")
        we_own = owner == self._ws_client.device_id
        state = str(state_dict.get("state", "ACTIVE"))
        if we_own:
            self._engine.is_owner = True
            self._engine.is_visible = state != "HANDOFF_PENDING"
        else:
            self._engine.is_owner = False
            self._engine.is_visible = False

    def shutdown(self) -> None:
        """Persist position and stop timers, camera, detector, WebSocket client, and embedded Hub."""
        logger.info("Shutting down GCS Shimeji Windows client...")
        self._timer.stop()
        self._dance_timer.stop()
        self._close_fetch_ball()
        self._close_snack()
        self._stop_balloon_game()
        if self._laser_win is not None:
            self._laser_win.hide()
            self._laser_win.close()
        self._thought_bubble.hide()
        self._thought_bubble.close()
        self._profile.save()
        if self._engine.is_owner:
            self._engine.save_position(scale=self._anim.sprite_sheet.scale)
        if self._camera_reader is not None:
            self._camera_reader.stop()
            self._camera_reader = None
        self._hand_detector.close()
        if self._ws_client is not None:
            self._ws_client.stop_background()
        if self._embedded_hub is not None:
            self._embedded_hub.stop()
        if self._camera_preview is not None:
            self._camera_preview.deleteLater()
        self._handoff_overlay.close()
        self._window.close()
        if self._tray is not None:
            self._tray.hide()
        self.qt_app.quit()
