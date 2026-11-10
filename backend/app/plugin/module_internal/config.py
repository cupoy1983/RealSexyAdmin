"""内务中心 · 公司休息日「考勤组方案」配置（读后端 env）。

配置写在后端 env 文件里，一行 JSON：

    INTERNAL_REST_DAY_POLICY={"<见本机运维记录>":{"mode":1},"330445194":{"mode":2,"money":100}}

- mode=1（休息）：该组登记公司休息日后按 ADR-0009 现行规则重算考勤
  （应出勤减少、异常清零、日明细显示「公司休息」），不发补贴。
- mode=2（补工资）：该组登记的公司休息日照常上班，考勤统计完全不改动；
  当天实际正常上班才按 money 元/天补进当月工资条。

约定：
- key 是钉钉考勤组 ID 的**字符串**（钉钉返回数字，落库是 varchar）。
- 没配置 group_id 时：登记公司休息日直接拒绝；生成工资条按「没有公司休息日」算。
- mode 只支持 1/2，未知值一律报错；mode=2 必须带正数 money，mode=1 不带 money。
- 不改框架 app/config/setting.py；env 文件在进程启动时读一次，改配置需重启后端。
  进程环境变量优先于 env 文件（容器注入 / 测试注入都走这条）。
"""

import json
import os
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.path_conf import ENV_DIR
from app.core.exceptions import CustomException

POLICY_ENV_KEY = "INTERNAL_REST_DAY_POLICY"
NO_POLICY_MESSAGE = "该考勤组未配置休息日方案，请先配置再使用！"


class RestDayGroupPolicy(BaseModel):
    """一个考勤组的公司休息日处理方案。"""

    mode: Literal[1, 2]
    money: Decimal | None = None

    @model_validator(mode="after")
    def _validate_money(self) -> "RestDayGroupPolicy":
        if self.mode == 2:
            if self.money is None or self.money <= 0:
                raise ValueError("mode=2 必须配置正数 money（每天补贴金额）")
        elif self.money not in (None, 0):
            raise ValueError("mode=1 不发补贴，不要配置 money")
        return self


class _InternalSettings(BaseSettings):
    """只读内务中心自己的配置项，避免往框架 Settings 里加字段。"""

    model_config = SettingsConfigDict(
        env_file=ENV_DIR / f".env.{os.getenv('ENVIRONMENT', 'dev')}",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    INTERNAL_REST_DAY_POLICY: str = ""


@lru_cache(maxsize=1)
def _env_settings() -> _InternalSettings:
    return _InternalSettings()


def _parse(raw: str) -> dict[str, RestDayGroupPolicy]:
    if not (raw or "").strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CustomException(msg=f"内务中心休息日方案配置不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CustomException(msg="内务中心休息日方案配置必须是 {group_id: {mode, money}} 对象")

    policy: dict[str, RestDayGroupPolicy] = {}
    for group_id, item in data.items():
        gid = str(group_id).strip()
        if not gid:
            raise CustomException(msg="内务中心休息日方案配置里有空的 group_id")
        try:
            policy[gid] = RestDayGroupPolicy.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            raise CustomException(
                msg=f"考勤组 {gid} 的休息日方案配置不合法（mode 只支持 1/2，"
                f"mode=2 必须带正数 money）: {first.get('msg', '未知错误')}"
            ) from exc
    return policy


def load_policy() -> dict[str, RestDayGroupPolicy]:
    """读取并校验当前生效的方案表。每次调用重新解析，配置量很小。"""
    raw = os.environ.get(POLICY_ENV_KEY) or _env_settings().INTERNAL_REST_DAY_POLICY
    return _parse(raw)


def get_policy(group_id: str | None) -> RestDayGroupPolicy | None:
    """按钉钉考勤组 ID 取方案；未配置返回 None。"""
    if not group_id:
        return None
    return load_policy().get(str(group_id))


def reload_policy() -> None:
    """清掉 env 文件缓存（改了 env 文件后重启后端即可，本函数仅供测试/诊断）。"""
    _env_settings.cache_clear()
