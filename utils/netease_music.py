"""网易云音乐点歌搜索与自动下载模块。
提供免登录公开接口搜索歌曲、解析真实播放直链并自动下载到本地曲库。
"""
import os
import re
import json
import time
import urllib.request
import urllib.parse
from utils.my_log import logger

# 搜索接口会对频繁请求返回 405“操作频繁”。被限流后继续敌接口只会让限流持续更久，
# 所以在冷却期内直接不发搜索（BGM 这时候改播本地缓存）。
SEARCH_COOLDOWN_SECONDS = 120
_search_blocked_until = 0.0


def sanitize_filename(name: str) -> str:
    """清理 Windows 文件名非法字符"""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


# 一首歌不可能低于这个字节数；网易云版权下架时 outer/url 会 302 到 music.163.com/404，
# 那个 HTML 页面恰好 100KB 出头，所以只按大小判会把错误页当成歌存下来。
MIN_AUDIO_BYTES = 100_000


def _head_is_audio(head: bytes) -> bool:
    """按文件头判断是不是音频。拿不准的一律当不是。"""
    if not head:
        return False
    if head.startswith(b'ID3'):
        return True
    # MPEG 帧同步：0xFF 后高 3 位全为 1
    if head[0:1] == b'\xff' and len(head) > 1 and (head[1] & 0xE0) == 0xE0:
        return True
    if head[:4] in (b'fLaC', b'OggS', b'RIFF'):
        return True
    if len(head) >= 8 and head[4:8] == b'ftyp':
        return True
    return False


def _looks_like_html(head: bytes) -> bool:
    return head[:1] in (b'<', b'{', b'[') or head.lower().startswith(b'<!doctype')


def is_valid_audio_file(path: str) -> bool:
    """缓存复用前必须验真：光看文件大小会把以前的坏文件一直捡回来。"""
    try:
        if os.path.getsize(path) < MIN_AUDIO_BYTES:
            return False
        with open(path, 'rb') as handle:
            return _head_is_audio(handle.read(16))
    except OSError:
        return False


def _http_get(url: str, headers: dict, timeout: int = 15) -> tuple[bytes, str]:
    """读回 (内容, Content-Type)。调用方自己判是不是音频。"""
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read(), (resp.headers.get("Content-Type") or "").lower()


# 网易云登录 cookie（凭据，已在 .gitignore）。只需要里面有 MUSIC_U；贴整份 Netscape cookie 文件或
# “MUSIC_U=xxx” 一行都行。没有这个文件就只用匿名下载，行为和以前一样。
COOKIE_FILE = os.path.join("cookie", "netease.txt")


def _audio_ok(content: bytes, content_type: str) -> bool:
    """是不是真音频：版权下架时返回的是 html 错误页，大小恰好也很像一首歌。"""
    head = content[:16]
    if "text/html" in content_type or _looks_like_html(head) or not _head_is_audio(head):
        return False
    return len(content) >= MIN_AUDIO_BYTES


def _read_music_u():
    try:
        with open(COOKIE_FILE, encoding="utf-8") as f:
            match = re.search(r"MUSIC_U[=\t ]+([^\s;]+)", f.read())
    except OSError:
        return None
    return match.group(1) if match else None


def _fetch_audio_bytes_logged_in(song_id, headers: dict, timeout: int = 30) -> bytes | None:
    """用登录账号取匿名拿不到的歌（VIP 专享等）。没有 cookie、没地址、只有试听片段都返回 None。"""
    music_u = _read_music_u()
    if not music_u:
        return None
    try:
        auth_headers = {**headers, "Cookie": f"MUSIC_U={music_u}; os=pc; appver=2.9.7"}
        body, _ = _http_get(
            f"https://music.163.com/api/song/enhance/player/url?ids=[{song_id}]&br=320000", auth_headers, 15)
        item = (json.loads(body.decode("utf-8")).get("data") or [{}])[0]
        url = item.get("url")
        if not url or item.get("freeTrialInfo"):
            return None
        # CDN 地址自带签名，不需要也不该把 cookie 发过去
        content, content_type = _http_get(url, {"User-Agent": headers.get("User-Agent", "Mozilla/5.0")}, timeout)
        if not _audio_ok(content, content_type):
            return None
        logger.info(f"[网易云] 匿名链接拿不到 id={song_id}，已用登录账号下载")
        return content
    except Exception as e:
        logger.warning(f"[网易云] 登录下载 id={song_id} 失败: {e}")
        return None


