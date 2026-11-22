"""PC-only AI brain powered by g4f with voice-command grounding for GCS Shimeji."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from windows.pet_engine import PetEngine

logger = logging.getLogger(__name__)

VALID_EMOTIONS: frozenset[str] = frozenset(
    {"neutral", "happy", "love", "excited", "scared", "dizzy", "angry", "sleepy"}
)

VALID_ACTIONS: frozenset[str] = frozenset(
    {
        "none",
        "jump",
        "sleep",
        "wake",
        "walk_left",
        "walk_right",
        "run_center",
        "dance",
        "love",
        "send_to_phone",
        "scale_up",
        "scale_down",
        "enable_camera",
    }
)

DEFAULT_MODELS: tuple[str, ...] = (
    "default",
    "gpt-4o-mini",
    "gpt-4o",
    "llama-3.3-70b",
)

SYSTEM_PROMPT = """Ты — Чубрик, живой весёлый пиксельный питомец-компаньон (Shimeji), который живёт прямо на экране Windows ПК пользователя!
Пользователь общается с тобой голосом (ГС) или текстом и управляет твоими действиями.
Отвечай КОРОТКО (1-2 живых предложения, без длинных списков и markdown), на том же языке, на котором говорит пользователь (обычно по-русски).

ВСЕГДА отвечай строго в формате JSON-объекта без лишнего текста вокруг:
{"reply": "твой короткий живой ответ", "emotion": "одна из эмоций", "action": "одно из действий"}

Доступные значения emotion (меняют твой цвет на экране!):
- "neutral" (бирюзовый, спокойный)
- "happy" (жёлтый, радостный)
- "love" (розовый с сердечками, ласковый/влюблённый)
- "excited" (зелёный, в восторге/энергичный)
- "scared" (ледяной синий, напуганный)
- "dizzy" (фиолетовый, кружится голова)
- "angry" (красный, злой/обиженный)
- "sleepy" (лавандовый, сонный)

