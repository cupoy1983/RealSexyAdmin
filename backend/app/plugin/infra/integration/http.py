"""对外 HTTP 请求与 access_token 缓存的共用机制。

只做两件事：把请求发出去并把 JSON 拿回来；通行证先查 Redis、未命中再去取并写回缓存。
这里不认识快麦、也不认识钉钉——各系统的地址、参数、字段含义与错误码解释留在各自的
客户端里（见同目录下的 `*_client.py`）。

放置约定：本包（`app/plugin/infra`）**不放 controller**。路由发现只扫
`app/plugin/module_*/**/controller.py`，非 `module_` 目录下的 controller 不会注册，
且启动时没有任何警告（ADR-0035）。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from redis.asyncio.client import Redis

from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD

# access_token 缓存 TTL（秒）：各家有效期以官方文档为准，统一留 200s 余量
ACCESS_TOKEN_CACHE_TTL = 7000


async def request_json(method: str, url: str, system_label: str, **kwargs: Any) -> dict:
    """发起 HTTP 请求并解析 JSON 响应。

    参数:
    - method (str): HTTP 方法
    - url (str): 请求地址
    - system_label (str): 出错时用于提示的系统名（保持各系统原有的报错措辞）
    - kwargs: 透传给 httpx 的请求参数

    返回:
    - dict: 解析后的 JSON 响应
    """
    timeout = settings.HTTPX_DEFAULT_TIMEOUT
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request(method, url, **kwargs)
        r.raise_for_status()
        try:
            return r.json()
        except json.JSONDecodeError:
            logger.error(f"{system_label} API 非 JSON 响应: {r.text[:500]}")
            raise CustomException(msg=f"{system_label}接口返回异常")


async def cached_access_token(
    redis: Redis,
    *,
    cache_key: str,
    fetch: Callable[[], Awaitable[str]],
    system_label: str,
    expire: int = ACCESS_TOKEN_CACHE_TTL,
) -> str:
    """获取 access_token，优先读 Redis 缓存（多实例共享同一份通行证）。

    参数:
    - redis (Redis): Redis 连接
    - cache_key (str): 缓存键（取 RedisInitKeyConfig 里的枚举，不在这里拼字符串）
    - fetch (Callable[[], Awaitable[str]]): 缓存未命中时去对方换取通行证的动作
    - system_label (str): 日志里的系统名
    - expire (int): 缓存有效期（秒）

    返回:
    - str: access_token
    """
    rc = RedisCURD(redis)
    cached = await rc.get(cache_key)
    if cached:
        if isinstance(cached, bytes):
            cached = cached.decode("utf-8")
        return str(cached)

    token = await fetch()
    await rc.set(key=cache_key, value=str(token), expire=expire)
    logger.info(f"{system_label} access_token 已缓存，TTL={expire}s")
    return str(token)
