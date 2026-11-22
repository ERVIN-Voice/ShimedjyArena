"""PC-only AI (g4f) + Voice Control (GS) desktop pet coordinator.

Subclasses `windows.desktop_pet.DesktopPetApp` without modifying any existing file.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from shared.config_loader import AvatarConfig
from windows.camera import CameraBackend
from windows.desktop_pet import DesktopPetApp
from windows_ai.ai_brain import PetAIResponse, ShimejiAIBrain, execute_pet_ai_action
from windows_ai.ai_ui import SpeechBubbleWindow, VoiceAIPanelWindow
from windows_ai.voice_engine import VoiceController, list_input_microphones

logger = logging.getLogger(__name__)


class AIDesktopPetApp(DesktopPetApp):
    """DesktopPetApp extended with g4f neural chat + GS voice control (PC-only)."""

    _ai_reply_ready = Signal(object)  # PetAIResponse

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
        ai_model: str = "default",
        voice_language: str = "ru-RU",
        continuous_voice: bool = False,
        require_wake_word: bool = False,
        tts_enabled: bool = True,
        show_ai_panel: bool = True,
    ) -> None:
        super().__init__(
            qt_app,
            avatar_config=avatar_config,
            scale=scale,
            standalone=standalone,
            hub_url=hub_url,
            hub_port=hub_port,
            enable_mdns=enable_mdns,
            camera=camera,
            show_camera_preview=show_camera_preview,
            hand_model_path=hand_model_path,
            auto_download_hand_model=auto_download_hand_model,
        )

        self._use_g4f = True
        self._ai_busy = False
        self._brain = ShimejiAIBrain(model=ai_model)
        self._voice = VoiceController(
            language=voice_language,
            require_wake_word=require_wake_word,
            tts_enabled=tts_enabled,
        )

        self._bubble = SpeechBubbleWindow()
        self._ai_panel = VoiceAIPanelWindow(
            microphones=list_input_microphones(),
            initial_model=ai_model,
            initial_language=voice_language,
            initial_continuous=continuous_voice,
            initial_wake_word=require_wake_word,
            initial_tts=tts_enabled,
        )

        # Wire UI -> Voice & AI
        self._ai_panel.request_send_text.connect(self._handle_user_utterance)
        self._ai_panel.request_listen_once.connect(self._start_voice_once)
        self._ai_panel.request_continuous_changed.connect(self._set_continuous_voice)
        self._ai_panel.request_wake_word_changed.connect(self._set_wake_word)
        self._ai_panel.request_tts_changed.connect(self._set_tts_enabled)
        self._ai_panel.request_g4f_enabled_changed.connect(self._set_g4f_enabled)
        self._ai_panel.request_model_changed.connect(self._set_ai_model)
        self._ai_panel.request_language_changed.connect(self._set_voice_language)
        self._ai_panel.request_microphone_changed.connect(self._set_microphone)
        self._ai_panel.request_clear_history.connect(self._brain.clear_history)

        # Wire Voice -> UI & AI
        self._voice.transcript_ready.connect(self._handle_user_utterance)
        self._voice.status_changed.connect(self._ai_panel.set_status)
        self._voice.level_changed.connect(self._ai_panel.set_mic_level)
        self._voice.listening_changed.connect(self._on_listening_changed)
        self._voice.error_occurred.connect(self._on_voice_error)
        self._ai_reply_ready.connect(self._apply_ai_response)

        # Extend tray menu with AI & Voice actions (without touching windows/desktop_pet.py!)
        self._attach_ai_tray_actions()

        if show_ai_panel:
            self._ai_panel.show()

        if continuous_voice:
            self._voice.set_continuous(True)

        self._bubble.show_message(
            "Привет! Я Чубрик с g4f ИИ и ГС! Нажми «🎤 Сказать»!",
            emotion="happy",
            duration_seconds=5.0,
        )

    def _attach_ai_tray_actions(self) -> None:
        if self._tray is None:
            return
        menu = self._tray.contextMenu()
        if menu is None:
            return
        first_action = menu.actions()[0] if menu.actions() else None

        panel_action = QAction("🤖 AI (g4f) & ГС Панель", menu)
        panel_action.triggered.connect(self.toggle_ai_panel)

        ptt_action = QAction("🎤 Сказать команду Чубрику (ГС)", menu)
        ptt_action.triggered.connect(self._start_voice_once)

        if first_action is not None:
            menu.insertAction(first_action, panel_action)
            menu.insertAction(first_action, ptt_action)
            menu.insertSeparator(first_action)
        else:
            menu.addAction(panel_action)
            menu.addAction(ptt_action)

    def toggle_ai_panel(self) -> None:
        if self._ai_panel.isVisible():
            self._ai_panel.hide()
        else:
            self._ai_panel.show()
            self._ai_panel.raise_()
            self._ai_panel.activateWindow()

    def _start_voice_once(self) -> None:
        self._bubble.show_message("🎤 Слушаю тебя...", emotion="excited", duration_seconds=5.0)
        self._voice.listen_once()

    def _set_continuous_voice(self, enabled: bool) -> None:
        self._voice.set_continuous(enabled)
        self._ai_panel.set_listening(enabled)

    def _set_wake_word(self, enabled: bool) -> None:
        self._voice.require_wake_word = enabled

    def _set_tts_enabled(self, enabled: bool) -> None:
        self._voice.tts_enabled = enabled

    def _set_g4f_enabled(self, enabled: bool) -> None:
        self._use_g4f = bool(enabled)

    def _set_ai_model(self, model: str) -> None:
        self._brain.model = model

    def _set_voice_language(self, lang: str) -> None:
        self._voice.language = lang

    def _set_microphone(self, dev_index: object) -> None:
        self._voice.device_index = dev_index if isinstance(dev_index, int) else None

    def _on_listening_changed(self, active: bool) -> None:
        self._ai_panel.set_listening(active)

    def _on_voice_error(self, message: str) -> None:
        self._bubble.show_message(f"Микрофон: {message[:90]}", emotion="dizzy", duration_seconds=4.5)

    def _handle_user_utterance(self, text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned or self._ai_busy:
            return
        self._ai_busy = True
        self._ai_panel.append_chat("Ты", cleaned)
        self._ai_panel.set_status("🧠 Нейросеть g4f думает над ответом...")
        self._bubble.show_message("🤔 Думаю...", emotion="neutral", duration_seconds=12.0)

        cur_emotion = self._engine.emotion
        cur_state = self._engine.state
        use_g4f = self._use_g4f

        def _worker() -> None:
            try:
                reply = self._brain.ask(
                    cleaned,
                    current_emotion=cur_emotion,
                    current_state=cur_state,
                    use_g4f=use_g4f,
                )
            except Exception as exc:
                logger.exception("Unexpected error in AI brain worker")
                reply = PetAIResponse(
                    reply="Ой, у меня перегрелись пиксели! Попробуй ещё раз.",
                    emotion="dizzy",
                    action="none",
                    error=str(exc),
                )
            self._ai_reply_ready.emit(reply)

        threading.Thread(target=_worker, name="gcs-shimeji-g4f", daemon=True).start()

    def _schedule_dance_hops(self) -> None:
        def _hop(delay_ms: int, emo: str) -> None:
            QTimer.singleShot(
                delay_ms,
                lambda: (
                    self._engine.trigger_jump(),
                    self._engine.set_emotion(emo, duration_seconds=4.0),
                    self._push_state_to_hub(force=True),
                ),
            )

        _hop(520, "happy")
        _hop(1050, "excited")
        _hop(1580, "love")

    def _apply_ai_response(self, response: object) -> None:
        self._ai_busy = False
        if not isinstance(response, PetAIResponse):
            return

        executed = execute_pet_ai_action(
            self._engine,
            response,
            on_handoff=self._trigger_manual_handoff,
            on_scale_delta=self._adjust_scale,
            on_enable_camera=self._enable_camera_runtime,
            on_schedule_dance=self._schedule_dance_hops,
        )
        self._push_state_to_hub(force=True)

        meta_parts = [f"эмоция: {response.emotion}"]
        if executed != "none":
            meta_parts.append(f"действие: {executed}")
        if response.provider_model:
            meta_parts.append(response.provider_model)

        self._ai_panel.append_chat("Чубрик", response.reply, meta=", ".join(meta_parts))
        self._ai_panel.set_status(
            f"Готов | g4f: {response.provider_model or 'local'} | эмоция: {response.emotion}"
        )
        self._bubble.show_message(response.reply, emotion=response.emotion, duration_seconds=6.5)
        self._voice.speak(response.reply)

    def _tick(self) -> None:
        super()._tick()
        self._bubble.follow_pet(
            self._engine.x,
            self._engine.y,
            self._engine.pet_width,
            pet_visible=(self._engine.is_visible and self._engine.is_owner),
        )

    def shutdown(self) -> None:
        self._voice.shutdown()
        self._bubble.close()
        self._ai_panel.close()
        super().shutdown()
