"""PySide6 UI components for the PC-only AI (g4f) + Voice Control (GS) Shimeji edition."""

from __future__ import annotations

import time
from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from windows_ai.ai_brain import DEFAULT_MODELS
from windows_ai.voice_engine import MicrophoneDevice

EMOTION_BORDER_HEX: dict[str, str] = {
    "neutral": "#56c6e0",
    "happy": "#ffd54f",
    "love": "#ff6ea8",
    "excited": "#4ce88a",
    "scared": "#7a94e8",
    "dizzy": "#b47cff",
    "angry": "#ff4d4d",
    "sleepy": "#888ce0",
}


class SpeechBubbleWindow(QWidget):
    """Frameless click-through speech bubble that floats above the desktop pet."""

    def __init__(self) -> None:
        super().__init__(None)
        self._expires_at: float = 0.0
        self._emotion: str = "neutral"

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(280)
        layout.addWidget(self._label)
        self._apply_style("neutral")

    def _apply_style(self, emotion: str) -> None:
        border = EMOTION_BORDER_HEX.get(emotion, "#56c6e0")
        self._label.setStyleSheet(
            f"""
            QLabel {{
                background-color: rgba(18, 24, 38, 232);
                color: #eef6ff;
                border: 2px solid {border};
                border-radius: 12px;
                padding: 7px 11px;
                font-family: "Segoe UI", Consolas, sans-serif;
                font-size: 12px;
                font-weight: 600;
            }}
            """
        )

    def show_message(self, text: str, *, emotion: str = "neutral", duration_seconds: float = 5.5) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            self.hide()
            return
        self._emotion = emotion
        self._apply_style(emotion)
        self._label.setText(cleaned)
        self._label.adjustSize()
        self.adjustSize()
        self._expires_at = time.monotonic() + max(1.0, float(duration_seconds))
        self.show()

    def follow_pet(self, pet_x: float, pet_y: float, pet_w: int, *, pet_visible: bool) -> None:
        if not pet_visible or time.monotonic() > self._expires_at:
            if self.isVisible():
                self.hide()
            return
        bx = int(round(pet_x + pet_w / 2.0 - self.width() / 2.0))
        by = int(round(pet_y - self.height() - 8))
        if by < 8:
            by = int(round(pet_y + pet_w + 6))
        if self.x() != bx or self.y() != by:
            self.move(bx, by)


