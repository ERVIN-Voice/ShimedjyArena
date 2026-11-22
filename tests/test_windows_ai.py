"""Tests for the isolated PC-only AI (g4f) + Voice Control (GS) edition in windows_ai/."""

from __future__ import annotations

import random
from pathlib import Path

from shared.config_loader import load_avatar_config
from windows.pet_engine import DesktopBounds, PetEngine, ScreenRect
from windows_ai.ai_brain import (
    PetAIResponse,
    ShimejiAIBrain,
    execute_pet_ai_action,
    parse_ai_json_response,
    parse_local_voice_command,
)
from windows_ai.main import parse_args
from windows_ai.voice_engine import extract_after_wake_word


def test_local_voice_command_recognition_ru_and_en() -> None:
    assert parse_local_voice_command("Чубрик, прыгни выше!") is not None
    assert parse_local_voice_command("прыгни").action == "jump"  # type: ignore[union-attr]
    assert parse_local_voice_command("ложись спать").action == "sleep"  # type: ignore[union-attr]
    assert parse_local_voice_command("проснись малыш").action == "wake"  # type: ignore[union-attr]
    assert parse_local_voice_command("иди налево").action == "walk_left"  # type: ignore[union-attr]
    assert parse_local_voice_command("иди направо").action == "walk_right"  # type: ignore[union-attr]
    assert parse_local_voice_command("лети на телефон").action == "send_to_phone"  # type: ignore[union-attr]
    assert parse_local_voice_command("станцуй для меня").action == "dance"  # type: ignore[union-attr]
    assert parse_local_voice_command("я тебя люблю").emotion == "love"  # type: ignore[union-attr]
    assert parse_local_voice_command("разозлись").emotion == "angry"  # type: ignore[union-attr]
    assert parse_local_voice_command("включи камеру").action == "enable_camera"  # type: ignore[union-attr]
    assert parse_local_voice_command("стань больше").action == "scale_up"  # type: ignore[union-attr]
    assert parse_local_voice_command("стань меньше").action == "scale_down"  # type: ignore[union-attr]
    assert parse_local_voice_command("send to phone please").action == "send_to_phone"  # type: ignore[union-attr]


def test_parse_ai_json_response_variants() -> None:
    r1 = parse_ai_json_response(
        '{"reply": "Ура, прыгаю!", "emotion": "excited", "action": "jump"}',
        provider_model="gpt-4o-mini",
    )
    assert r1.reply == "Ура, прыгаю!"
    assert r1.emotion == "excited"
    assert r1.action == "jump"
    assert r1.used_g4f is True

    # Markdown code-fenced JSON
    r2 = parse_ai_json_response(
        'Вот ответ:\n```json\n{"reply": "Мур!", "emotion": "love", "action": "love"}\n```',
        provider_model="default",
    )
    assert r2.reply == "Мур!"
    assert r2.emotion == "love"
    assert r2.action == "love"

    # Plain-text fallback infers emotion gracefully
    r3 = parse_ai_json_response("Ха-ха, привет! Я рад тебя видеть!", provider_model="default")
    assert "привет" in r3.reply.lower()
    assert r3.emotion == "happy"
    assert r3.action == "none"


def test_shimeji_ai_brain_g4f_fallback_and_command_grounding() -> None:
    attempted: list[str] = []

    def flaky_provider(model: str, messages: list[dict[str, str]]) -> str:
        attempted.append(model)
        if model == "broken-model":
            raise RuntimeError("provider 503")
        # Return valid text reply with action="none", verifying local command grounding still triggers "jump"
        return '{"reply": "Смотри как высоко!", "emotion": "happy", "action": "none"}'

    brain = ShimejiAIBrain(model="broken-model", completion_fn=flaky_provider)
    res = brain.ask("подпрыгни пожалуйста")
    assert attempted[:2] == ["broken-model", "default"]
    assert res.used_g4f is True
    assert res.provider_model == "default"
    assert res.reply == "Смотри как высоко!"
    assert res.emotion == "happy"
    assert res.action == "jump"

    # When all g4f providers fail, direct voice commands still work offline!
    def always_fail(model: str, messages: list[dict[str, str]]) -> str:
        raise RuntimeError("network offline")

    offline_brain = ShimejiAIBrain(model="default", completion_fn=always_fail)
    res_off = offline_brain.ask("лети на телефон")
    assert res_off.used_g4f is False
    assert res_off.action == "send_to_phone"
    assert res_off.emotion == "excited"


def test_wake_word_filter() -> None:
    # Free mode accepts any speech
    assert extract_after_wake_word("прыгни сейчас", require_wake_word=False) == "прыгни сейчас"
    # Wake-word mode ignores unrelated chatter
    assert extract_after_wake_word("просто разговор в комнате", require_wake_word=True) is None
    # Wake-word mode strips prefix cleanly
    assert extract_after_wake_word("Эй Чубрик, расскажи анекдот", require_wake_word=True) == "расскажи анекдот"
    assert extract_after_wake_word("чубрик", require_wake_word=True) == "Привет!"


def test_execute_pet_ai_actions_on_pet_engine(tmp_path: Path) -> None:
    cfg = load_avatar_config()
    bounds = DesktopBounds([ScreenRect(0, 0, 1920, 1080, is_primary=True)])
    engine = PetEngine(
        cfg,
        bounds,
        pet_width=128,
        pet_height=128,
        state_file=tmp_path / "ai_pet.json",
        rng=random.Random(7),
    )

    handoffs: list[bool] = []
    scales: list[int] = []
    cams: list[str] = []

    # Jump + angry color
    act = execute_pet_ai_action(engine, PetAIResponse(reply="Грр!", emotion="angry", action="jump"))
    assert act == "jump"
    assert engine.animation == "jump"
    assert engine.emotion == "angry"

    # Let pet land
    for _ in range(120):
        engine.tick(1.0 / 60.0)

    # Sleep -> SLEEPING + sleepy lavender color
    execute_pet_ai_action(engine, PetAIResponse(reply="Zzz", emotion="sleepy", action="sleep"))
    assert engine.state == "SLEEPING"
    assert engine.emotion == "sleepy"

    # Wake + walk left
    execute_pet_ai_action(engine, PetAIResponse(reply="Иду!", emotion="excited", action="walk_left"))
    assert engine.state == "ACTIVE"
    assert engine.facing == "left"
    assert engine.emotion == "excited"

    # Send to phone, scale, enable camera callbacks
    execute_pet_ai_action(
        engine,
        PetAIResponse(reply="Лечу!", emotion="excited", action="send_to_phone"),
        on_handoff=lambda: handoffs.append(True),
    )
    execute_pet_ai_action(
        engine,
        PetAIResponse(reply="Расту!", emotion="happy", action="scale_up"),
        on_scale_delta=scales.append,
    )
    execute_pet_ai_action(
        engine,
        PetAIResponse(reply="Камера!", emotion="excited", action="enable_camera"),
        on_enable_camera=cams.append,
    )
    assert handoffs == [True]
    assert scales == [1]
    assert cams == ["webcam"]


def test_ai_cli_parse_args() -> None:
    args = parse_args(
        ["--ai-model", "gpt-4o-mini", "--voice-lang", "en-US", "--continuous-voice", "--wake-word", "--no-tts", "--self-test"]
    )
    assert args.ai_model == "gpt-4o-mini"
    assert args.voice_lang == "en-US"
    assert args.continuous_voice is True
    assert args.wake_word is True
    assert args.no_tts is True
    assert args.self_test is True
