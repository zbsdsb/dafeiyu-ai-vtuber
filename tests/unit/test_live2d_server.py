"""Live2D 资源并发加载回归测试。"""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
import socketserver
import threading
from urllib.request import urlopen

from utils.live2d_server import ThreadedStaticServer


def test_static_server_serves_parallel_asset_requests(tmp_path):
    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"live2d-test-asset")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    with ThreadedStaticServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            urls = [f"http://127.0.0.1:{server.server_address[1]}/asset.bin?request={i}" for i in range(24)]
            with ThreadPoolExecutor(max_workers=24) as pool:
                responses = list(pool.map(lambda url: urlopen(url, timeout=5).read(), urls))
            assert responses == [b"live2d-test-asset"] * 24
        finally:
            server.shutdown()
            thread.join(timeout=5)