class VoiceAIPanelWindow(QWidget):
    """Control panel for g4f AI chat, GS voice control, microphone selection, and TTS."""

    request_send_text = Signal(str)
    request_listen_once = Signal()
    request_continuous_changed = Signal(bool)
    request_wake_word_changed = Signal(bool)
    request_tts_changed = Signal(bool)
    request_g4f_enabled_changed = Signal(bool)
    request_model_changed = Signal(str)
    request_language_changed = Signal(str)
    request_microphone_changed = Signal(object)  # int | None
    request_clear_history = Signal()

    def __init__(
        self,
        *,
        microphones: Iterable[MicrophoneDevice],
        initial_model: str = "default",
        initial_language: str = "ru-RU",
        initial_continuous: bool = False,
        initial_wake_word: bool = False,
        initial_tts: bool = True,
    ) -> None:
        super().__init__(None)
        self.setWindowTitle("GCS Shimeji — AI (g4f) & ГС Управление")
        self.setMinimumSize(430, 500)
        self.resize(450, 540)

        self.setStyleSheet(
            """
            QWidget {
                background-color: #111622;
                color: #e6f2ff;
                font-family: "Segoe UI", sans-serif;
                font-size: 12px;
            }
            QLabel#headerTitle {
                font-size: 15px;
                font-weight: bold;
                color: #66fcf1;
            }
            QLabel#statusLabel {
                color: #9dc2e0;
                font-size: 11px;
                padding: 4px 8px;
                background-color: #192234;
                border-radius: 6px;
            }
            QPushButton {
                background-color: #24314a;
                color: #eef6ff;
                border: 1px solid #395075;
                border-radius: 7px;
                padding: 7px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #304263;
            }
            QPushButton#pttBtn {
                background-color: #1c9c64;
                border: 1px solid #4ce88a;
                color: #07140d;
                font-size: 13px;
                padding: 10px 14px;
            }
            QPushButton#pttBtn:hover {
                background-color: #27ba7a;
            }
            QPushButton#pttBtn:disabled {
                background-color: #3a4a63;
                color: #b4c3d6;
                border-color: #4f6382;
            }
            QLineEdit, QComboBox, QTextEdit {
                background-color: #182030;
                color: #eef6ff;
                border: 1px solid #304363;
                border-radius: 6px;
                padding: 5px 8px;
            }
            QProgressBar {
                background-color: #182030;
                border: 1px solid #2e405e;
                border-radius: 5px;
                height: 10px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4ce88a;
                border-radius: 4px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setSpacing(8)

        title = QLabel("🤖 Чубрик AI (g4f) + 🎤 Голосовое управление")
        title.setObjectName("headerTitle")
        root.addWidget(title)

        self._status_lbl = QLabel("Готов! Нажмите «🎤 Сказать (ГС)» или напишите Чубрику.")
        self._status_lbl.setObjectName("statusLabel")
        self._status_lbl.setWordWrap(True)
        root.addWidget(self._status_lbl)

        # Big Push-to-Talk button + live mic meter
        self._ptt_btn = QPushButton("🎤 Сказать команду / вопрос (ГС)")
        self._ptt_btn.setObjectName("pttBtn")
        self._ptt_btn.clicked.connect(self.request_listen_once.emit)
        root.addWidget(self._ptt_btn)

        self._level_bar = QProgressBar()
        self._level_bar.setRange(0, 100)
        self._level_bar.setValue(0)
        self._level_bar.setTextVisible(False)
        root.addWidget(self._level_bar)

        # Toggle switches row 1 & 2
        row_chk1 = QHBoxLayout()
        self._chk_continuous = QCheckBox("Непрерывное ГС")
        self._chk_continuous.setChecked(initial_continuous)
        self._chk_continuous.toggled.connect(self.request_continuous_changed.emit)

        self._chk_wake = QCheckBox("Только по слову «Чубрик»")
        self._chk_wake.setChecked(initial_wake_word)
        self._chk_wake.toggled.connect(self.request_wake_word_changed.emit)

        row_chk1.addWidget(self._chk_continuous)
        row_chk1.addWidget(self._chk_wake)
        root.addLayout(row_chk1)

        row_chk2 = QHBoxLayout()
        self._chk_tts = QCheckBox("🔊 Озвучивать ответы")
        self._chk_tts.setChecked(initial_tts)
        self._chk_tts.toggled.connect(self.request_tts_changed.emit)

        self._chk_g4f = QCheckBox("🧠 Нейросеть g4f")
        self._chk_g4f.setChecked(True)
        self._chk_g4f.toggled.connect(self.request_g4f_enabled_changed.emit)

        row_chk2.addWidget(self._chk_tts)
        row_chk2.addWidget(self._chk_g4f)
        root.addLayout(row_chk2)

        # Model + Language selectors
        row_cfg = QHBoxLayout()
        row_cfg.addWidget(QLabel("Модель g4f:"))
        self._model_combo = QComboBox()
        for m in DEFAULT_MODELS:
            self._model_combo.addItem(m)
        if initial_model not in DEFAULT_MODELS:
            self._model_combo.addItem(initial_model)
        self._model_combo.setCurrentText(initial_model)
        self._model_combo.currentTextChanged.connect(self.request_model_changed.emit)
        row_cfg.addWidget(self._model_combo, 1)

        row_cfg.addWidget(QLabel("Язык ГС:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("Русский (ru-RU)", "ru-RU")
        self._lang_combo.addItem("English (en-US)", "en-US")
        idx_lang = self._lang_combo.findData(initial_language)
        if idx_lang >= 0:
            self._lang_combo.setCurrentIndex(idx_lang)
        self._lang_combo.currentIndexChanged.connect(
            lambda _i: self.request_language_changed.emit(str(self._lang_combo.currentData()))
        )
        row_cfg.addWidget(self._lang_combo, 1)
        root.addLayout(row_cfg)

        # Microphone selector
        row_mic = QHBoxLayout()
        row_mic.addWidget(QLabel("Микрофон:"))
        self._mic_combo = QComboBox()
        for dev in microphones:
            self._mic_combo.addItem(dev.name, dev.index)
        self._mic_combo.currentIndexChanged.connect(
            lambda _i: self.request_microphone_changed.emit(self._mic_combo.currentData())
        )
        row_mic.addWidget(self._mic_combo, 1)
        root.addLayout(row_mic)

        # Chat log
        self._chat_log = QTextEdit()
        self._chat_log.setReadOnly(True)
        self._chat_log.setPlaceholderText(
            "Примеры голосовых команд:\n"
            "• «Прыгни!», «Станцуй!», «Иди налево / направо»\n"
            "• «Спи», «Проснись», «Я тебя люблю», «Разозлись»\n"
            "• «Лети на телефон!», «Включи камеру», «Стань больше»\n"
            "• Или просто поговори с ним — ответит нейросеть g4f!"
        )
        root.addWidget(self._chat_log, 1)

        # Manual text input row
        row_input = QHBoxLayout()
        self._text_input = QLineEdit()
        self._text_input.setPlaceholderText("Написать Чубрику (если без микрофона)...")
        self._text_input.returnPressed.connect(self._emit_text)

        send_btn = QPushButton("Отправить")
        send_btn.clicked.connect(self._emit_text)

        clear_btn = QPushButton("Очистить")
        clear_btn.clicked.connect(self._clear_log)

        row_input.addWidget(self._text_input, 1)
        row_input.addWidget(send_btn)
        row_input.addWidget(clear_btn)
        root.addLayout(row_input)

    def _emit_text(self) -> None:
        text = self._text_input.text().strip()
        if not text:
            return
        self._text_input.clear()
        self.request_send_text.emit(text)

    def _clear_log(self) -> None:
        self._chat_log.clear()
        self.request_clear_history.emit()

    def set_status(self, text: str) -> None:
        self._status_lbl.setText(text)

    def set_mic_level(self, level_0_to_1: float) -> None:
        self._level_bar.setValue(int(max(0.0, min(1.0, level_0_to_1)) * 100))

    def set_listening(self, active: bool) -> None:
        if active and not self._chk_continuous.isChecked():
            self._ptt_btn.setText("🎤 Слушаю... говорите!")
            self._ptt_btn.setEnabled(False)
        elif self._chk_continuous.isChecked():
            self._ptt_btn.setText("🎤 Непрерывное ГС включено")
            self._ptt_btn.setEnabled(False)
        else:
            self._ptt_btn.setText("🎤 Сказать команду / вопрос (ГС)")
            self._ptt_btn.setEnabled(True)

    def append_chat(self, speaker: str, text: str, *, meta: str = "") -> None:
        suffix = f" <span style='color:#7d98b5;font-size:10px;'>({meta})</span>" if meta else ""
        color = "#66fcf1" if speaker == "Чубрик" else "#ffd54f"
        self._chat_log.append(f"<b style='color:{color};'>{speaker}:</b> {text}{suffix}")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        # Hide panel instead of closing the whole desktop pet
        event.ignore()
        self.hide()
