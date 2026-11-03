"""企业微信群机器人 webhook 推送。

用于推送每日「未完成清单」、跨月重算通知、刷单核对重大错误提醒等（§5.5/§5.2/§7）。
webhook URL/密钥存 ``.env``（``WECOM_WEBHOOK_URL/SECRET``），不入库/入代码明文。
"""

import base64
import hashlib
import hmac
import os
import time
from typing import Any
from urllib.parse import quote_plus

from app.config.setting import settings
from app.core.logger import logger

from .http import request_json


def _build_signed_webhook_url() -> str | None:
    """按企业微信群机器人签名规则拼出带签名的完整 webhook URL。

    未配置 WEBHOOK_URL 时返回 None（调用方应静默跳过，不影响业务主流程）。
    未配置 WEBHOOK_SECRET 时视为该机器人未启用签名校验，直接返回原始 URL。
    """
    # 配置定义在框架 Settings 之外，插件不能直接新增框架字段；环境变量仍按原约定读取。
    webhook_url = getattr(settings, "WECOM_WEBHOOK_URL", None) or os.getenv("WECOM_WEBHOOK_URL")
    if not webhook_url:
        return None

    secret = getattr(settings, "WECOM_WEBHOOK_SECRET", None) or os.getenv("WECOM_WEBHOOK_SECRET")
    if not secret:
        return webhook_url

    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(hmac_code))
    separator = "&" if "?" in webhook_url else "?"
    return f"{webhook_url}{separator}timestamp={timestamp}&sign={sign}"


async def send_text_message(content: str) -> bool:
    """发送一条纯文本消息到企业微信群。

    未配置 webhook 时静默跳过，不抛异常——通知属于增值能力，失败不应阻断主业务流程
    （与 ``wx_mini_service.send_subscribe_message`` 的静默失败策略一致）。

    参数:
    - content (str): 消息文本内容

    返回:
    - bool: 是否发送成功
    """
    url = _build_signed_webhook_url()
    if not url:
        logger.debug("企微群机器人消息发送跳过：WECOM_WEBHOOK_URL 未配置")
        return False

    payload: dict[str, Any] = {"msgtype": "text", "text": {"content": content}}

    try:
        data = await request_json("POST", url, "企微群机器人", json=payload)

        errcode = data.get("errcode")
        if errcode and errcode != 0:
            errmsg = data.get("errmsg") or "未知错误"
            logger.warning(f"企微群机器人消息发送失败: [{errcode}] {errmsg}")
            return False

        logger.info("企微群机器人消息发送成功")
        return True
    except Exception as e:
        logger.warning(f"企微群机器人消息发送异常: {e}")
        return False
