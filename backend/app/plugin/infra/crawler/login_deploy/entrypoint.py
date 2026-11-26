"""统一管理专用登录容器的子进程，任何一个退出则关闭整个登录环境。"""

import os
import signal
import subprocess
import time

children = []


def stop(*_):
    raise SystemExit(0)


def start(args):
    process = subprocess.Popen(args)
    children.append(process)
    return process


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    start(["Xvfb", ":99", "-screen", "0", "1280x720x24", "-nolisten", "tcp", "-ac"])
    deadline = time.monotonic() + 10
    while not os.path.exists("/tmp/.X11-unix/X99"):
        if time.monotonic() > deadline or children[0].poll() is not None:
            raise RuntimeError("虚拟显示启动失败")
        time.sleep(0.1)
    start(["x11vnc", "-display", ":99", "-localhost", "-rfbport", "5900", "-forever", "-shared", "-nopw", "-quiet"])
    start(["websockify", "--web=/usr/share/novnc", "127.0.0.1:6080", "127.0.0.1:5900"])
    start(["python", "-m", "app.plugin.infra.crawler.login_service"])
    start(["nginx", "-c", "/opt/login/nginx.conf", "-g", "daemon off;"])
    while all(child.poll() is None for child in children):
        time.sleep(1)
    raise RuntimeError("登录环境子进程退出")
finally:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for child in reversed(children):
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
