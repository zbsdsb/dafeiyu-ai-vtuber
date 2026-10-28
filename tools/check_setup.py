"""开播前自检：逐项检查环境、素材、配置和服务，告诉你哪一步没做好、该怎么修。
用法（在项目根目录）：
    .venv\\Scripts\\python tools\\check_setup.py            离线检查（不联网、不花额度）
    .venv\\Scripts\\python tools\\check_setup.py --online   另外真实调用一次大模型和 Fish 配音，验证两个 Key
    可选 --config 路径（默认 config.json）
不会修改任何配置；Key 只显示"已填写（长度 N）"，不会打印出来。
结果：✅ 通过 / ⚠️ 提醒 / ❌ 必须修。全部没有 ❌ 时退出码为 0。
"""
import argparse, importlib, json, os, shutil, socket, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

bad = 0


def ok(msg, fix=""): print(f"✅ {msg}")
def warn(msg, fix=""): print(f"⚠️  {msg}" + (f"\n    → {fix}" if fix else ""))
def fail(msg, fix=""):
    global bad
    bad += 1
    print(f"❌ {msg}" + (f"\n    → {fix}" if fix else ""))


def masked(v): return f"已填写（长度 {len(v)}）" if v else "未填写"


def port_open(p):
    with socket.socket() as s:
        s.settimeout(.4)
        return s.connect_ex(("127.0.0.1", p)) == 0


