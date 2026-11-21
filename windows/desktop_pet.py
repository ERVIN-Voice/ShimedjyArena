"""High-level Windows Desktop Pet coordinator.

Connects:
- Stage 1: PetEngine + AnimationController + TransparentPetWindow (frameless, pixel-perfect, multi-monitor)
- Stage 2 & 4: GCS Local Hub WebSocket client + two-phase cross-device handoff UI & edge drag
- Stage 6: Real-time 2D RGB camera gesture control (pinch-drag, point-call, palm-wake/jump, fist-sleep,
           and confirmation-only incoming handoff acceptance) + live Camera & Gestures HUD
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout

from server.hub import build_pairing_uri, render_pairing_qr_ascii, save_pairing_qr_png
from shared.config_loader import AvatarConfig, load_avatar_config, load_protocol_config
from windows.animations import AnimationController
from windows.camera import CameraBackend, CameraFrame, PS3EyeCamera, ThreadedCameraReader, create_camera
from windows.camera_preview import CameraPreviewWindow
from windows.gesture_controller import GesturePetController
from windows.hand_detection import (
    GestureRecognizer,
    HandoffGestureConfirmationController,
    create_best_hand_detector,
    draw_hand_overlay,
)
from windows.network_client import EmbeddedHubRunner, HubClientEvent, WindowsHubClient
from windows.pet_engine import EdgeHandoffEvent, PetEngine
from windows.transparent_window import (
    HandoffPromptOverlay,
    TransparentPetWindow,
    pil_frame_to_pixmap,
    query_qt_desktop_bounds,
)

logger = logging.getLogger(__name__)


class PairingDialog(QDialog):
    """Displays local Hub WebSocket URL and scannable QR code for Android pairing."""

    def __init__(self, pairing_uri: str, qr_png_path: Path) -> None:
        super().__init__(None)
        self.setWindowTitle("GCS Shimeji — Pair Android Device")
        layout = QVBoxLayout(self)

        title = QLabel("Scan QR code or enter URL in Android GCS Shimeji:")
        uri_lbl = QLabel(pairing_uri)
        uri_lbl.setWordWrap(True)

        qr_lbl = QLabel()
        pix = QPixmap(str(qr_png_path))
        if not pix.isNull():
            qr_lbl.setPixmap(pix)

        layout.addWidget(title)
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
        self._window.request_handoff.connect(self._trigger_manual_handoff)
        self._window.request_scale_delta.connect(self._adjust_scale)
        self._window.request_toggle_sleep.connect(self._toggle_sleep)
        self._window.request_show_pairing.connect(self.show_pairing_dialog)
        self._window.request_toggle_camera_hud.connect(self.toggle_camera_preview)
        self._window.request_quit.connect(self.shutdown)

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
            reader = ThreadedCameraReader(camera, max_poll_hz=30.0)
            if reader.start():
                self._camera_reader = reader
                cam_idx = getattr(camera, "device_index", 0)
                self._camera_preview = CameraPreviewWindow(
                    camera_kind=self._camera_kind,
                    camera_index=cam_idx,
                    tracker_name=self._hand_detector.name,
                )
                self._camera_preview.request_calibrate_bg.connect(self._calibrate_camera_background)
                self._camera_preview.request_calibrate_skin.connect(self._calibrate_camera_skin)
                self._camera_preview.request_clear_bg.connect(self._clear_camera_background)
                self._camera_preview.request_toggle_pause.connect(self._set_gestures_paused)
                self._camera_preview.request_switch_camera_index.connect(self._switch_camera_index)
                self._camera_preview.request_set_roi.connect(self._set_camera_roi)
                if show_camera_preview:
                    self._camera_preview.show()
                logger.info(
                    "Live gesture control active (camera=%s, tracker=%s)",
                    self._camera_kind,
                    self._hand_detector.name,
                )
            else:
                logger.warning(
                    "Could not open camera (%s). Try --list-cameras or --camera-index 1. Continuing without camera.",
                    self._camera_kind,
                )

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
                uri = self._embedded_hub.hub.pairing_uri
                logger.info("Android Pairing URI: %s", uri)
                logger.info("\n%s", render_pairing_qr_ascii(uri))

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

        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        tray.show()
        return tray

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
        self._engine.enter_handoff_pending()
        self._ws_client.send_handoff_request(edge=event.edge, normalized_y=event.normalized_y)

    def _trigger_manual_handoff(self) -> None:
        if self.standalone or self._ws_client is None or not self._ws_client.is_connected:
            return
        if not self._engine.is_owner or not self._has_online_peer():
            return
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

    def show_pairing_dialog(self) -> None:
        if self._embedded_hub is not None:
            uri = self._embedded_hub.hub.pairing_uri
        else:
            uri = build_pairing_uri("0.0.0.0", self.hub_port, self.protocol)
        qr_path = save_pairing_qr_png(uri, Path("pairing_qr.png"))
        dialog = PairingDialog(uri, qr_path)
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
                    )
                    self._camera_preview.update_frame(annotated, status_text=update.active_action)

        # Advance physics & animations
        self._engine.tick(dt)
        self._anim.facing = self._engine.facing
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
            )
            self._render_current_frame()
            self._window.move_to(self._engine.x, self._engine.y)
        else:
            if self._window.isVisible():
                self._window.hide()

        # Throttled Hub state updates (~10 Hz while active)
        if now - self._last_sync_sent >= 0.1:
            self._push_state_to_hub(force=False)

        # Periodic position persistence every 15 seconds
        if now - self._last_save_time >= 15.0 and self._engine.is_owner:
            self._engine.save_position(scale=self._anim.sprite_sheet.scale)
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
            expires_at = float(payload.get("expires_at", time.time() + timeout_sec))

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
