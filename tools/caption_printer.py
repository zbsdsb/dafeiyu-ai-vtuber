"""兼容现有 /send_message 的本机字幕服务；仅绑定回环地址，不播放音频。

除字幕外还承担两类舞台信号（都是只读轮询，前端按 seq 判断是否是新事件）：
- /thinking?on=1|0   角色是否处于「思考中」，超过 THINKING_TTL 秒自动失效，
                      避免核心异常退出后气泡一直挂着
- /effect?name=clap  一次性特效触发，seq 单调递增
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out/songroom'
LOCK = threading.Lock()
STATE = {'id': 0, 'text': '', 'at': 0}

# 思考状态：核心开始跑 LLM 时置位，出结果或超时后失效
THINKING_TTL = 25.0
THINKING = {'on': False, 'at': 0.0}

# 特效：每次触发 seq+1，前端记住上次看过的 seq
EFFECT = {'name': '', 'seq': 0, 'at': 0.0}


def emit(event):
    """运行日志。日志写不进去（编码、文件被占用……）不能让请求处理线程崩掉。

    stdout 被重定向成 GBK 文件时，“♪”、emoji、“ʚ”这类字符会抛 UnicodeEncodeError；
    以前这条异常发生在状态已更新、响应还没发出去之间，客户端看到的是“连接被断开”。
    """
    try:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        origin = self.headers.get('Origin', '')
        if origin and urlparse(origin).hostname not in ('127.0.0.1', 'localhost'):
            self.send_error(403)
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        now = time.time()

        if parsed.path == '/send_message':
            text = query.get('content', [''])[0][:1000]
            with LOCK:
                STATE.update(id=STATE['id'] + 1, text=text, at=now)
                value = dict(STATE)
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / 'caption-last.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            data = {'code': 200, 'message': '字幕已接收', 'id': value['id']}
            emit({'event': 'CAPTION_RECEIVED', **value})

        elif parsed.path == '/thinking':
            on = query.get('on', ['0'])[0] in ('1', 'true', 'True')
            with LOCK:
                THINKING['on'] = on
                THINKING['at'] = now
            data = {'code': 200, 'thinking': on}
            emit({'event': 'THINKING', 'on': on})

        elif parsed.path == '/effect':
            name = query.get('name', ['clap'])[0][:32] or 'clap'
            with LOCK:
                EFFECT.update(name=name, seq=EFFECT['seq'] + 1, at=now)
                value = dict(EFFECT)
            data = {'code': 200, **value}
            emit({'event': 'EFFECT', **value})

        elif parsed.path == '/state':
            with LOCK:
                data = dict(STATE)
                thinking_on = THINKING['on'] and (now - THINKING['at'] < THINKING_TTL)
                data['thinking'] = thinking_on
                data['thinking_age'] = round(now - THINKING['at'], 2) if THINKING['at'] else None
                data.update(effect=EFFECT['name'], effect_seq=EFFECT['seq'], effect_at=EFFECT['at'])

        elif parsed.path == '/health':
            data = {'code': 200, 'service': 'local-caption-printer'}

        else:
            self.send_error(404)
            return

        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Vary', 'Origin')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    # 日志统一用 UTF-8，不可编码的字符用替代符，不再依赖系统默认的 GBK
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 5500), Handler)
    print('CAPTION_PRINTER_READY http://127.0.0.1:5500', flush=True)
    server.serve_forever()
