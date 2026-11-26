"""A private headed display for collection; the login UI remains a separate component."""
import os
import signal
import subprocess
import time
from pathlib import Path


def main():
    def stop(*args):raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    children=[]
    try:
        for path in [Path('/tmp/.X99-lock'),Path('/tmp/.X11-unix/X99')]:
            if path.exists() or path.is_socket():path.unlink()
        children.append(subprocess.Popen(['Xvfb',':99','-screen','0','1280x720x24','-nolisten','tcp','-ac']))
        for _ in range(100):
            if Path('/tmp/.X11-unix/X99').exists():break
            if children[0].poll() is not None:raise RuntimeError('采集显示启动失败')
            time.sleep(0.1)
        else:raise RuntimeError('采集显示启动超时')
        children.append(subprocess.Popen(['python','-m','app.plugin.infra.crawler.product_jobs.service']))
        while all(p.poll() is None for p in children):time.sleep(0.5)
        raise RuntimeError('商品采集服务已退出')
    finally:
        for p in reversed(children):
            if p.poll() is None:p.terminate()
        for p in reversed(children):
            try:p.wait(timeout=8)
            except subprocess.TimeoutExpired:p.kill();p.wait()


if __name__=='__main__':main()