Доступные значения action:
- "none" (просто поболтать)
- "jump" (подпрыгнуть)
- "sleep" (лечь спать)
- "wake" (проснуться)
- "walk_left" (пойти налево)
- "walk_right" (пойти направо)
- "run_center" (прибежать в центр экрана)
- "dance" (весело попрыгать и потанцевать)
- "love" (показать сердечки и помурчать)
- "send_to_phone" (перелететь на телефон)
- "scale_up" (стать больше)
- "scale_down" (стать меньше)
- "enable_camera" (включить управление камерой/жестами)
"""


@dataclass(frozen=True)
class PetAIResponse:
    """Structured decision produced by the AI brain or fast voice-command matcher."""

    reply: str
    emotion: str = "neutral"
    action: str = "none"
    provider_model: str = ""
    used_g4f: bool = False
    error: str | None = None


@dataclass(frozen=True)
class LocalCommandHint:
    """Fast local voice-command recognition result."""

    action: str
    emotion: str
    fallback_reply: str


def parse_local_voice_command(text: str) -> LocalCommandHint | None:
    """Detect direct voice commands in Russian or English for zero-latency action grounding."""
    clean = re.sub(r"[^\w\sёЁа-яА-Яa-zA-Z-]", " ", (text or "").lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return None

    # Phone handoff
    if any(k in clean for k in ("на телефон", "в телефон", "телефон", "send to phone", "go to phone", "to phone")):
        if any(v in clean for v in ("лети", "иди", "отправ", "перейд", "перелет", "прыг", "давай", "на телефон", "в телефон", "send", "go", "to phone")):
            return LocalCommandHint("send_to_phone", "excited", "Лечу на телефон! Держи меня!")

    # Camera activation
    if any(k in clean for k in ("включи камер", "запусти камер", "камеру включ", "enable camera", "start camera")):
        return LocalCommandHint("enable_camera", "excited", "Включаю камеру! Маши мне рукой!")

    # Sleep / wake
    if any(k in clean for k in ("спи", "спать", "бай", "усни", "ложись", "sleep", "go to sleep", "nap")):
        return LocalCommandHint("sleep", "sleepy", "Зеваю... ложусь спать, Zzz...")
    if any(k in clean for k in ("просн", "подъем", "подъём", "вставай", "хватит спать", "wake up", "wake")):
        return LocalCommandHint("wake", "happy", "Я проснулся! Готов к приключениям!")

    # Dance / party
    if any(k in clean for k in ("танц", "потанц", "диско", "dance", "party")):
        return LocalCommandHint("dance", "excited", "Ура, танцы! Смотри как умею!")

    # Jump
    if any(k in clean for k in ("прыг", "подпрыг", "скачи", "jump", "hop")):
        return LocalCommandHint("jump", "happy", "Прыг-скок! Выше всех!")

    # Movement
    if any(k in clean for k in ("налево", "влево", "левее", "go left", "walk left", "move left")):
        return LocalCommandHint("walk_left", "excited", "Топаю налево!")
    if any(k in clean for k in ("направо", "вправо", "правее", "go right", "walk right", "move right")):
        return LocalCommandHint("walk_right", "excited", "Бегу направо!")
    if any(k in clean for k in ("в центр", "на середину", "ко мне", "сюда", "center", "come here")):
        return LocalCommandHint("run_center", "happy", "Уже бегу в центр экрана!")

    # Size
    if any(k in clean for k in ("больше", "увеличь", "выраст", "scale up", "bigger", "grow")):
        return LocalCommandHint("scale_up", "excited", "Ого, я расту! Стал больше!")
    if any(k in clean for k in ("меньше", "уменьш", "маленьк", "scale down", "smaller", "shrink")):
        return LocalCommandHint("scale_down", "happy", "Оп! Стал компактным малышом!")

    # Emotions / colors
    if any(k in clean for k in ("любл", "поглад", "глажу", "хороший", "молодец", "милый", "сердечк", "love", "good boy", "cute")):
        return LocalCommandHint("love", "love", "Мур-мур! Обожаю тебя!")
    if any(k in clean for k in ("злис", "разозл", "злой", "красн", "angry", "mad")):
        return LocalCommandHint("none", "angry", "Гррр! Я сейчас очень грозный!")
    if any(k in clean for k in ("испуг", "страшн", "бойся", "scared", "boo")):
        return LocalCommandHint("none", "scared", "Ааа! Не пугай меня так!")
    if any(k in clean for k in ("круж", "голова круж", "фиолетов", "dizzy")):
        return LocalCommandHint("none", "dizzy", "Ой-ой, всё вокруг кружится...")
    if any(k in clean for k in ("обычн", "успокой", "нормальн", "нейтральн", "neutral", "calm")):
        return LocalCommandHint("none", "neutral", "Фух, я снова спокойный и бирюзовый.")

    return None


def infer_emotion_from_text(text: str) -> str:
    """Fallback emotion detector when an LLM replies with plain text instead of JSON."""
    low = (text or "").lower()
    if any(w in low for w in ("♥", "❤", "любл", "мур", "обожа", "мило", "love", "heart")):
        return "love"
    if any(w in low for w in ("грр", "злой", "бесит", "злюсь", "angry", "хватит")):
        return "angry"
    if any(w in low for w in ("ааа", "страш", "боюсь", "испуг", "scared", "ой!")):
        return "scared"
    if any(w in low for w in ("круж", "@_@", "dizzy")):
        return "dizzy"
    if any(w in low for w in ("zzz", "спать", "зева", "сонн", "sleep")):
        return "sleepy"
    if any(w in low for w in ("ура", "круто", "ого", "вау", "супер", "excited", "погнали")):
        return "excited"
    if any(w in low for w in ("ха-ха", "хех", "привет", "рад", "счастл", "happy", "отлично", "!")):
        return "happy"
    return "neutral"


def parse_ai_json_response(raw_text: str, *, provider_model: str = "") -> PetAIResponse:
    """Parse structured JSON {reply, emotion, action} from g4f output with graceful text fallback."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return PetAIResponse(reply="Хм, я задумался...", emotion="neutral", action="none", provider_model=provider_model, used_g4f=True)

    # Strip ```json ... ``` fences if present
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    candidate_str = fenced.group(1) if fenced else None

    if candidate_str is None:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            candidate_str = cleaned[start : end + 1]

    if candidate_str:
        try:
            data = json.loads(candidate_str)
            if isinstance(data, dict):
                reply = str(data.get("reply") or data.get("text") or data.get("message") or "").strip()
                emotion = str(data.get("emotion") or "neutral").strip().lower()
                action = str(data.get("action") or "none").strip().lower()
                if emotion not in VALID_EMOTIONS:
                    emotion = infer_emotion_from_text(reply)
                if action not in VALID_ACTIONS:
                    action = "none"
                if not reply:
                    reply = "Готово!"
                return PetAIResponse(
                    reply=reply[:240],
                    emotion=emotion,
                    action=action,
                    provider_model=provider_model,
                    used_g4f=True,
                )
        except (ValueError, TypeError):
            pass

    # Graceful fallback when provider replies with plain text
    plain = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL).strip()
    plain = re.sub(r"\s+", " ", plain)
    return PetAIResponse(
        reply=plain[:240] or "Я тебя слышу!",
        emotion=infer_emotion_from_text(plain),
        action="none",
        provider_model=provider_model,
        used_g4f=True,
    )


