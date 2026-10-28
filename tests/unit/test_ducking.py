"""压低音量的步进和比例限制。"""
from utils.audio_handle.ducking import clamp_ratio, duck_step


def test_duck_down_is_faster_than_recover():
    down = 1.0 - duck_step(1.0, 0.3)
    up = duck_step(0.3, 1.0) - 0.3
    assert down > up


def test_duck_step_never_overshoots_target():
    assert duck_step(0.35, 0.3) == 0.3
    assert duck_step(0.98, 1.0) == 1.0
    assert duck_step(0.5, 0.5) == 0.5


def test_duck_reaches_target_in_finite_steps():
    level, steps = 1.0, 0
    while level != 0.3 and steps < 100:
        level, steps = duck_step(level, 0.3), steps + 1
    assert level == 0.3 and steps <= 6

    steps = 0
    while level != 1.0 and steps < 100:
        level, steps = duck_step(level, 1.0), steps + 1
    assert level == 1.0 and steps <= 20


def test_clamp_ratio():
    assert clamp_ratio(0.3, 0.5) == 0.3
    assert clamp_ratio(5, 0.5) == 1.0
    assert clamp_ratio(-1, 0.5) == 0.0
    assert clamp_ratio(None, 0.5) == 0.5
    assert clamp_ratio("abc", 0.5) == 0.5
    # 0 是有意义的（表示不压低而是暂停），不能被当成“没配置”
    assert clamp_ratio(0, 0.5) == 0.0
