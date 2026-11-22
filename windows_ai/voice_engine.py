"""PC-only Voice Control (GS Speech-to-Text + Text-to-Speech) engine for GCS Shimeji AI."""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

DEFAULT_WAKE_WORDS: tuple[str, ...] = (
    "чубрик",
    "чубрика",
    "чубрику",
    "шимеджи",
    "шимежи",
    "shimeji",
    "малыш",
    "питомец",
)


@dataclass(frozen=True)
class MicrophoneDevice:
    """Represents one PC audio input device."""

    index: int | None
    name: str


def list_input_microphones() -> list[MicrophoneDevice]:
    """Return available PC microphone input devices via sounddevice (safe on headless hosts)."""
    devices: list[MicrophoneDevice] = [MicrophoneDevice(index=None, name="Системный микрофон по умолчанию")]
    try:
        import sounddevice as sd

        for idx, info in enumerate(sd.query_devices()):
            if int(info.get("max_input_channels", 0)) > 0:
                name = str(info.get("name", f"Microphone #{idx}")).strip()
                devices.append(MicrophoneDevice(index=idx, name=f"[{idx}] {name}"))
    except Exception as exc:
        logger.debug("Could not enumerate sounddevice microphones: %s", exc)
    return devices


def extract_after_wake_word(
    transcript: str,
    *,
    require_wake_word: bool = False,
    wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS,
) -> str | None:
    """Filter recognized speech by optional wake word and strip the wake prefix.

    Returns:
        - Cleaned utterance to send to the AI brain, or
        - None if `require_wake_word=True` and no wake word was spoken.
    """
    text = (transcript or "").strip()
    if not text:
        return None

    low = text.lower()
    for word in wake_words:
        pattern = rf"(?:^|\b)(?:эй\s+|окей\s+|привет\s+)?{re.escape(word.lower())}(?:\b|[,!?:.\s])"
        match = re.search(pattern, low)
        if match:
            remainder = text[match.end() :].lstrip(" ,.!?:;-")
            return remainder if remainder else "Привет!"

    if require_wake_word:
        return None
    return text


def record_utterance_sounddevice(
    *,
    device_index: int | None = None,
    sample_rate: int = 16000,
    wait_for_speech_sec: float = 4.5,
    max_phrase_sec: float = 8.5,
    silence_finish_sec: float = 0.95,
    min_speech_sec: float = 0.25,
    stop_event: threading.Event | None = None,
    on_level: Callable[[float], None] | None = None,
) -> bytes | None:
    """Capture one spoken phrase as 16-bit mono PCM bytes using sounddevice + adaptive RMS VAD."""
    import sounddevice as sd

    block_sec = 0.08
    block_size = int(sample_rate * block_sec)
    audio_queue: queue.Queue[np.ndarray] = queue.Queue()

    def _callback(indata: np.ndarray, _frames: int, _time_info: object, status: object) -> None:
        if status:
            logger.debug("sounddevice input status: %s", status)
        audio_queue.put(indata.copy())

    pre_roll: list[np.ndarray] = []
    recorded: list[np.ndarray] = []
    ambient_samples: list[float] = []
    noise_floor = 0.012
    speech_threshold = 0.024

    speech_started = False
    speech_elapsed = 0.0
    silence_elapsed = 0.0
    wait_elapsed = 0.0
    calibration_blocks = 4

    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=block_size,
        device=device_index,
        callback=_callback,
    ):
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                chunk = audio_queue.get(timeout=0.25)
            except queue.Empty:
                wait_elapsed += 0.25
                if not speech_started and wait_elapsed >= wait_for_speech_sec:
                    return None
                continue

            samples_f = chunk.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(samples_f * samples_f)))
            if on_level is not None:
                on_level(min(1.0, rms * 10.0))

            if len(ambient_samples) < calibration_blocks:
                ambient_samples.append(rms)
                pre_roll.append(chunk)
                if len(pre_roll) > 4:
                    pre_roll.pop(0)
                if len(ambient_samples) == calibration_blocks:
                    noise_floor = max(0.005, float(np.median(ambient_samples)))
                    speech_threshold = max(0.018, min(0.12, noise_floor * 2.4 + 0.008))
                continue

            if not speech_started:
                pre_roll.append(chunk)
                if len(pre_roll) > 4:
                    pre_roll.pop(0)
                if rms >= speech_threshold:
                    speech_started = True
                    recorded.extend(pre_roll)
                    speech_elapsed = block_sec
                    silence_elapsed = 0.0
                else:
                    # Slowly track drifting room noise while waiting
                    noise_floor = noise_floor * 0.92 + rms * 0.08
                    speech_threshold = max(0.018, min(0.12, noise_floor * 2.4 + 0.008))
                    wait_elapsed += block_sec
                    if wait_elapsed >= wait_for_speech_sec:
                        return None
            else:
                recorded.append(chunk)
                speech_elapsed += block_sec
                if rms >= speech_threshold * 0.72:
                    silence_elapsed = 0.0
                else:
                    silence_elapsed += block_sec

                if silence_elapsed >= silence_finish_sec and speech_elapsed >= min_speech_sec:
                    break
                if speech_elapsed >= max_phrase_sec:
                    break

    if not speech_started or speech_elapsed < min_speech_sec or not recorded:
        return None
    pcm = np.concatenate(recorded, axis=0).astype(np.int16)
    return pcm.tobytes()


