"""执行端主动轮询后台；只使用机器凭据，不导入后台设置或数据库。"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from .login_access import ticket

logger = logging.getLogger(__name__)


async def execute(manager, access, operation):
    manager.access = access
    platform, account = operation["platform"], operation["account"]
    if operation["kind"] == "check":
        return await manager.check(platform, account), None
    async with manager.lock:
        active = manager.active
        busy = active and (active["platform"], active["account"]) != (platform, account)
    if busy:
        return {"state": "unknown", "reason": "window_busy"}, None
    if not active:
        await manager.command("start", platform, account)
        active = manager.active
    result = await manager.check(platform, account)
    if result.get('reason') == 'window_busy':
        return result, None
    if result["state"] == "unknown":
        result = {"state": "login_required", "reason": "manual_login"}
    async with manager.lock:
        current = manager.active
        if (getattr(manager, 'closing', False) or not current or current is not active
                or (current['platform'], current['account']) != (platform, account)):
            return {'state': 'unknown', 'reason': 'window_busy'}, None
        access.begin_window(current)
        return result, ticket(access.key)


async def serve(manager, access):
    import httpx

    root = os.environ["LOGIN_API_URL"].rstrip("/")
    parsed = urlsplit(root)
    socket = os.environ.get("LOGIN_DEV_SOCKET")
    if parsed.scheme != "https" and not (socket and parsed.scheme == "http" and parsed.hostname == "selection-dev-relay"):
        raise ValueError("会话执行端需要 HTTPS，开发环境须显式指定专用套接字")
    credentials = Path(os.environ["LOGIN_CREDENTIALS_FILE"])
    if credentials.is_symlink():
        raise ValueError("机器凭据不能为符号链接")
    data = json.loads(credentials.read_text())
    if data.get("auth_type") != "machine" or data.get("api_url") != root or not data.get("api_key", "").startswith("selw_"):
        raise ValueError("会话执行端需要与后台匹配的机器凭据")
    transport = httpx.AsyncHTTPTransport(uds=socket) if socket else None
    async with httpx.AsyncClient(base_url=root + "/selection/platform-session-worker/", headers={"Authorization": "Bearer " + data["api_key"]},
                                 transport=transport, trust_env=False, timeout=30) as client:
        async def post(path, payload):
            response = await client.post(path, json=payload)
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 0:
                raise RuntimeError("后台拒绝平台会话请求")
            return result.get("data")

        async def heartbeat():
            # 领取和处理串行；长操作期间不另领任务，后台在线 TTL 足以覆盖常规轮询。
            return await post("poll", {"platforms": list(manager.sites)})

        observed = {}
        while True:
            try:
                operation = await heartbeat()
                if operation:
                    try:
                        result, entry = await execute(manager, access, operation)
                    except (RuntimeError, ValueError):
                        result, entry = {"state": "network_error", "reason": "network_failed"}, None
                    await post("result", {"operation_id": operation["operation_id"], "secret": operation["secret"], **result, "token": entry})
                    observed[(operation["platform"], operation["account"])] = (operation["generation"], result, manager.active is not None)
                # 只监测已开启人工窗口的作用域；不循环访问所有平台。
                for scope, (generation, last, was_active) in list(observed.items()):
                    if not was_active:
                        continue
                    current = await manager.check(*scope)
                    if current != last:
                        reply = await post("observation", {"platform": scope[0], "account": scope[1], "generation": generation,
                                                            "observed_at": time.time(), **current})
                        generation = reply["generation"]
                    observed[scope] = (generation, current, manager.active is not None)
            except httpx.HTTPStatusError as error:
                if error.response.status_code in {401, 403}:
                    raise RuntimeError("平台会话机器凭据已失效，请检查授权") from None
                logger.warning("平台会话请求暂未完成，操作保持后台超时规则，不自动重放")
            except (httpx.HTTPError, RuntimeError, ValueError):
                logger.warning("平台会话协调暂不可用，请检查连接或站点适配；不回传凭据及浏览器错误原文")
            await asyncio.sleep(5)
