"""Unit tests for classic GCS Shimeji website reactions, cozy profile, and interactive games."""

from __future__ import annotations

import random
from pathlib import Path

from shared.config_loader import load_avatar_config
from windows.pet_engine import DesktopBounds, PetEngine, ScreenRect
from windows.pet_profile import PetProfile
from windows.site_reactor import SiteActivity, SiteReactor, classify_window_title
from windows.toy_physics import (
    BalloonPopGame,
    FetchBallState,
    PettingStrokeDetector,
    SnackDropState,
)


def test_site_title_classification_youtube_music_coding_games_and_self_ignore() -> None:
    yt = classify_window_title("Lo-fi Beats — YouTube - Google Chrome")
    assert yt is not None
    assert yt.activity == SiteActivity.VIDEO_POPCORN
    assert yt.prop_name == "popcorn"
    assert yt.prefer_sit is True

    # YouTube Music must match MUSIC_HEADPHONES rather than generic YouTube video!
    ytm = classify_window_title("Synthwave Mix - YouTube Music - Brave")
    assert ytm is not None
    assert ytm.activity == SiteActivity.MUSIC_HEADPHONES
    assert ytm.prop_name == "headphones"

    code = classify_window_title("pet_engine.py - ShimedjyArena - Visual Studio Code")
    assert code is not None
    assert code.activity == SiteActivity.CODING_COFFEE
    assert code.prop_name == "coffee"

    game = classify_window_title("Steam - Library")
    assert game is not None
    assert game.activity == SiteActivity.GAMING_GAMEPAD
    assert game.prop_name == "gamepad"

    # Never react to our own pet window
    assert classify_window_title("GCS Shimeji") is None


def test_site_reactor_debounce_manual_preview_and_quips() -> None:
    reactor = SiteReactor(enabled=True, debounce_polls=2, quip_interval_sec=10.0, rng=random.Random(7))

    # Single poll does not switch yet (debounce against quick Alt+Tab flicker)
    u1 = reactor.update("Cat Video - YouTube", now=1.0)
    assert u1.reaction is None and u1.activity_changed is False

    # Second consecutive poll confirms YouTube -> popcorn + quip!
    u2 = reactor.update("Cat Video - YouTube", now=1.8)
    assert u2.reaction is not None
    assert u2.reaction.prop_name == "popcorn"
    assert u2.activity_changed is True
    assert u2.new_phrase is not None

    # Periodic munching quip after quip_interval_sec
    u3 = reactor.update("Cat Video - YouTube", now=12.5)
    assert u3.activity_changed is False
    assert u3.new_phrase is not None

    # Manual preview overrides foreground window cleanly
    up = reactor.set_manual_preview(SiteActivity.MUSIC_HEADPHONES, now=13.0, duration_sec=10.0)
    assert up.reaction is not None and up.reaction.prop_name == "headphones"
    assert reactor.current_reaction is not None and reactor.current_reaction.prop_name == "headphones"


def test_fetch_ball_snack_balloon_and_petting_games() -> None:
    bounds = DesktopBounds([ScreenRect(0, 0, 1280, 720, is_primary=True)])
    screen = bounds.primary_screen

    # 1) Fetch ball bounces off floor and pet catches & kicks it
    ball = FetchBallState(x=300.0, y=500.0, vx=120.0, vy=50.0, radius=18.0)
    for _ in range(30):
        ball.tick(1.0 / 60.0, bounds)
    assert ball.y + ball.radius * 2 <= screen.bottom + 1.0
    assert ball.is_caught_by_pet(ball.center_x, ball.center_y, 128, 128) is True
    ball.kick_by_pet(random.Random(1))
    assert ball.catches == 1
    assert ball.vy < -300.0
    assert ball.catch_cooldown > 0.5

    # 2) Snack drops to floor and is eaten by pet
    snack = SnackDropState(x=500.0, y=200.0, kind="donut", size=36)
    for _ in range(180):
        snack.tick(1.0 / 60.0, bounds)
    assert abs(snack.y - (screen.bottom - 36)) < 1.0
    assert snack.is_eaten_by_pet(snack.center_x, snack.center_y, 128, 128) is True

    # 3) Balloon Pop spawns, rises, pops on click & pet collision, and finishes cleanly
    game = BalloonPopGame(duration_sec=5.0, _rng=random.Random(3))
    game.start(screen)
    assert game.active is True and len(game.balloons) == 3
    first_id = game.balloons[0].balloon_id
    assert game.pop_balloon(first_id) is not None
    assert game.score == 1

    # Pet collides with another balloon
    b2 = next(b for b in game.balloons if not b.popped)
    pet_popped, finished = game.tick(0.05, screen, b2.center_x, b2.center_y, 128, 128)
    assert len(pet_popped) >= 1 and finished is False
    assert game.score >= 2

    # Advance past duration -> game completes cleanly
    _, finished_end = game.tick(6.0, screen, 0.0, 0.0, 128, 128)
    assert finished_end is True and game.active is False

    # 4) Back-and-forth head petting strokes trigger purr
    petting = PettingStrokeDetector(reversals_required=3, window_sec=2.0, cooldown_sec=2.0)
    assert petting.feed(40.0, 20.0, 128, now=1.0) is False
    assert petting.feed(70.0, 20.0, 128, now=1.1) is False
    assert petting.feed(42.0, 22.0, 128, now=1.2) is False
    assert petting.feed(72.0, 22.0, 128, now=1.3) is False
    assert petting.feed(40.0, 22.0, 128, now=1.4) is True


def test_pet_profile_persistence_and_sit_and_watch(tmp_path: Path) -> None:
    p = PetProfile()
    assert p.level == 1
    for _ in range(5):
        p.record_ball_catch()
    assert p.level >= 2
    assert p.balls_caught == 5

    save_path = tmp_path / "pet_profile.json"
    p.save(save_path)
    loaded = PetProfile.load(save_path)
    assert loaded.xp == p.xp
    assert loaded.balls_caught == 5
    assert loaded.level == p.level

    # PetEngine.sit_and_watch calmly seats pet with emotion & prevents sleep
    cfg = load_avatar_config()
    engine = PetEngine(cfg, DesktopBounds([ScreenRect(0, 0, 1280, 720)]), pet_width=128, pet_height=128, state_file=tmp_path / "pos.json")
    engine.vx = 140.0
    engine.animation = "walk"
    engine.sit_and_watch("happy")
    assert engine.vx == 0.0
    assert engine.animation == "idle"
    assert engine.emotion == "happy"
