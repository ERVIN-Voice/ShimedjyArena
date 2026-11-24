"""Active window / website detector & reaction mapper for classic GCS Shimeji (no AI required).

Reads the active foreground window title natively via Win32 user32.GetForegroundWindow /
GetWindowTextW (zero browser extensions, zero network calls, zero CPU overhead) and maps
visited websites & apps to expressive pixel-art props, emotions, seated watching behavior,
and playful Russian thought-bubble quips.
"""

from __future__ import annotations

import ctypes
import platform
import random
import re
from dataclasses import dataclass
from enum import Enum


class SiteActivity(str, Enum):
    """High-level activity detected from the user's active browser tab or app."""

    NONE = "none"
    VIDEO_POPCORN = "video_popcorn"      # YouTube, Twitch, Netflix, Kinopoisk, anime...
    MUSIC_HEADPHONES = "music_headphones"  # Spotify, Yandex Music, SoundCloud...
    CODING_COFFEE = "coding_coffee"      # VS Code, PyCharm, GitHub, Terminal, StackOverflow...
    GAMING_GAMEPAD = "gaming_gamepad"    # Steam, Discord, Minecraft, CS2, Dota, Roblox...
    CHAT_PEEK = "chat_peek"              # Telegram, WhatsApp, VK, Reddit, Twitter/X...
    AI_IDEA = "ai_idea"                  # ChatGPT, Claude, Arena, Gemini...
    STUDY_BOOK = "study_book"            # Wikipedia, Google Docs, Word, Notion...
    SHOPPING_SPARKLE = "shopping_sparkle"  # Ozon, Wildberries, Amazon, AliExpress...


@dataclass(frozen=True)
class SiteReaction:
    """Visual and behavioral reaction descriptor for a detected website/application."""

    activity: SiteActivity
    site_label: str
    prop_name: str              # Pixel prop drawn on TransparentPetWindow ("popcorn", "headphones", ...)
    emotion: str                # Unified palette emotion ("happy", "excited", "love", ...)
    prefer_sit: bool            # Sit calmly facing screen (e.g. watching YouTube with popcorn!)
    phrases: tuple[str, ...]    # Short playful thought-bubble quips


