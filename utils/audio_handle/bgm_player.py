"""网易云 BGM：安静时填满空隙，她说话或点歌在播时自动让位。

链路是「按关键词从网易云搜一批候选 -> 挑一首没播过的 -> 下到本地缓存 -> 用独立
channel 播放」。实时拉流不可靠（要先解析直链再落盘），而实测搜索 0.2 秒、下载
0.2~0.3 秒，所以边播边预取下一首就足够，不会出现「歌与歌之间空 3 秒」。

为什么单独一条通道：mixer_normal 是弹幕回复和点歌共用的，BGM 挤进去就会互相
抢占、暂停也会把她的语音一起停掉。channel 2 只归 BGM，暂停/恢复不影响任何语音。
"""

import os
import random
import threading
import time

from utils.my_log import logger
from utils.audio_handle.playback_stream import PlaybackStream
from utils.audio_handle.ducking import clamp_ratio
from utils.netease_music import download_netease_candidate, is_valid_audio_file, search_netease_songs

# 没配置关键词时用这几组兜底；都偏「不抢人声」的器乐
DEFAULT_KEYWORDS = [
    "轻音乐 安静",
    "钢琴 纯音乐 舒缓",
    "lofi 纯音乐",
    "古筝 轻音乐",
    "大提琴 舒缓",
]

BGM_CHANNEL_INDEX = 2


