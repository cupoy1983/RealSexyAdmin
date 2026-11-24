"""通过本机 Edge 的调试协议运行独立、无界面的采集会话。"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def find_edge() -> str:
    executable = shutil.which("msedge") or shutil.which("microsoft-edge")
    if executable:
        return executable
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / "Microsoft/Edge/Application/msedge.exe"
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError("未找到 Edge，请通过 --browser 指定已安装的浏览器程序")


class EdgeBrowser:
    def __init__(self, executable: str | None = None, timeout: float = 40, navigation_gate=None) -> None:
        self.executable = executable
        self.timeout = timeout
        self.process = None
        self.socket = None
        self.profile = None
        self.sequence = 0
        self.navigation_info = {}
        self.navigation_gate = navigation_gate

    async def __aenter__(self):
        self.profile = tempfile.TemporaryDirectory(prefix="crawler-edge-")
        try:
            self.process = subprocess.Popen(
                [
                    self.executable or find_edge(),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--lang=en-US",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-debugging-port=0",
                    f"--user-data-dir={self.profile.name}",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            active_port = Path(self.profile.name) / "DevToolsActivePort"
            async with asyncio.timeout(self.timeout):
                while not active_port.exists():
                    if self.process.poll() is not None:
                        raise RuntimeError("后台浏览器启动失败")
                    await asyncio.sleep(0.1)
                port = int(active_port.read_text().splitlines()[0])
                async with httpx.AsyncClient(trust_env=False) as client:
                    response = await client.get(f"http://127.0.0.1:{port}/json/list")
                    response.raise_for_status()
                page = next(item for item in response.json() if item["type"] == "page")
                self.socket = await connect(
                    page["webSocketDebuggerUrl"],
                    proxy=None,
                    max_size=8_000_000,
                )
            await self.command("Page.enable")
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *args):
        await self.close()

    async def command(self, method: str, params: dict | None = None) -> dict:
        try:
            return await self._exchange(method, params)
        except ConnectionClosed as error:
            raise RuntimeError("后台浏览器连接中断") from error

    async def _exchange(self, method: str, params: dict | None = None) -> dict:
        self.sequence += 1
        request_id = self.sequence
        await self.socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        async with asyncio.timeout(self.timeout):
            while True:
                response = json.loads(await self.socket.recv())
                if response.get("id") == request_id:
                    if "error" in response:
                        raise RuntimeError(f"浏览器协议执行失败：{method}")
                    return response.get("result", {})

    async def evaluate(self, expression: str):
        response = await self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if response.get("exceptionDetails"):
            raise RuntimeError("页面解析执行失败")
        return response.get("result", {}).get("value")

    async def navigate(self, url: str) -> None:
        if self.navigation_gate is not None:
            await self.navigation_gate.navigate()
        self.navigation_info = {"requested_url": url}
        response = await self.command("Page.navigate", {"url": url})
        if response.get("errorText"):
            raise RuntimeError("页面导航失败")
        async with asyncio.timeout(self.timeout):
            while True:
                tree = await self.command("Page.getFrameTree")
                committed = tree["frameTree"]["frame"].get("loaderId") == response.get("loaderId")
                ready = committed and await self.evaluate("document.readyState !== 'loading' && location.href !== 'about:blank'")
                if ready:
                    return
                await asyncio.sleep(0.25)

    async def close(self) -> None:
        if self.socket:
            try:
                await self.command("Browser.close")
            except Exception:
                pass
            await self.socket.close()
            self.socket = None
        if self.process and self.process.poll() is None:
            try:
                await asyncio.to_thread(self.process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    await asyncio.to_thread(
                        subprocess.run,
                        ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    self.process.kill()
                await asyncio.to_thread(self.process.wait, timeout=5)
        if self.profile:
            await asyncio.to_thread(self.profile.cleanup)
            self.profile = None