# Ordered from most specific to broader matches so "YouTube Music" hits MUSIC before VIDEO.
_SITE_RULES: tuple[tuple[re.Pattern[str], SiteReaction], ...] = (
    # 1) Music streaming (must come before generic YouTube!)
    (
        re.compile(
            r"(youtube\s*music|spotify|яндекс\s*музык|yandex\s*music|soundcloud|"
            r"apple\s*music|vk\s*музык|вк\s*музык|deezer|last\.fm|aimp|foobar2000)",
            re.IGNORECASE,
        ),
        SiteReaction(
            activity=SiteActivity.MUSIC_HEADPHONES,
            site_label="Музыка",
            prop_name="headphones",
            emotion="excited",
            prefer_sit=False,
            phrases=(
                "О, качает! Надеваю наушники 🎧",
                "Топ трек! ♪ ♫",
                "Танцуем под бит! 🎶",
                "Включи погромче! 🎧",
            ),
        ),
    ),
    # 2) Video / Streaming / Cinema / Anime -> POPCORN & WATCHING!
    (
        re.compile(
            r"(youtube|ютуб|twitch|твич|кинопоиск|kinopoisk|netflix|rutube|рутуб|"
            r"vk\s*видео|vk\s*video|crunchyroll|jut\.su|shikimori|anilibria|animego|"
            r"tiktok|тикток|vimeo|ivi\.ru|okko|wink|vlc\s*media\s*player|mpv)",
            re.IGNORECASE,
        ),
        SiteReaction(
            activity=SiteActivity.VIDEO_POPCORN,
            site_label="Видео / YouTube",
            prop_name="popcorn",
            emotion="happy",
            prefer_sit=True,
            phrases=(
                "О, видосик! Несу попкорн 🍿",
                "Хрум-хрум... интересненько! 🍿",
                "Тсс, смотрим экран! 👀🍿",
                "Поделись попкорном? 🍿",
                "Классный момент! 🍿✨",
            ),
        ),
    ),
    # 3) Coding / Dev tools / GitHub -> Glasses & Coffee!
    (
        re.compile(
            r"(visual\s*studio\s*code|vs\s*code|pycharm|intellij|webstorm|clion|"
            r"android\s*studio|cursor|github|gitlab|stack\s*overflow|stackoverflow|"
            r"powershell|windows\s*terminal|cmd\.exe|sublime\s*text|neovim|leetcode|habr|хабр)",
            re.IGNORECASE,
        ),
        SiteReaction(
            activity=SiteActivity.CODING_COFFEE,
            site_label="Код / GitHub",
            prop_name="coffee",
            emotion="neutral",
            prefer_sit=True,
            phrases=(
                "Кодим без багов! ☕💻",
                "Заварил кофе для работы ☕",
                "Хмм, тут точно нет опечатки? 🤓",
                "Не забудь сделать commit! ☕",
            ),
        ),
    ),
    # 4) Games & Discord -> Gamepad!
    (
        re.compile(
            r"(steam|стим|discord|дискорд|minecraft|майнкрафт|roblox|роблокс|"
            r"counter-strike|cs2|dota\s*2|valorant|genshin|honkai|terraria|"
            r"epic\s*games|osu!|overwatch|apex\s*legends|league\s*of\s*legends|gta\s*v|cyberpunk)",
            re.IGNORECASE,
        ),
        SiteReaction(
            activity=SiteActivity.GAMING_GAMEPAD,
            site_label="Игры",
            prop_name="gamepad",
            emotion="excited",
            prefer_sit=False,
            phrases=(
                "Затащим катку! 🎮🔥",
                "Я за тебя болею! 🎮",
                "GG WP! Давай ещё! ✨",
                "Не мискликни! 🎮😄",
            ),
        ),
    ),
    # 5) AI / Smart tools -> Lightbulb!
    (
        re.compile(
            r"(chatgpt|openai|claude|arena\.ai|lmarena|gemini|deepseek|perplexity|grok)",
            re.IGNORECASE,
        ),
        SiteReaction(
            activity=SiteActivity.AI_IDEA,
            site_label="Идеи / AI",
            prop_name="idea",
            emotion="happy",
            prefer_sit=True,
            phrases=(
                "Ого, гениальные мысли! 💡",
                "Эврика! Я тоже умный! 💡✨",
                "Хмм, дай почитать! 🤓",
            ),
        ),
    ),
    # 6) Messengers & Social -> Peeking chat!
    (
        re.compile(
            r"(telegram|телеграм|whatsapp|вконтакте|\bvk\b|reddit|twitter|\bx\s*/|pikabu|пикабу|instagram)",
            re.IGNORECASE,
        ),
        SiteReaction(
            activity=SiteActivity.CHAT_PEEK,
            site_label="Чат / Соцсети",
            prop_name="chat",
            emotion="love",
            prefer_sit=False,
            phrases=(
                "Кто там написал? 👀💬",
                "Передай им привет от меня! 👋",
                "О, мемчики? Покажи! 😄",
            ),
        ),
    ),
    # 7) Study / Docs / Reading -> Book!
    (
        re.compile(
            r"(wikipedia|википедия|google\s*docs|google\s*документ|microsoft\s*word|"
            r"\.docx|\.pdf|notion|obsidian|coursera|stepik|duolingo|excel)",
            re.IGNORECASE,
        ),
        SiteReaction(
            activity=SiteActivity.STUDY_BOOK,
            site_label="Учёба / Текст",
            prop_name="book",
            emotion="neutral",
            prefer_sit=True,
            phrases=(
                "Тихо-тихо, учимся! 📚🤫",
                "Столько умных букв! 📖",
                "Ты молодец, так держать! ✨",
            ),
        ),
    ),
    # 8) Shopping -> Sparkles!
    (
        re.compile(
            r"(ozon|озон|wildberries|вайлдберриз|aliexpress|алиэкспресс|amazon|яндекс\s*маркет|avito|авито|kaspi)",
            re.IGNORECASE,
        ),
        SiteReaction(
            activity=SiteActivity.SHOPPING_SPARKLE,
            site_label="Магазин",
            prop_name="sparkle",
            emotion="love",
            prefer_sit=False,
            phrases=(
                "О, шопинг! Купи вкусняшку? 🛍️✨",
                "Какая красивая штука! ✨",
                "Берём! ...или подумаем? 😄",
            ),
        ),
    ),
)

_ACTIVITY_LOOKUP: dict[SiteActivity, SiteReaction] = {
    reaction.activity: reaction for _, reaction in _SITE_RULES
}


def get_reaction_for_activity(activity: SiteActivity) -> SiteReaction | None:
    """Look up a built-in reaction by SiteActivity (useful for manual menu previews)."""
    return _ACTIVITY_LOOKUP.get(activity)


def classify_window_title(title: str) -> SiteReaction | None:
    """Return a SiteReaction if the foreground window title matches a known site or app."""
    cleaned = (title or "").strip()
    if not cleaned:
        return None
    # Never react to our own Shimeji windows
    low = cleaned.lower()
    if "gcs shimeji" in low or "handmouse" in low:
        return None
    for pattern, reaction in _SITE_RULES:
        if pattern.search(cleaned):
            return reaction
    return None