def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(300).decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true", help="真实调用大模型和 Fish，验证 Key（会消耗极少额度）")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    a = ap.parse_args()

    print("== 1. 环境")
    v = sys.version_info
    if (3, 10) <= (v.major, v.minor) <= (3, 12): ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else: warn(f"Python {v.major}.{v.minor}，推荐 3.11（3.10~3.12 可用）", "winget install -e --id Python.Python.3.11")
    if ".venv" not in sys.executable.replace("\\", "/"):
        warn(f"当前不是项目虚拟环境里的 Python：{sys.executable}", "用 .venv\\Scripts\\python tools\\check_setup.py 运行")
    missing = []
    for mod in ("nicegui", "openai", "edge_tts", "pygame", "pydub", "websocket", "requests", "loguru"):
        try: importlib.import_module(mod)
        except Exception: missing.append(mod)
    if missing: fail("缺少依赖：" + ", ".join(missing), ".venv\\Scripts\\python -m pip install -r requirements\\core+platform.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple")
    else: ok("依赖已安装")
    if shutil.which("ffmpeg"): ok("FFmpeg 可用")
    else: warn("找不到 ffmpeg（点歌切歌、读歌曲时长要用）", "winget install -e --id Gyan.FFmpeg，装完重新打开命令行窗口")

    print("== 2. 配置文件")
    try:
        c = json.loads(Path(a.config).read_text("utf-8"))
    except Exception as e:
        fail(f"读不了 {a.config}：{e}", "config.json 格式坏了：用记事本改过的话检查引号和逗号，或从仓库重新下载这个文件再填 Key")
        return
    plat = c.get("platform")
    ok("弹幕平台：抖音") if plat == "dy" else warn(f"弹幕平台是 {plat}，不是抖音（dy）", "后台「直播接入」→ 平台 选「抖音」")
    chat = c.get("chat_type"); api = c.get("openai", {}).get("api", ""); key = (c.get("openai", {}).get("api_key") or [""])[0]
    model = c.get("chatgpt", {}).get("model", "")
    if chat != "chatgpt":
        warn(f"大语言模型类型是 {chat}，本检查只验证「ChatGPT / 通用模型」这一种")
    else:
        ok(f"大模型：{model} @ {api}")
        (ok if key else fail)(f"大模型 API 密钥：{masked(key)}", "" if key else "后台「人设与大脑」→ OpenAI / Ollama / 通用模型 → API密钥，粘贴智谱的 Key，然后保存配置")
    tts = c.get("audio_synthesis_type")
    fish_key = c.get("fish_audio", {}).get("api_key", "") or os.environ.get("FISH_API_KEY", "")
    if tts == "fish_audio":
        ok("语音合成：Fish Audio，音色 " + (c.get("fish_audio", {}).get("reference_id") or "（默认）"))
        (ok if fish_key else fail)(f"Fish Key：{masked(fish_key)}", "" if fish_key else "先关掉后台，用记事本打开 config.json，搜索 fish_audio，把 Key 填进 \"api_key\": \"\" 的引号里")
    else:
        ok(f"语音合成：{tts}")
    if c.get("read_comment", {}).get("enable"): warn("「念弹幕」是开着的：她会先把观众的弹幕念一遍再回答", "不想念的话：后台「弹幕与礼仪」里关掉念弹幕")

    print("== 3. 素材")
    name = c.get("live2d", {}).get("name", "")
    m3 = ROOT / "Live2D" / "live2d-model" / name / f"{name}.model3.json"
    if not c.get("live2d", {}).get("enable"): warn("Live2D 没有启用", "后台「声音与表现」→ Live2D → 启用")
    if m3.exists():
        n = sum(1 for _ in m3.parent.rglob("*") if _.is_file())
        ok(f"Live2D 模型：{name}（{n} 个文件）")
    else:
        fail(f"找不到模型文件 Live2D/live2d-model/{name}/{name}.model3.json", "按教程第三步，把 da-feiyu 文件夹里的全部文件复制到 Live2D/live2d-model/大肥鱼/")
    js = ROOT / "Live2D" / "js" / "model_name.js"
    if js.exists() and name and name not in js.read_text("utf-8", "replace"):
        warn(f"Live2D/js/model_name.js 里的模型名和配置（{name}）不一致", "两处要改成同一个模型名")
    exe = ROOT / "tools" / "DouyinBarrageGrab" / "WssBarrageServer.exe"
    ok("弹幕监听程序已就位") if exe.exists() else fail("找不到 tools/DouyinBarrageGrab/WssBarrageServer.exe", "按教程第四步下载 Release 包并解压到 tools/DouyinBarrageGrab/")
    songs = list((ROOT / "song").glob("*.mp3")) if (ROOT / "song").exists() else []
    ok(f"本地歌曲 {len(songs)} 首（没有也行，点歌会去网易云搜）")

    print("== 4. 服务（开播前全部启动后再看这一节）")
    svc = [(8888, "弹幕监听", "以管理员身份运行 tools\\DouyinBarrageGrab\\WssBarrageServer.exe"),
           (8081, "管理后台", "双击 启动WebUI管理后台.bat"),
           (8082, "主程序", "后台右下角「操作」→「一键运行」"),
           (12345, "Live2D 画面", "主程序启动后自动提供；没有的话检查 Live2D 是否启用"),
           (5500, "字幕服务", "双击 启动字幕和口型.bat")]
    for p, label, fix in svc:
        (ok if port_open(p) else warn)(f"{label}（端口 {p}）" + ("运行中" if port_open(p) else "没有运行"), "" if port_open(p) else fix)
    if port_open(8082):
        st, body = http_ok("http://127.0.0.1:8082/get_sys_info")
        ok("主程序接口正常") if st == 200 else warn(f"主程序接口没有响应：{body}")
    if port_open(12345):
        st, _ = http_ok("http://127.0.0.1:12345/Live2D/stage-portrait.html")
        ok("直播场景页可以打开：http://127.0.0.1:12345/Live2D/stage-portrait.html") if st == 200 else warn("直播场景页打不开")
    if port_open(5500):
        st, body = http_ok("http://127.0.0.1:5500/health")
        (ok if st == 200 and "caption" in body else warn)("字幕服务 /health " + ("正常" if st == 200 else body))

    if a.online:
        print("== 5. 联网验证 Key（各调用一次）")
        if chat == "chatgpt" and key:
            try:
                from openai import OpenAI
                t0 = time.time()
                extra = {"thinking": {"type": "disabled"}} if "bigmodel.cn" in api else None
                r = OpenAI(base_url=api, api_key=key, timeout=30).chat.completions.create(
                    model=model, max_tokens=60, extra_body=extra,
                    messages=[{"role": "user", "content": "用一句话跟直播间的观众打个招呼"}])
                txt = (r.choices[0].message.content or "").strip().replace("\n", " ")
                ok(f"大模型回复（{time.time() - t0:.1f}s）：{txt[:60]}")
            except Exception as e:
                msg = str(e)
                fail("大模型调用失败：" + msg[:160], "401：Key 错了或过期；404：API 地址或模型名不对；超时：检查网络或代理")
        if tts == "fish_audio" and fish_key:
            try:
                fa = c.get("fish_audio", {})
                body = {"text": "你好，我是大肥鱼，测试一下声音。", "format": "mp3"}
                if fa.get("reference_id"): body["reference_id"] = fa["reference_id"]
                req = urllib.request.Request("https://api.fish.audio/v1/tts", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    method="POST", headers={"Authorization": f"Bearer {fish_key}", "Content-Type": "application/json; charset=utf-8",
                                            "model": fa.get("model", "s2.1-pro-free")})
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=60) as r:
                    audio = r.read()
                out = ROOT / "out" / "check_fish.mp3"; out.parent.mkdir(exist_ok=True); out.write_bytes(audio)
                ok(f"Fish 配音成功（{time.time() - t0:.1f}s，{len(audio) // 1024} KB）→ out/check_fish.mp3，双击可以试听")
            except urllib.error.HTTPError as e:
                fail(f"Fish 配音失败：HTTP {e.code} {e.read()[:120].decode('utf-8', 'replace')}", "401/403：Key 不对；402：额度用完；400：检查 reference_id")
            except Exception as e:
                fail(f"Fish 配音失败：{e}", "检查网络")

    print()
    print("结果：" + ("全部通过 🎉" if not bad else f"有 {bad} 项必须修（见上面的 ❌）"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