class BGMPlayer:
    """空闲 BGM 播放器。

    Args:
        config: utils.config.Config
        is_voice_busy: 无参回调，返回 True 表示有人声正在播或被排队。
            人声来了只把 BGM 音量压低（bgm.duck_ratio），不暂停；设成 0 则回到暂停。
        is_song_busy: 无参回调，返回 True 表示点歌的歌正在放。
            两首音乐不能叠着响，这时 BGM 完全让开（暂停）。
    """

    def __init__(self, config, is_voice_busy, is_song_busy=None):
        self.config = config
        self.is_voice_busy = is_voice_busy
        self.is_song_busy = is_song_busy
        self._level = 1.0             # 相对 bgm.volume 的比例：压低时是 duck_ratio，正常是 1.0
        self._last_speech_at = -1e9   # 最近一次检测到人声的时刻
        self.stream = PlaybackStream(BGM_CHANNEL_INDEX)

        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        self._paused = False          # 是否正被语音压住
        self._last_busy_at = 0.0      # 最后一次检测到语音的时刻
        self._current = None          # 正在播的 "歌名 - 歌手"
        self._current_path = None
        self._next = None             # 预取好的 (path, display)
        self._prefetching = False
        self._recent = []             # 最近播过的展示名，避免连播同一首
        self._recent_files = []       # 缓存兜底时最近选过的文件
        self._fail_streak = 0

    # ---------- 生命周期 ----------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bgm-player")
        self._thread.start()
        logger.info("[BGM] 后台线程已启动")

    def stop(self):
        self._running = False
        self._stop_current()
        logger.info("[BGM] 已停止")

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"[BGM] 循环异常: {e}")
            time.sleep(0.3)

    # ---------- 配置 ----------

    def _settings(self):
        bgm = self.config.get("bgm") or {}
        keywords = [k for k in (bgm.get("keywords") or []) if str(k).strip()]
        return {
            "enable": bool(bgm.get("enable", False)),
            "volume": max(0.0, min(1.0, float(bgm.get("volume", 0.3) or 0))),
            "keywords": keywords or DEFAULT_KEYWORDS,
            "cache_dir": bgm.get("cache_dir") or "bgm_cache",
            "cache_max_files": int(bgm.get("cache_max_files") or 60),
            "min_track_seconds": float(bgm.get("min_track_seconds") or 45),
            "max_track_seconds": float(bgm.get("max_track_seconds") or 600),
            "resume_delay_seconds": float(bgm.get("resume_delay_seconds") or 1.0),
            # 0 是有意义的（人声来了直接暂停），所以不能写成 `or` 默认值
            "duck_ratio": clamp_ratio(bgm.get("duck_ratio"), 0.35),
            "candidate_tries": int(bgm.get("candidate_tries") or 4),
        }

    # ---------- 每 0.3 秒一次的判断 ----------

    def _tick(self):
        cfg = self._settings()

        if not cfg["enable"]:
            if self._paused or self.stream.get_busy():
                self._stop_current()
            return

        now = time.monotonic()
        song = bool(self.is_song_busy and self.is_song_busy())
        speech = bool(self.is_voice_busy())
        if speech:
            self._last_speech_at = now

        # 点歌的歌在放，或者配置成“人声来了直接暂停”：完全让开
        if song or (speech and cfg["duck_ratio"] <= 0):
            self._last_busy_at = now
            if not self._paused:
                self._duck(cfg)
            return

        if self._paused:
            if now - self._last_busy_at < cfg["resume_delay_seconds"]:
                return
            # 从暂停恢复时她可能正好在说话，那就直接从压低的音量继续
            self._unduck(cfg, cfg["duck_ratio"] if speech else 1.0)
            return

        # 人声来了只压低音量；说完后留 resume_delay 再抬回来，避免句与句之间音量一跳一跳
        low = speech or (now - self._last_speech_at < cfg["resume_delay_seconds"])
        target = cfg["duck_ratio"] if low else 1.0
        if self.stream.get_busy() and abs(self._level - target) > 0.01:
            logger.info(f"[BGM] {'压低音量（她在说话）：%d%%' % int(target * 100) if low else '恢复音量'}")
            if low:
                self._ramp_to(cfg, target, 4, 0.04)
            else:
                self._ramp_to(cfg, target, 8, 0.1)

        if not self.stream.get_busy():
            if self._current is not None:
                logger.info(f"[BGM] 播完：{self._current}")
                self._current = None
                self._current_path = None
            self._play_next(cfg)

    # ---------- 让位 / 恢复 ----------

    def _duck(self, cfg):
        """降一点音量再暂停，避免硬切出「咔」的一声。"""
        self._paused = True
        logger.info(f"[BGM] 让位（语音开始）：{self._current or '无曲目'}")
        try:
            if self.stream.get_busy():
                self.stream.set_volume(cfg["volume"] * 0.4)
                time.sleep(0.12)
                self.stream.pause()
            self.stream.set_volume(0.0)
        except Exception as e:
            logger.error(f"[BGM] 暂停失败: {e}")

    def _unduck(self, cfg, level=1.0):
        self._paused = False
        self._level = level
        logger.info(f"[BGM] 恢复：{self._current or '无曲目'}")
        try:
            self.stream.set_volume(0.0)
            self.stream.unpause()
            steps = 5
            for i in range(1, steps + 1):
                self.stream.set_volume(cfg["volume"] * level * i / steps)
                time.sleep(0.08)
        except Exception as e:
            logger.error(f"[BGM] 恢复失败: {e}")

    def _ramp_to(self, cfg, level, steps, delay):
        """把音量比例平滑调到 level（不暂停）。"""
        start = self._level
        try:
            for i in range(1, steps + 1):
                self._level = start + (level - start) * i / steps
                self.stream.set_volume(cfg["volume"] * self._level)
                time.sleep(delay)
            self._level = level
        except Exception as e:
            logger.error(f"[BGM] 调音量失败: {e}")

    def _stop_current(self):
        self._level = 1.0
        self._paused = False
        self._current = None
        self._current_path = None
        try:
            self.stream.stop()
            self.stream.set_volume(0.0)
        except Exception as e:
            logger.error(f"[BGM] 停止失败: {e}")

    # ---------- 选歌与播放 ----------

    def _play_next(self, cfg):
        item = None
        with self._lock:
            if self._next is not None:
                item = self._next
                self._next = None

        if item is None:
            item = self._resolve(cfg)

        if item is None:
            self._fail_streak += 1
            # 连续失败就慢点重试，别把日志刷满
            time.sleep(min(2 * self._fail_streak, 20))
            return

        path, display_name = item
        try:
            # 新的一首从当前的压低状态开始，不要在她说话时猛地突然响起来
            self.stream.set_volume(cfg["volume"] * self._level)
            self.stream.load(path)
            self.stream.play()
            self._current = display_name
            self._current_path = path
            self._remember(display_name)
            self._fail_streak = 0
            logger.info(f"[BGM] 播放：{display_name} ({path})")
        except Exception as e:
            logger.error(f"[BGM] 播放失败 {path}: {e}")
            self._current = None
            self._current_path = None

        self._kick_prefetch(cfg)
        self._prune_cache(cfg)

    def _resolve(self, cfg):
        """搜关键词 -> 过滤老歌/时长不合适的 -> 逐个候选试下载。"""
        keywords = list(cfg["keywords"])
        random.shuffle(keywords)

        for keyword in keywords:
            songs = search_netease_songs(keyword, limit=10)
            if not songs:
                continue
            random.shuffle(songs)

            tried = 0
            for song in songs:
                name = song.get("name") or ""
                artists = song.get("artists") or [{}]
                display_name = f"{name} - {artists[0].get('name', '未知歌手')}"
                if display_name in self._recent:
                    continue

                duration = (song.get("duration") or 0) / 1000.0
                if duration and not (cfg["min_track_seconds"] <= duration <= cfg["max_track_seconds"]):
                    continue

                tried += 1
                if tried > cfg["candidate_tries"]:
                    break

                path, got_display = download_netease_candidate(song, cfg["cache_dir"])
                if path:
                    return path, got_display

        # 搜索被限流/断网/候选都不可用时，改播本地缓存，直播间不能因为网易云一时不可用就没声音
        cached = self._from_cache(cfg)
        if cached:
            logger.warning(f"[BGM] 在线搜索没有可用候选，改播本地缓存：{cached[1]}")
            return cached
        logger.warning(f"[BGM] 本轮没有可用候选且缓存为空（关键词={cfg['keywords']}）")
        return None

    def _from_cache(self, cfg):
        """从 bgm_cache 里随机挑一首：不是正在播的、尽量不是最近刚选过的。"""
        cache_dir = cfg["cache_dir"]
        try:
            files = [os.path.join(cache_dir, n) for n in os.listdir(cache_dir) if n.lower().endswith(".mp3")]
        except OSError:
            return None
        current = os.path.abspath(self._current_path) if self._current_path else None
        files = [f for f in files if os.path.abspath(f) != current and is_valid_audio_file(f)]
        if not files:
            return None
        fresh = [f for f in files if f not in self._recent_files] or files
        path = random.choice(fresh)
        self._recent_files.append(path)
        del self._recent_files[: max(0, len(self._recent_files) - min(10, len(files) - 1))]
        name = os.path.splitext(os.path.basename(path))[0]
        return os.path.abspath(path), name

    def _kick_prefetch(self, cfg):
        with self._lock:
            if self._prefetching or self._next is not None:
                return
            self._prefetching = True

        def worker():
            try:
                item = self._resolve(cfg)
                if item:
                    with self._lock:
                        if self._next is None:
                            self._next = item
            except Exception as e:
                logger.error(f"[BGM] 预取异常: {e}")
            finally:
                with self._lock:
                    self._prefetching = False

        threading.Thread(target=worker, daemon=True, name="bgm-prefetch").start()

    # ---------- 记录与缓存 ----------

    def _remember(self, display_name):
        self._recent.append(display_name)
        if len(self._recent) > 30:
            del self._recent[: len(self._recent) - 30]

    def _prune_cache(self, cfg):
        """缓存只留最近 N 首，按修改时间删最旧的。"""
        try:
            cache_dir = cfg["cache_dir"]
            if not os.path.isdir(cache_dir):
                return
            files = [
                os.path.join(cache_dir, name)
                for name in os.listdir(cache_dir)
                if name.lower().endswith((".mp3", ".flac", ".m4a", ".wav", ".ogg"))
            ]
            limit = max(5, cfg["cache_max_files"])
            if len(files) <= limit:
                return
            files.sort(key=lambda p: os.path.getmtime(p))
            for path in files[: len(files) - limit]:
                if self._current_path and os.path.abspath(path) == os.path.abspath(self._current_path):
                    continue
                os.remove(path)
                logger.info(f"[BGM] 清理缓存: {path}")
        except Exception as e:
            logger.error(f"[BGM] 清理缓存失败: {e}")

    # ---------- 查询（给 WebUI / 调试用） ----------

    def status(self):
        return {
            "playing": bool(self._current),
            "paused": self._paused,
            "current": self._current,
            "next_ready": self._next[1] if self._next else None,
        }
