"""快麦 ERP API 客户端。

第一期默认订单数据源仍是 Excel 导出（ADR-0009），本客户端作为备用 API 通路实现，
供 order 子域的 ``KuaimaiApiSource``（非默认 ``OrderDataSource`` 实现）调用。

发请求与通行证缓存用本包的共用机制（`http.py`）；本文件只保留快麦自己的地址、
参数与错误措辞。**目前仅电商插件在用**（ADR-0017）。
"""

from typing import Any

from redis.asyncio.client import Redis

from app.common.enums import RedisInitKeyConfig
from app.config.setting import settings
from app.core.exceptions import CustomException

from .http import cached_access_token, request_json

# 快麦 ERP API 基础地址（开放平台，具体路径以快麦官方文档为准）
_KUAIMAI_API_BASE = "https://openapi.kuaimai.com"

_TOKEN_URL = f"{_KUAIMAI_API_BASE}/token"
_SHOP_LIST_URL = f"{_KUAIMAI_API_BASE}/shop/list"


def _require_credentials() -> tuple[str, str]:
    """校验快麦 AppKey / AppSecret 是否已配置。"""
    app_key = settings.KUAIMAI_APP_KEY
    app_secret = settings.KUAIMAI_APP_SECRET
    if not app_key or not app_secret:
        raise CustomException(msg="快麦 ERP 未配置（AppKey / AppSecret 为空）")
    return app_key, app_secret


class KuaimaiClient:
    """快麦 ERP API 客户端：token 获取/缓存 + 业务接口调用。"""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def _get_access_token(self) -> str:
        """获取快麦 access_token，优先从 Redis 缓存读取。"""

        async def _fetch() -> str:
            app_key, app_secret = _require_credentials()
            data = await request_json(
                "POST",
                _TOKEN_URL,
                "快麦",
                json={"app_key": app_key, "app_secret": app_secret, "grant_type": "client_credential"},
            )
            token = data.get("access_token")
            if not token:
                errmsg = data.get("message") or data.get("errmsg") or "未知错误"
                raise CustomException(msg=f"获取快麦 access_token 失败: {errmsg}")
            return str(token)

        return await cached_access_token(
            self.redis,
            cache_key=RedisInitKeyConfig.KUAIMAI_ACCESS_TOKEN.key,
            fetch=_fetch,
            system_label="快麦 ERP",
        )

    async def list_shops(self) -> list[dict[str, Any]]:
        """拉取快麦店铺列表，用于验证联通性 / 与自动建档的店铺核对。

        返回:
        - list[dict]: 快麦店铺原始记录列表（字段名以快麦官方文档为准）
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "GET",
            _SHOP_LIST_URL,
            "快麦",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        shops = data.get("data")
        if not isinstance(shops, list):
            raise CustomException(msg="快麦店铺列表返回数据格式异常")
        return shops