CompletionFn = Callable[[str, list[dict[str, str]]], str]


class ShimejiAIBrain:
    """Stateful g4f-powered conversational & command brain for the Windows desktop pet."""

    def __init__(
        self,
        *,
        model: str = "default",
        max_history_turns: int = 8,
        completion_fn: CompletionFn | None = None,
    ) -> None:
        self.model = (model or "default").strip()
        self._max_history_turns = max(1, int(max_history_turns))
        self._history: list[dict[str, str]] = []
        self._completion_fn = completion_fn
        self._g4f_client: Any = None

    def clear_history(self) -> None:
        self._history.clear()

    def _default_g4f_complete(self, model_name: str, messages: list[dict[str, str]]) -> str:
        if self._g4f_client is None:
            from g4f.client import Client

            self._g4f_client = Client()
        response = self._g4f_client.chat.completions.create(
            model=model_name,
            messages=messages,
            stream=False,
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Empty g4f response from model '{model_name}'")
        return content

    def _candidate_models(self) -> list[str]:
        ordered: list[str] = []
        for m in (self.model, "default", "gpt-4o-mini", "gpt-4o"):
            if m and m not in ordered:
                ordered.append(m)
        return ordered

    def ask(
        self,
        user_text: str,
        *,
        current_emotion: str = "neutral",
        current_state: str = "ACTIVE",
        use_g4f: bool = True,
    ) -> PetAIResponse:
        """Process a voice or text utterance using g4f plus fast command grounding."""
        utterance = (user_text or "").strip()
        if not utterance:
            return PetAIResponse(reply="Я слушаю!", emotion="neutral", action="none")

        local_hint = parse_local_voice_command(utterance)

        if not use_g4f:
            if local_hint is not None:
                return PetAIResponse(
                    reply=local_hint.fallback_reply,
                    emotion=local_hint.emotion,
                    action=local_hint.action,
                    provider_model="local",
                    used_g4f=False,
                )
            return PetAIResponse(
                reply="Нейросеть g4f выключена — могу выполнять базовые команды (прыгни, спи, на телефон)!",
                emotion="neutral",
                action="none",
                provider_model="local",
                used_g4f=False,
            )

        context_note = f"[Состояние питомца: state={current_state}, emotion={current_emotion}]"
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + context_note},
            *self._history[-(self._max_history_turns * 2) :],
            {"role": "user", "content": utterance},
        ]

        complete = self._completion_fn or self._default_g4f_complete
        last_err: str | None = None

        for candidate in self._candidate_models():
            try:
                raw = complete(candidate, messages)
                parsed = parse_ai_json_response(raw, provider_model=candidate)

                # Ground explicit voice commands so the requested physical action always fires
                final_action = parsed.action
                final_emotion = parsed.emotion
                if local_hint is not None:
                    if final_action == "none" and local_hint.action != "none":
                        final_action = local_hint.action
                    if final_emotion == "neutral" and local_hint.emotion != "neutral":
                        final_emotion = local_hint.emotion

                result = PetAIResponse(
                    reply=parsed.reply,
                    emotion=final_emotion,
                    action=final_action,
                    provider_model=candidate,
                    used_g4f=True,
                )
                self._history.append({"role": "user", "content": utterance})
                self._history.append({"role": "assistant", "content": result.reply})
                if len(self._history) > self._max_history_turns * 2:
                    self._history = self._history[-(self._max_history_turns * 2) :]
                return result
            except Exception as exc:
                last_err = f"{candidate}: {exc}"
                logger.warning("g4f attempt failed (%s): %s", candidate, exc)

        # Graceful offline/provider fallback so voice commands NEVER stop working
        if local_hint is not None:
            return PetAIResponse(
                reply=local_hint.fallback_reply,
                emotion=local_hint.emotion,
                action=local_hint.action,
                provider_model="local-fallback",
                used_g4f=False,
                error=last_err,
            )

        return PetAIResponse(
            reply="Ой, связь с нейросетью g4f сейчас барахлит... Попробуй ещё раз или скажи команду!",
            emotion="dizzy",
            action="none",
            provider_model="fallback",
            used_g4f=False,
            error=last_err,
        )