def detect_foreground_window_title() -> str:
    """Read the active Windows foreground window title via user32 (returns '' on non-Windows)."""
    if platform.system() != "Windows":
        return ""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


@dataclass(frozen=True)
class SiteReactorUpdate:
    """Emitted when the pet's active site reaction or speech quip changes."""

    reaction: SiteReaction | None
    activity_changed: bool
    new_phrase: str | None = None


class SiteReactor:
    """Debounced active-site monitor with manual preview override and periodic quips."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        debounce_polls: int = 2,
        quip_interval_sec: float = 28.0,
        rng: random.Random | None = None,
    ) -> None:
        self.enabled: bool = bool(enabled)
        self._debounce_polls = max(1, int(debounce_polls))
        self._quip_interval_sec = max(5.0, float(quip_interval_sec))
        self._rng = rng or random.Random()

        self._candidate_activity: SiteActivity = SiteActivity.NONE
        self._candidate_streak: int = 0
        self._current_reaction: SiteReaction | None = None
        self._last_quip_t: float = 0.0

        # Optional manual prop preview (e.g. user clicked "Попкорн 🍿" in menu to test!)
        self._manual_reaction: SiteReaction | None = None
        self._manual_until: float = 0.0

    @property
    def current_reaction(self) -> SiteReaction | None:
        return self._manual_reaction or self._current_reaction

    def set_manual_preview(self, activity: SiteActivity, *, now: float, duration_sec: float = 25.0) -> SiteReactorUpdate:
        """Temporarily force a specific site reaction/prop so the user can enjoy it anytime."""
        if activity == SiteActivity.NONE:
            self._manual_reaction = None
            self._manual_until = 0.0
            return SiteReactorUpdate(reaction=self._current_reaction, activity_changed=True, new_phrase=None)
        reaction = get_reaction_for_activity(activity)
        self._manual_reaction = reaction
        self._manual_until = now + max(3.0, duration_sec)
        self._last_quip_t = now
        phrase = self._rng.choice(reaction.phrases) if reaction and reaction.phrases else None
        return SiteReactorUpdate(reaction=reaction, activity_changed=True, new_phrase=phrase)

    def clear_manual_preview(self) -> None:
        self._manual_reaction = None
        self._manual_until = 0.0

    def update(self, window_title: str, *, now: float) -> SiteReactorUpdate:
        """Poll with the current foreground window title and return any reaction change or quip."""
        # Respect active manual preview first
        if self._manual_reaction is not None:
            if now < self._manual_until:
                phrase = None
                if (now - self._last_quip_t) >= self._quip_interval_sec and self._manual_reaction.phrases:
                    self._last_quip_t = now
                    phrase = self._rng.choice(self._manual_reaction.phrases)
                return SiteReactorUpdate(
                    reaction=self._manual_reaction,
                    activity_changed=False,
                    new_phrase=phrase,
                )
            self._manual_reaction = None
            self._manual_until = 0.0

        if not self.enabled:
            changed = self._current_reaction is not None
            self._current_reaction = None
            self._candidate_activity = SiteActivity.NONE
            self._candidate_streak = 0
            return SiteReactorUpdate(reaction=None, activity_changed=changed, new_phrase=None)

        detected = classify_window_title(window_title)
        det_act = detected.activity if detected is not None else SiteActivity.NONE

        if det_act == self._candidate_activity:
            self._candidate_streak += 1
        else:
            self._candidate_activity = det_act
            self._candidate_streak = 1

        cur_act = self._current_reaction.activity if self._current_reaction is not None else SiteActivity.NONE
        if self._candidate_streak >= self._debounce_polls and det_act != cur_act:
            self._current_reaction = detected
            self._last_quip_t = now
            phrase = self._rng.choice(detected.phrases) if detected and detected.phrases else None
            return SiteReactorUpdate(
                reaction=self._current_reaction,
                activity_changed=True,
                new_phrase=phrase,
            )

        # Occasional cozy quip while staying on the same site (e.g. munching popcorn on YouTube)
        if (
            self._current_reaction is not None
            and self._current_reaction.phrases
            and (now - self._last_quip_t) >= self._quip_interval_sec
        ):
            self._last_quip_t = now
            return SiteReactorUpdate(
                reaction=self._current_reaction,
                activity_changed=False,
                new_phrase=self._rng.choice(self._current_reaction.phrases),
            )

        return SiteReactorUpdate(reaction=self._current_reaction, activity_changed=False, new_phrase=None)
