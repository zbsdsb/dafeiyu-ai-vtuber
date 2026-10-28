"""点歌歌词：按本地歌曲文件去网易云匹配同一个版本，取带时间轴的歌词。

只在运行时取、放内存，不落盘、不写进仓库。

匹配靠「歌名 + 时长」：本地文件的时长和网易云候选时长差在容差内，才认为是同一个
版本（现场版、DJ 版、深情版时长都不同），否则宁可只显示歌名，也不拿错版本的时间轴
去对歌——对错了比没有更糟。
"""

import json
import os
import re

from utils.my_log import logger
from utils.netease_music import _http_get, search_netease_songs

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://music.163.com",
}

_LRC_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# 歌词开头常带「作词/作曲/…」署名行，不是唱的内容
_CREDIT = re.compile(
    r"^\s*(作词|作曲|编曲|制作人?|监制|混音|母带|录音|和声|配唱|演唱|词|曲|OP|SP|Lyrics?|Compose[rd]?|"
    r"Arrange[rd]?|Produce[rd]?|Mix(ed)?|Master(ed)?)\s*[:：]",
    re.I,
)


# 匹配成功的「歌名|时长 -> 网易云歌曲 id」记在这里。只记 id，不记歌词；搜索接口被限流时
# 歌词接口还是通的，有 id 就不用再搜，也能减少对网易云的请求。
_ID_FILE = os.path.join("out", "song_lyric_ids.json")


def _id_key(stem, file_seconds):
    return f"{stem}|{round(file_seconds)}"


def _load_ids():
    try:
        with open(_ID_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _remember_id(stem, file_seconds, song_id):
    try:
        ids = _load_ids()
        ids[_id_key(stem, file_seconds)] = song_id
        os.makedirs(os.path.dirname(_ID_FILE), exist_ok=True)
        tmp = _ID_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ids, f, ensure_ascii=False)
        os.replace(tmp, _ID_FILE)
    except OSError as e:
        logger.error(f"[歌词] 记录歌曲 id 失败: {e}")


def parse_lrc(text):
    """LRC 文本 -> [(秒, 文字)]，按时间排序；跳过空行、署名行、元信息行。"""
    lines = []
    for raw in (text or "").splitlines():
        tags = list(_LRC_TAG.finditer(raw))
        if not tags:
            continue
        content = raw[tags[-1].end():].strip()
        if not content or _CREDIT.match(content):
            continue
        for tag in tags:
            minutes, seconds, frac = tag.group(1), tag.group(2), tag.group(3) or "0"
            at = int(minutes) * 60 + int(seconds) + int(frac) / (10 ** len(frac))
            lines.append((at, content))
    lines.sort(key=lambda item: item[0])
    return lines


def fetch_lyric_lines(song_id):
    """按网易云歌曲 id 取歌词，失败/纯音乐返回空列表。"""
    try:
        body, _ = _http_get(
            f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1", _HEADERS, 10)
        data = json.loads(body.decode("utf-8"))
        return parse_lrc((data.get("lrc") or {}).get("lyric") or "")
    except Exception as e:
        logger.error(f"[歌词] 取歌词失败 id={song_id}: {e}")
        return []


def _base_title(stem):
    """去掉「(深情版)」「（live）」这类括号后缀，剩下的才是歌名。"""
    return re.sub(r"[\(（\[【].*?[\)）\]】]", "", stem).strip()


def match_lyrics(stem, file_seconds, tolerance=3.0):
    """用歌名 + 时长找同一版本的歌词。

    Args:
        stem: 文件名（不含扩展名）
        file_seconds: 本地文件时长（秒）
        tolerance: 允许的时长差（秒）

    Returns:
        list[(秒, 文字)]；没匹配上返回 []
    """
    known_id = _load_ids().get(_id_key(stem, file_seconds))
    if known_id:
        lines = fetch_lyric_lines(known_id)
        if lines:
            logger.info(f"[歌词] 《{stem}》用已记住的歌曲 id：{len(lines)} 句")
            return lines

    keywords = []
    for keyword in (stem, _base_title(stem)):
        if keyword and keyword not in keywords:
            keywords.append(keyword)

    base = _base_title(stem).lower()

    # 先把所有符合条件的候选收齐，再按时长差从小到大尝试：“取第一个在容差内的”会在两个
    # 时长接近的翻唱版之间选错，对错的时间轴比没有歌词更糟
    candidates = {}
    for keyword in keywords:
        # 热门歌的翻唱版很多，同版本可能排得靠后，取前 20 条
        for song in search_netease_songs(keyword, limit=20):
            duration = (song.get("duration") or 0) / 1000.0
            diff = abs(duration - file_seconds)
            if diff > tolerance:
                continue
            name = (song.get("name") or "").lower()
            if base and base not in name and name not in base:
                continue
            candidates.setdefault(song.get("id"), diff)

    for song_id, diff in sorted(candidates.items(), key=lambda item: item[1]):
        lines = fetch_lyric_lines(song_id)
        if lines:
            logger.info(f"[歌词] 《{stem}》匹配成功：{len(lines)} 句，时长差 {diff:.1f} 秒")
            _remember_id(stem, file_seconds, song_id)
            return lines
    logger.info(f"[歌词] 《{stem}》没有时长一致的歌词，只显示歌名")
    return []
