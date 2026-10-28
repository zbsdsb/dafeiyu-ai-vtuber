"""礼物的轻量互动；不把播放原唱误当成角色唱歌。"""
from collections import OrderedDict
import hashlib
import re
import threading
import time


class GiftBlessings:
    def __init__(self):
        self._last = OrderedDict()
        self._lock = threading.Lock()

    def resolve(self, data, settings, now=None):
        rule = (settings or {}).get("small_heart_blessing", {})
        name = re.sub(r"\s*[xX×]\s*\d+\s*$", "", str(data.get("gift_name", ""))).strip()
        if not rule.get("enable", False) or name != "小心心":
            return False, None
        username = str(data.get("username") or "朋友")[:20]
        now = time.monotonic() if now is None else now
        cooldown = max(0, min(300, float(rule.get("cooldown_seconds", 30))))
        with self._lock:
            previous = self._last.get(username)
            if previous is not None and now - previous < cooldown:
                return True, None
            self._last[username] = now
            self._last.move_to_end(username)
            while len(self._last) > 512:
                self._last.popitem(last=False)
        wishes = (
            "愿你今天的烦恼都被海风吹散，接下来的日子平安顺心。",
            "愿你忙的时候有收获，累的时候有依靠，每一天都有小小的开心。",
            "愿你所期待的好事慢慢靠近，也记得好好吃饭、照顾自己。",
            "愿你一路遇见温柔，心里有光，生活里有值得期待的小惊喜。",
        )
        index = hashlib.sha256((username + str(int(now // max(1, cooldown)))).encode()).digest()[0] % len(wishes)
        return True, f"谢谢{username}的小心心。{username}，{wishes[index]}"
