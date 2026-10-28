"""BGM 对人声和点歌的让位：人声来了压低音量，点歌的歌在放才暂停。"""
import pytest

from utils.audio_handle import bgm_player
from utils.audio_handle.bgm_player import BGMPlayer


class FakeStream:
    def __init__(self):
        self.volume = None
        self.paused = False
        self.busy = True
        self.calls = []

    def get_busy(self):
        return self.busy

    def set_volume(self, v):
        self.volume = v

    def pause(self):
        self.paused = True
        self.calls.append("pause")

    def unpause(self):
        self.paused = False
        self.calls.append("unpause")

    def stop(self):
        self.calls.append("stop")


class FakeConfig:
    def __init__(self, bgm):
        self._bgm = bgm

    def get(self, *keys):
        data = {"bgm": self._bgm}
        for key in keys:
            data = data.get(key) if isinstance(data, dict) else None
        return data


def make(speech, song, **bgm):
    cfg = {"enable": True, "volume": 0.4, "duck_ratio": 0.5, "resume_delay_seconds": 1.0, **bgm}
    state = {"speech": speech, "song": song}
    player = BGMPlayer(FakeConfig(cfg), lambda: state["speech"], lambda: state["song"])
    player.stream = FakeStream()
    return player, state


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(bgm_player.time, "sleep", lambda s: None)


def test_speech_ducks_volume_without_pausing():
    player, state = make(speech=True, song=False)
    player._tick()
    assert player.stream.paused is False
    assert "pause" not in player.stream.calls
    assert player.stream.volume == pytest.approx(0.4 * 0.5)


def test_song_pauses_bgm_completely():
    player, state = make(speech=False, song=True)
    player._tick()
    assert player.stream.paused is True


def test_speech_during_song_still_paused_not_just_ducked():
    player, state = make(speech=True, song=True)
    player._tick()
    assert player.stream.paused is True


def test_volume_returns_after_speech_ends(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(bgm_player.time, "monotonic", lambda: clock["t"])
    player, state = make(speech=True, song=False)
    player._tick()
    assert player.stream.volume == pytest.approx(0.2)

    state["speech"] = False
    # 刚说完还在 resume_delay 内：保持压低，避免句与句之间音量一跳一跳
    clock["t"] += 0.5
    player._tick()
    assert player.stream.volume == pytest.approx(0.2)

    clock["t"] += 2.0
    player._tick()
    assert player.stream.volume == pytest.approx(0.4)


def test_duck_ratio_zero_falls_back_to_pause():
    player, state = make(speech=True, song=False, duck_ratio=0)
    player._tick()
    assert player.stream.paused is True


def test_resume_from_pause_while_speaking_starts_ducked(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(bgm_player.time, "monotonic", lambda: clock["t"])
    player, state = make(speech=False, song=True)
    player._tick()
    assert player.stream.paused is True

    # 歌放完了，但她正好在说话：恢复时直接从压低的音量继续，不能先顶到原音量
    state["song"] = False
    state["speech"] = True
    clock["t"] += 5
    player._tick()
    assert player.stream.paused is False
    assert player.stream.volume == pytest.approx(0.4 * 0.5)


def test_without_song_callback_behaves_like_before_for_speech():
    cfg = {"enable": True, "volume": 0.4, "duck_ratio": 0.5}
    player = BGMPlayer(FakeConfig(cfg), lambda: True)
    player.stream = FakeStream()
    player._tick()
    assert player.stream.paused is False