def execute_pet_ai_action(
    engine: PetEngine,
    response: PetAIResponse,
    *,
    on_handoff: Callable[[], None] | None = None,
    on_scale_delta: Callable[[int], None] | None = None,
    on_enable_camera: Callable[[str], None] | None = None,
    on_schedule_dance: Callable[[], None] | None = None,
) -> str:
    """Apply an AI response's emotion (color + FX) and physical action to PetEngine."""
    action = (response.action or "none").strip().lower()
    emotion = (response.emotion or "neutral").strip().lower()

    if emotion == "neutral":
        engine.set_emotion("neutral", duration_seconds=0.0)
    else:
        engine.set_emotion(emotion, duration_seconds=6.5)

    screen = engine.bounds.primary_screen

    if action == "jump":
        if engine.state == "SLEEPING":
            engine.wake_up()
        engine.trigger_jump()
        if emotion != "neutral":
            engine.set_emotion(emotion, duration_seconds=6.0)
        return "jump"

    if action == "sleep":
        engine.trigger_sleep()
        return "sleep"

    if action == "wake":
        engine.wake_up()
        if emotion != "neutral":
            engine.set_emotion(emotion, duration_seconds=5.5)
        return "wake"

    if action == "love":
        engine.pet_with_love()
        return "love"

    if action == "walk_left":
        if engine.state == "SLEEPING":
            engine.wake_up()
        engine.guide_toward_x(float(screen.left + max(40, engine.pet_width)))
        if emotion != "neutral":
            engine.set_emotion(emotion, duration_seconds=5.5)
        return "walk_left"

    if action == "walk_right":
        if engine.state == "SLEEPING":
            engine.wake_up()
        engine.guide_toward_x(float(screen.right - max(40, engine.pet_width)))
        if emotion != "neutral":
            engine.set_emotion(emotion, duration_seconds=5.5)
        return "walk_right"

    if action == "run_center":
        if engine.state == "SLEEPING":
            engine.wake_up()
        engine.guide_toward_x(float(screen.left + screen.width / 2.0))
        if emotion != "neutral":
            engine.set_emotion(emotion, duration_seconds=5.5)
        return "run_center"

    if action == "dance":
        if engine.state == "SLEEPING":
            engine.wake_up()
        engine.celebrate_peace()
        if on_schedule_dance is not None:
            on_schedule_dance()
        return "dance"

    if action == "send_to_phone":
        if on_handoff is not None:
            on_handoff()
        return "send_to_phone"

    if action == "scale_up":
        if on_scale_delta is not None:
            on_scale_delta(1)
        return "scale_up"

    if action == "scale_down":
        if on_scale_delta is not None:
            on_scale_delta(-1)
        return "scale_down"

    if action == "enable_camera":
        if on_enable_camera is not None:
            on_enable_camera("webcam")
        return "enable_camera"

    return "none"

