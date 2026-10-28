"""把 AI 核心的真实播放会话音量提供给 Live2D，不读取麦克风。"""
from pathlib import Path
import argparse
import json
import os
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils.audio_handle.core_audio_meter import CoreSessionMeter

def main(probe=False):
    meter=CoreSessionMeter()
    if probe:
        print(json.dumps(meter.read(),ensure_ascii=False))
        return
    out=ROOT/"out/songroom"
    out.mkdir(parents=True,exist_ok=True)
    lock=(out/"lipsync.lock").open("a+b")
    import msvcrt
    lock.seek(0)
    if not lock.read(1):lock.write(b"0");lock.flush()
    lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        print("LIPSYNC_ALREADY_RUNNING",flush=True)
        return
    (out/"lipsync.pid").write_text(str(os.getpid()),encoding="ascii")
    print("LIPSYNC_SESSION_METER_STARTED",flush=True)
    previous=None
    wrote_at=0
    max_peak=0.0
    while True:
        state=meter.read()
        max_peak=max(max_peak,state["peak"])
        state["max_peak_seen"]=max_peak
        signature=(state["source_pid"],state["session_count"],round(state["peak"],2))
        # 有声音时约 15 Hz 发布；安静时只写心跳，避免无意义的高频磁盘 I/O。
        if signature!=previous or time.monotonic()-wrote_at>.7:
            temp=out/f"mouth.{os.getpid()}.tmp"
            temp.write_text(json.dumps(state),encoding="utf-8")
            try:
                os.replace(temp,out/"mouth.json")
                previous=signature
                wrote_at=time.monotonic()
            except PermissionError:
                pass
        time.sleep(.065)

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--probe",action="store_true")
    main(p.parse_args().probe)