def _fetch_audio_bytes(song_id, headers: dict, timeout: int = 30) -> bytes | None:
    """取一首歌的音频字节；拿不到真音频一律返回 None。

    先匿名下载（免费歌不用动账号），匿名拿不到再用登录账号，把对账号的请求量降到最低。
    """
    try:
        content, content_type = _http_get(
            f"http://music.163.com/song/media/outer/url?id={song_id}.mp3", headers, timeout)
        if _audio_ok(content, content_type):
            return content
    except Exception:
        pass
    return _fetch_audio_bytes_logged_in(song_id, headers, timeout)


def search_netease_songs(keyword: str, limit: int = 10) -> list[dict]:
    """搜索候选歌曲，只返回元信息，不下载。"""
    global _search_blocked_until
    if time.monotonic() < _search_blocked_until:
        return []
    try:
        encoded_keyword = urllib.parse.quote(keyword.strip())
        search_url = ("https://music.163.com/api/search/get/web?csrf_token=Free&type=1"
                      f"&offset=0&total=true&limit={int(limit)}&s={encoded_keyword}")
        content, _ = _http_get(search_url, {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://music.163.com",
        }, timeout=12)
        data = json.loads(content.decode("utf-8"))
        if data.get("code") not in (None, 200):
            # 例如 405“操作频繁”：直接返回空列表会被当成“没搜到”，排查时分不清
            logger.warning(f"[网易云搜索] {keyword} 被拒绝：code={data.get('code')} {data.get('msg') or ''}")
            if data.get("code") == 405:
                _search_blocked_until = time.monotonic() + SEARCH_COOLDOWN_SECONDS
                logger.warning(f"[网易云搜索] 被限流，{SEARCH_COOLDOWN_SECONDS} 秒内不再发搜索请求")
            return []
        return data.get("result", {}).get("songs", []) or []
    except Exception as e:
        logger.error(f"[网易云搜索] {keyword} 异常: {e}")
        return []


def _song_ids(song: dict, fallback: str = "未命名") -> tuple[object, str, str, str]:
    """从搜索条目里取 (id, 文件名, 歌手, 展示名)。"""
    song_id = song.get("id")
    song_name = sanitize_filename(song.get("name", fallback))
    artist_name = "未知歌手"
    if song.get("artists"):
        artist_name = sanitize_filename(song["artists"][0].get("name", "未知歌手"))
    return song_id, song_name, artist_name, f"{song_name} - {artist_name}"


