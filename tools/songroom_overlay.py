"""只读现有直播日志，生成最小展示状态；不发送评论、不调用 LLM/TTS、不改代理。"""
from pathlib import Path
import argparse
import ast
from datetime import datetime
import json
import os
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"out/songroom"

class OverlayState:
    def __init__(self):
        self.reply={"text":"","at":0}
        self.song={"title":"","requester":"","at":0}
        self.last_barrage_at=0
        self.core_ready=False

    def feed(self,line):
        if len(line)>16384:
            return
        try:
            stamp=datetime.fromisoformat(line[:23]).timestamp()
        except ValueError:
            return
        if "utils.platforms.dy:on_message" in line and "直播间弹幕消息" in line:
            self.last_barrage_at=max(self.last_barrage_at,stamp)
        marker="WEBUI API callback接口收到数据："
        if marker not in line:
            return
        try:
            value=ast.literal_eval(line.split(marker,1)[1])
            data=value.get("data",{})
            if value.get("type")!="llm" or data.get("content_type")!="answer":
                return
            text=str(data.get("content",""))
            if data.get("type")=="点歌":
                if text.startswith("网易云音乐: "):
                    title=text.removeprefix("网易云音乐: ")
                elif Path(text).suffix.lower() in (".mp3",".wav",".flac",".ogg",".m4a"):
                    title=Path(text).stem
                else:
                    return
                if stamp>=self.song["at"]:
                    self.song={"title":title[:70],"requester":str(data.get("username",""))[:20],"at":stamp}
            else:
                if stamp>=self.reply["at"]:
                    self.reply={"text":text[:240],"at":stamp}
        except (ValueError,SyntaxError,TypeError,AttributeError,RecursionError):
            return

    def snapshot(self):
        return {"schema":1,"updated_at":time.time(),"core_ready":self.core_ready,
                "last_barrage_at":self.last_barrage_at,"reply":self.reply,"song":self.song}

def atomic_write(data):
    OUT.mkdir(parents=True,exist_ok=True)
    temp=OUT/f"state.{os.getpid()}.tmp"
    temp.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    for attempt in range(3):
        try:
            os.replace(temp,OUT/"state.json")
            return
        except PermissionError:
            if attempt==2:raise
            time.sleep(.05)

def run(once=False):
    OUT.mkdir(parents=True,exist_ok=True)
    lock=(OUT/"collector.lock").open("a+b")
    if not once:
        # 单实例锁；重复运行启动器不会产生第二个采集进程。
        if os.name=="nt":
            import msvcrt
            lock.seek(0)
            if not lock.read(1):lock.write(b"0");lock.flush()
            lock.seek(0)
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                print("COLLECTOR_ALREADY_RUNNING")
                return
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        (OUT/"collector.pid").write_text(str(os.getpid()),encoding="ascii")
    state=OverlayState()
    offsets={}
    health_at=0
    print("SONGROOM_COLLECTOR_STARTED",flush=True)
    while True:
        # 兼容跨午夜及核心仍写前一日文件的情况，只跟踪最近更新的少量日志。
        logs=sorted((ROOT/"log").glob("log-*.txt"),key=lambda p:p.stat().st_mtime,reverse=True)[:2]
        for path in sorted(logs,key=lambda p:p.stat().st_mtime):
            size=path.stat().st_size
            offset=offsets.get(path,max(0,size-524288))
            if size<offset:offset=0
            with path.open("rb") as f:
                f.seek(offset)
                raw=f.read(1048576)
                complete=raw.rfind(b"\n")
                if complete>=0:
                    offsets[path]=offset+complete+1
                    for line in raw[:complete+1].decode("utf-8",errors="replace").splitlines():state.feed(line)
        if time.monotonic()-health_at>3:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8082/get_sys_info",timeout=1) as response:
                    state.core_ready=json.load(response).get("code")==200
            except (OSError,ValueError):state.core_ready=False
            health_at=time.monotonic()
        atomic_write(state.snapshot())
        if once:
            print(json.dumps(state.snapshot(),ensure_ascii=False))
            return
        time.sleep(.6)

def self_test():
    state=OverlayState()
    stamp="2026-10-27 23:30:00.000"
    state.feed(stamp+" | INFO | utils.platforms.dy:on_message:33 - [直播间弹幕消息] [测试观众]：你好")
    def callback(data):
        state.feed(stamp+" | INFO | webui_app.api:callback:170 - WEBUI API callback接口收到数据："+repr({"type":"llm","data":data}))
    callback({"content_type":"question","type":"弹幕信息","content":"不得作为 AI 字幕"})
    assert state.reply["text"]==""
    callback({"content_type":"answer","type":"chatgpt","content":"只显示 AI 回答 <img src=x onerror=alert(1)>"})
    assert state.reply["text"].startswith("只显示 AI 回答") and state.last_barrage_at>0
    callback({"content_type":"answer","type":"点歌","content":"song/测试曲目.mp3","username":"测试观众"})
    assert state.song["title"]=="测试曲目"
    state.feed(stamp+" | WEBUI API callback接口收到数据：__import__('os').system('echo BAD')")
    assert state.song["title"]=="测试曲目"
    print("SELF_TEST_PASS: real-barrage marker, answer-only caption, song metadata, invalid input rejected")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    self_test() if args.self_test else run(args.once)
