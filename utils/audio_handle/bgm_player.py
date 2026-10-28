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
        is_voice_busy: 无参回调，返回 True 表示有语音正在播或被排队
    """

    def __init__(self, config, is_voice_busy):
        self.config = config
        self.is_voice_busy = is_voice_busy

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
            "candidate_tries": int(bgm.get("candidate_tries") or 4),
        }

    # ---------- 每 0.3 秒一次的判断 ----------

    def _tick(self):
        cfg = self._settings()

        if not cfg["enable"]:
            if self._paused or self.stream.get_busy():
                self._stop_current()
            return

        if self.is_voice_busy():
            self._last_busy_at = time.monotonic()
            if not self._paused:
                self._duck(cfg)
            return

        if self._paused:
            if time.monotonic() - self._last_busy_at < cfg["resume_delay_seconds"]:
                return
            self._unduck(cfg)
            return

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

    def _unduck(self, cfg):
        self._paused = False
        logger.info(f"[BGM] 恢复：{self._current or '无曲目'}")
        try:
            self.stream.set_volume(0.0)
            self.stream.unpause()
            steps = 5
            for i in range(1, steps + 1):
                self.stream.set_volume(cfg["volume"] * i / steps)
                time.sleep(0.08)
        except Exception as e:
            logger.error(f"[BGM] 恢复失败: {e}")

    def _stop_current(self):
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
            self.stream.set_volume(cfg["volume"])
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