def download_netease_candidate(song: dict, save_dir: str) -> tuple[str | None, str | None]:
    """下载指定候选（不搜关键词），文件名前缀 id 避免同名覆盖。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://music.163.com",
    }
    try:
        song_id, song_name, _artist, display_name = _song_ids(song)
        target_path = os.path.abspath(os.path.join(save_dir, f"{song_id}_{song_name}.mp3"))
        if os.path.exists(target_path) and is_valid_audio_file(target_path):
            return target_path, display_name

        content = _fetch_audio_bytes(song_id, headers, timeout=30)
        if content is None:
            logger.warning(f"[网易云建库] 候选《{display_name}》无可用音频流，跳过")
            return None, None

        os.makedirs(save_dir, exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(content)
        logger.info(f"[网易云建库] 下载《{display_name}》-> {target_path} ({len(content)} 字节)")
        return target_path, display_name
    except Exception as e:
        logger.error(f"[网易云建库] 下载 {song.get('name')} 异常: {e}")
        return None, None


def download_netease_song(keyword: str, save_dir: str) -> tuple[str | None, str | None]:
    """按关键词下载**第一个能拿到可用音频流**的候选，且不复用同名旧文件。

    点歌用的 search_and_download_netease_music 会复用本地缓存、且歌名撞车时可能
    命中别人点过的另一首；批量建曲库要的是「尽量都下到」，所以这里逐候选换。
    """
    try:
        os.makedirs(save_dir, exist_ok=True)
        for s in search_netease_songs(keyword, limit=5):
            path, display_name = download_netease_candidate(s, save_dir)
            if path:
                return path, display_name
        return None, None
    except Exception as e:
        logger.error(f"[网易云建库] {keyword} 异常: {e}")
        return None, None


def search_and_download_netease_music(keyword: str, save_dir: str = "song") -> tuple[str | None, str | None]:
    """根据关键词从网易云音乐搜索并下载歌曲。

    Args:
        keyword: 歌名或歌名+歌手名，例如 "晴天" 或 "周杰伦 晴天"
        save_dir: 歌曲保存目录，默认为项目根目录下的 song

    Returns:
        (saved_file_path, display_info)
        如果成功返回 (绝对文件路径, "歌名 - 歌手")；如果失败返回 (None, None)
    """
    try:
        os.makedirs(save_dir, exist_ok=True)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://music.163.com"
        }

        # 走统一的搜索函数：限流/冷却期会在日志里写明原因，不再被当成“没搜到”
        songs = search_netease_songs(keyword, limit=5)
        if not songs:
            logger.warning(f"[网易云点歌] 未搜索到关键词: {keyword}（若上一条是搜索被拒绝/限流，则是网易云接口问题而不是歌不存在）")
            return None, None

        for s in songs:
            song_id = s.get("id")
            song_name = sanitize_filename(s.get("name", keyword))
            artist_name = "未知歌手"
            if s.get("artists") and len(s["artists"]) > 0:
                artist_name = sanitize_filename(s["artists"][0].get("name", "未知歌手"))

            display_name = f"{song_name} - {artist_name}"
            target_filename = f"{song_name}.mp3"
            target_path = os.path.abspath(os.path.join(save_dir, target_filename))

            # 如果本地已有该文件且确实是有效音频，直接复用
            if os.path.exists(target_path) and is_valid_audio_file(target_path):
                logger.info(f"[网易云点歌] 本地曲库命中缓存: {target_path}")
                return target_path, display_name
            if os.path.exists(target_path):
                # 旧版本可能把 404 页面当成歌存下来了，删掉免得一直被当成缓存
                logger.warning(f"[网易云点歌] 本地文件不是有效音频，删除后重下: {target_path}")
                try:
                    os.remove(target_path)
                except OSError:
                    pass

            # 先走网易云免登录外链，VIP 等匿名拿不到的再用登录账号（见 _fetch_audio_bytes）
            try:
                content = _fetch_audio_bytes(song_id, headers, timeout=15)
                if content is None:
                    logger.warning(f"[网易云点歌] 候选《{display_name}》拿不到音频流，跳过")
                    continue

                with open(target_path, "wb") as f:
                    f.write(content)
                logger.info(f"[网易云点歌] 成功下载歌曲 《{display_name}》 -> {target_path} ({len(content)} 字节)")
                return target_path, display_name
            except Exception as e:
                logger.debug(f"[网易云点歌] 候选 {display_name} (id={song_id}) 解析跳过: {e}")
                continue

        logger.warning(f"[网易云点歌] 关键词 《{keyword}》 的候选歌曲均无可用音频流 (可能受版权或VIP限制)")
        return None, None
    except Exception as e:
        logger.error(f"[网易云点歌] 搜索下载异常: {e}")
        return None, None
