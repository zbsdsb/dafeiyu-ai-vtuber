"""Live2D 静态资源服务器。"""
import http.server
import socketserver


class ThreadedStaticServer(socketserver.ThreadingTCPServer):
    """浏览器会并行请求动作、纹理和模型资源，必须并发处理连接。"""

    allow_reuse_address = True
    daemon_threads = True
    # 模型启动时会并行请求数十份动作、表情和纹理文件。
    request_queue_size = 128


def create_live2d_server(port):
    return ThreadedStaticServer(("", port), http.server.SimpleHTTPRequestHandler)