def recognize_pcm_google(pcm_bytes: bytes, *, sample_rate: int = 16000, language: str = "ru-RU") -> str | None:
    """Transcribe 16-bit mono PCM audio via SpeechRecognition Google Web Speech API."""
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    audio = sr.AudioData(pcm_bytes, sample_rate, 2)
    try:
        text = recognizer.recognize_google(audio, language=language)
        return text.strip() if isinstance(text, str) and text.strip() else None
    except sr.UnknownValueError:
        return None
    except sr.RequestError as exc:
        raise RuntimeError(f"Ошибка сервиса распознавания речи: {exc}") from exc


class SpeechSynthesizer:
    """Background Windows SAPI5 / pyttsx3 voice output queue."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._speaking = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gcs-shimeji-tts", daemon=True)
        self._thread.start()

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def speak(self, text: str) -> None:
        if not self.enabled:
            return
        cleaned = re.sub(r"[^\w\sа-яА-ЯёЁa-zA-Z0-9,.!?:-]", " ", (text or "")).strip()
        if not cleaned:
            return
        # Drop stale queued replies so the pet always speaks the latest reply immediately
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(cleaned)

    def shutdown(self) -> None:
        self._stop.set()
        self._queue.put(None)

    def _run(self) -> None:
        engine = None
        while not self._stop.is_set():
            text = self._queue.get()
            if text is None or self._stop.is_set():
                break
            if not self.enabled:
                continue
            self._speaking.set()
            try:
                if engine is None:
                    import pyttsx3

                    engine = pyttsx3.init()
                    engine.setProperty("rate", 178)
                engine.say(text)
                engine.runAndWait()
            except Exception as exc:
                logger.debug("TTS output unavailable: %s", exc)
                engine = None
            finally:
                self._speaking.clear()


class VoiceController(QObject):
    """Coordinates push-to-talk and continuous GS voice listening plus TTS output."""

    transcript_ready = Signal(str)
    status_changed = Signal(str)
    level_changed = Signal(float)
    listening_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(
        self,
        *,
        language: str = "ru-RU",
        device_index: int | None = None,
        require_wake_word: bool = False,
        tts_enabled: bool = True,
    ) -> None:
        super().__init__()
        self._language = language
        self._device_index = device_index
        self._require_wake_word = require_wake_word
        self._tts = SpeechSynthesizer(enabled=tts_enabled)

        self._continuous = False
        self._busy_single = False
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    @property
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        self._language = value or "ru-RU"

    @property
    def device_index(self) -> int | None:
        return self._device_index

    @device_index.setter
    def device_index(self, value: int | None) -> None:
        self._device_index = value

    @property
    def require_wake_word(self) -> bool:
        return self._require_wake_word

    @require_wake_word.setter
    def require_wake_word(self, value: bool) -> None:
        self._require_wake_word = bool(value)

    @property
    def tts_enabled(self) -> bool:
        return self._tts.enabled

    @tts_enabled.setter
    def tts_enabled(self, value: bool) -> None:
        self._tts.enabled = bool(value)

    def speak(self, text: str) -> None:
        self._tts.speak(text)

    def listen_once(self) -> None:
        """Capture a single spoken voice command (Push-to-Talk)."""
        if self._continuous or self._busy_single:
            return
        self._busy_single = True
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._single_listen_worker,
            name="gcs-shimeji-voice-once",
            daemon=True,
        )
        self._worker_thread.start()

    def set_continuous(self, enabled: bool) -> None:
        """Toggle always-on continuous background voice listening."""
        if enabled == self._continuous:
            return
        if enabled:
            self._continuous = True
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._continuous_worker,
                name="gcs-shimeji-voice-loop",
                daemon=True,
            )
            self._worker_thread.start()
        else:
            self._continuous = False
            self._stop_event.set()
            self.listening_changed.emit(False)
            self.level_changed.emit(0.0)
            self.status_changed.emit("ГС остановлено (нажмите кнопку или включите непрерывное ГС)")

    def _single_listen_worker(self) -> None:
        self.listening_changed.emit(True)
        self.status_changed.emit("🎤 Слушаю... говорите команду или вопрос!")
        try:
            pcm = record_utterance_sounddevice(
                device_index=self._device_index,
                wait_for_speech_sec=5.0,
                max_phrase_sec=8.5,
                stop_event=self._stop_event,
                on_level=self.level_changed.emit,
            )
            self.level_changed.emit(0.0)
            if pcm is None:
                self.status_changed.emit("Тишина — речь не услышана. Попробуйте снова!")
                return
            self.status_changed.emit("🔎 Распознаю речь...")
            text = recognize_pcm_google(pcm, language=self._language)
            if not text:
                self.status_changed.emit("Не удалось разобрать слова. Попробуйте чуть громче!")
                return
            self.status_changed.emit(f"Услышано: «{text}»")
            self.transcript_ready.emit(text)
        except Exception as exc:
            msg = str(exc)
            logger.warning("Voice listen_once error: %s", msg)
            self.error_occurred.emit(msg)
            self.status_changed.emit(f"Ошибка микрофона/ГС: {msg}")
        finally:
            self._busy_single = False
            self.level_changed.emit(0.0)
            self.listening_changed.emit(False)

    def _continuous_worker(self) -> None:
        self.listening_changed.emit(True)
        self.status_changed.emit("🎤 Непрерывное ГС активно (скажите команду)...")
        while self._continuous and not self._stop_event.is_set():
            if self._tts.is_speaking:
                time.sleep(0.15)
                continue
            try:
                pcm = record_utterance_sounddevice(
                    device_index=self._device_index,
                    wait_for_speech_sec=3.0,
                    max_phrase_sec=8.0,
                    stop_event=self._stop_event,
                    on_level=self.level_changed.emit,
                )
                self.level_changed.emit(0.0)
                if pcm is None or self._stop_event.is_set() or self._tts.is_speaking:
                    continue
                self.status_changed.emit("🔎 Распознаю речь...")
                raw_text = recognize_pcm_google(pcm, language=self._language)
                if not raw_text:
                    self.status_changed.emit("🎤 Слушаю...")
                    continue
                filtered = extract_after_wake_word(
                    raw_text,
                    require_wake_word=self._require_wake_word,
                )
                if filtered is None:
                    self.status_changed.emit(f"Пропущено (нет слова «Чубрик»): «{raw_text}»")
                    continue
                self.status_changed.emit(f"Услышано: «{filtered}»")
                self.transcript_ready.emit(filtered)
            except Exception as exc:
                msg = str(exc)
                logger.warning("Continuous voice loop error: %s", msg)
                self.error_occurred.emit(msg)
                self.status_changed.emit(f"Ошибка ГС: {msg}")
                time.sleep(1.5)

        self.level_changed.emit(0.0)
        self.listening_changed.emit(False)

    def shutdown(self) -> None:
        self._continuous = False
        self._stop_event.set()
        self._tts.shutdown()
