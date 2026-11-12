"""钉钉 API 客户端。

供收款审批（Payment Receipt Approval）与刷单核对所需的「销量权重」审批明细拉取
（ADR-0028，由 order 子域的刷单核对模块消费）。应用审批已通过，第一期直接接入。

发请求与通行证缓存用本包的共用机制（`http.py`）；本文件只保留钉钉自己的地址、
错误码语义与字段解释。**目前仅电商插件在用**（ADR-0035）。
"""

import json
from typing import Any

from redis.asyncio.client import Redis

from app.common.enums import RedisInitKeyConfig
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD

from .http import cached_access_token, request_json

# 钉钉开放平台 API 基础地址（老版：topapi 系列，token 放 URL query）
_DINGTALK_API_BASE = "https://oapi.dingtalk.com"
# 新版开放平台：智能人事的离职接口只存在于新版域名，老版 topapi 没有任何离职能力
_DINGTALK_V2_API_BASE = "https://api.dingtalk.com"

_TOKEN_URL = f"{_DINGTALK_API_BASE}/gettoken"
# 审批实例列表接口（具体路径/字段以钉钉开放平台「审批」API 文档为准）
_APPROVAL_INSTANCE_LIST_URL = f"{_DINGTALK_API_BASE}/topapi/processinstance/listids"
_APPROVAL_INSTANCE_DETAIL_URL = f"{_DINGTALK_API_BASE}/topapi/processinstance/get"
# 考勤打卡结果（老版接口：一次最多 50 人，起止工作日相隔最多 7 天，不支持查半年前数据）
_ATTENDANCE_LIST_URL = f"{_DINGTALK_API_BASE}/attendance/list"
# 通讯录部门用户详情（只取本部门，不含子部门）
_DEPT_USER_LIST_URL = f"{_DINGTALK_API_BASE}/topapi/v2/user/list"
# 通讯录写操作：创建/更新企业通讯录成员（不是替员工注册个人钉钉账号）
_USER_CREATE_URL = f"{_DINGTALK_API_BASE}/topapi/v2/user/create"
_USER_UPDATE_URL = f"{_DINGTALK_API_BASE}/topapi/v2/user/update"
# 按手机号查在职员工 userId（离职员工查不到）
_USER_GET_BY_MOBILE_URL = f"{_DINGTALK_API_BASE}/topapi/v2/user/getbymobile"
_USER_GET_URL = f"{_DINGTALK_API_BASE}/topapi/v2/user/get"
_USER_GROUP_URL = f"{_DINGTALK_API_BASE}/topapi/attendance/getusergroup"
# 智能考勤报表：列定义 + 列值
_ATTENDANCE_COLUMNS_URL = f"{_DINGTALK_API_BASE}/topapi/attendance/getattcolumns"
_ATTENDANCE_COLUMN_VAL_URL = f"{_DINGTALK_API_BASE}/topapi/attendance/getcolumnval"
# 部门详情（取部门主管 dept_manager_userid_list 与 parent_id）
_DEPT_GET_URL = f"{_DINGTALK_API_BASE}/topapi/v2/department/get"
# 企业管理员列表（sys_level: 1=主管理员 2=子管理员；不受通讯录权限范围限制）
_ADMIN_LIST_URL = f"{_DINGTALK_API_BASE}/topapi/user/listadmin"
# 新版：token 与智能人事离职（token 放 Header，不是 query）
_V2_TOKEN_URL = f"{_DINGTALK_V2_API_BASE}/v1.0/oauth2/accessToken"
_V2_DISMISSION_REASONS_URL = f"{_DINGTALK_V2_API_BASE}/v1.0/hrm/dismission/reasons"
_V2_TERMINATE_URL = f"{_DINGTALK_V2_API_BASE}/v1.0/hrm/processes/terminateAndHandOver"
# 企业内部应用机器人单聊：按钉钉 userId 发给员工本人，不经过任何群。
_ROBOT_OTO_BATCH_SEND_URL = f"{_DINGTALK_V2_API_BASE}/v1.0/robot/oToMessages/batchSend"

# 离职原因缓存 TTL：企业可自定义原因清单，但不至于每次删除都拉一次
_DISMISSION_REASONS_CACHE_TTL = 86400

# 根据手机号查用户：查无此人的错误码，业务上要当成「未找到」而不是失败
_ERRCODE_USER_NOT_FOUND = 60121


def _require_credentials() -> tuple[str, str]:
    """校验钉钉 AppKey / AppSecret 是否已配置。"""
    app_key = settings.DINGTALK_APP_KEY
    app_secret = settings.DINGTALK_APP_SECRET
    if not app_key or not app_secret:
        raise CustomException(msg="钉钉未配置（AppKey / AppSecret 为空）")
    return app_key, app_secret


def _check_dingtalk_error(data: dict, action: str) -> None:
    """统一检查钉钉 API 返回的 errcode。"""
    errcode = data.get("errcode")
    if errcode and errcode != 0:
        errmsg = data.get("errmsg") or "未知错误"
        raise CustomException(msg=f"钉钉{action}失败: [{errcode}] {errmsg}")


def _check_v2_error(data: dict, action: str) -> None:
    """检查新版域名（api.dingtalk.com）的响应。

    新版不用 errcode/errmsg 那套：成功时给 `result`（有的接口还带 `success: true`），
    失败时是 `message` + 非 2xx（已在 request_json 里被 raise_for_status 拦掉），
    也有少数接口回 success=false。两种情况都要接住。
    """
    errcode = data.get("errcode")
    if errcode not in (None, 0, "0"):
        errmsg = data.get("errmsg") or data.get("message") or "未知错误"
        raise CustomException(msg=f"钉钉{action}失败: [{errcode}] {errmsg}")
    if data.get("success") is False:
        raise CustomException(msg=f"钉钉{action}失败: {data.get('message') or '未知错误'}")


class DingtalkClient:
    """钉钉 API 客户端：token 获取/缓存 + 审批明细拉取。"""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def _get_access_token(self) -> str:
        """获取钉钉 access_token，优先从 Redis 缓存读取（多实例共享同一 token）。"""

        async def _fetch() -> str:
            app_key, app_secret = _require_credentials()
            data = await request_json(
                "GET",
                _TOKEN_URL,
                "钉钉",
                params={"appkey": app_key, "appsecret": app_secret},
            )
            _check_dingtalk_error(data, "获取 access_token")
            token = data.get("access_token")
            if not token:
                raise CustomException(msg="钉钉 access_token 返回数据不完整")
            return str(token)

        return await cached_access_token(
            self.redis,
            cache_key=RedisInitKeyConfig.DINGTALK_ACCESS_TOKEN.key,
            fetch=_fetch,
            system_label="钉钉",
            expire=settings.DINGTALK_ACCESS_TOKEN_CACHE_TTL,
        )

    async def _get_v2_access_token(self) -> str:
        """取新版域名（api.dingtalk.com）的 access_token，供智能人事接口使用。

        与老版 token 是两个不同的凭证、不能混用。**这里刻意不进 Redis 缓存**：
        为它加一条 RedisInitKeyConfig 枚举就要再动一次 `common/enums.py`，
        而离职是低频操作，现取的开销可以忽略。别"顺手优化"成缓存。
        """
        app_key, app_secret = _require_credentials()
        data = await request_json(
            "POST",
            _V2_TOKEN_URL,
            "钉钉",
            json={"appKey": app_key, "appSecret": app_secret},
        )
        _check_v2_error(data, "获取钉钉新版 access_token")
        token = data.get("accessToken")
        if not token:
            raise CustomException(msg="钉钉新版 access_token 返回数据不完整")
        return str(token)

    async def send_ro_text_to_user(self, *, userid: str, content: str) -> dict[str, Any]:
        """以企业内部应用机器人的身份向员工发送一条单聊文本消息。

        robotCode 企业内部应用的 AppKey；员工 userId 来自账号绑定表。
        工资等敏感数据应让通知只包含提醒和查看指引，不在这里拼具体金额。
        """
        token = await self._get_v2_access_token()
        data = await request_json(
            "POST",
            _ROBOT_OTO_BATCH_SEND_URL,
            "钉钉机器人单聊",
            headers={"x-acs-dingtalk-access-token": token},
            json={
                "robotCode": settings.DINGTALK_APP_KEY,
                "userIds": [userid],
                "msgKey": "sampleText",
                "msgParam": json.dumps({"content": content}, ensure_ascii=False),
            },
        )
        invalid_users = data.get("invalidStaffIdList") or []
        limited_users = data.get("flowControlledStaffIdList") or []
        if invalid_users or limited_users:
            raise CustomException(msg=f"钉钉机器人消息未送达: 无效用户={invalid_users} 限流用户={limited_users}")
        return {"process_query_key": data.get("processQueryKey") or "", "userid": userid}

    async def list_approval_instance_ids(
        self,
        *,
        process_code: str,
        start_time: int,
        end_time: int,
        cursor: int = 0,
        size: int = 20,
    ) -> dict[str, Any]:
        """按审批模板拉取审批实例 ID 列表（分页）。

        参数:
        - process_code (str): 审批模板唯一标识（「销量权重」审批模板）
        - start_time (int): 开始时间，毫秒时间戳
        - end_time (int): 结束时间，毫秒时间戳
        - cursor (int): 分页游标
        - size (int): 每页数量

        返回:
        - dict: 钉钉原始响应（包含 result.list / result.next_cursor）
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _APPROVAL_INSTANCE_LIST_URL,
            "钉钉",
            params={"access_token": access_token},
            json={
                "process_code": process_code,
                "start_time": start_time,
                "end_time": end_time,
                "cursor": cursor,
                "size": size,
            },
        )
        _check_dingtalk_error(data, "拉取审批实例列表")
        return data.get("result") or {}

    async def get_approval_instance_detail(self, *, process_instance_id: str) -> dict[str, Any]:
        """拉取单条审批实例详情（含表单明细字段，用于刷单订单号/金额逐笔核对）。

        参数:
        - process_instance_id (str): 审批实例 ID

        返回:
        - dict: 审批实例详情（含 form_component_values 等字段，以钉钉官方文档为准）
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "GET",
            _APPROVAL_INSTANCE_DETAIL_URL,
            "钉钉",
            params={"access_token": access_token, "process_instance_id": process_instance_id},
        )
        _check_dingtalk_error(data, "拉取审批实例详情")
        result = data.get("process_instance")
        if not isinstance(result, dict):
            raise CustomException(msg="钉钉审批实例详情返回数据格式异常")
        return result

    async def list_attendance_records(
        self,
        *,
        work_date_from: str,
        work_date_to: str,
        user_id_list: list[str],
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """批量拉取员工打卡结果（老版「获取打卡结果」接口）。

        钉钉侧的硬约束（不在本方法内做兜底，由调用方分批）：
        - user_id_list 一次最多 50 人
        - work_date_from / work_date_to 相隔最多 7 天（含）
        - 不支持查询半年以前的数据
        - 当天有排班但没打卡时，仍会返回当天的卡点信息（timeResult=NotSigned），不会返回空

        参数:
        - work_date_from (str): 起始工作日，格式 yyyy-MM-dd HH:mm:ss
        - work_date_to (str): 结束工作日，格式 yyyy-MM-dd HH:mm:ss
        - user_id_list (list[str]): 员工 userId 列表，最多 50 个
        - offset (int): 分页起点
        - limit (int): 每页条数，最大 50

        返回:
        - dict: {"recordresult": [...], "hasMore": bool}
        """
        if not user_id_list:
            return {"recordresult": [], "hasMore": False}

        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _ATTENDANCE_LIST_URL,
            "钉钉",
            params={"access_token": access_token},
            json={
                "workDateFrom": work_date_from,
                "workDateTo": work_date_to,
                "userIdList": user_id_list,
                "offset": offset,
                "limit": min(limit, 50),
                "isI18n": False,
            },
        )
        _check_dingtalk_error(data, "拉取考勤打卡结果")
        return {
            "recordresult": data.get("recordresult") or [],
            "hasMore": bool(data.get("hasMore")),
        }

    async def list_dept_users(
        self,
        *,
        dept_id: int,
        cursor: int = 0,
        size: int = 100,
    ) -> dict[str, Any]:
        """拉取指定部门下的员工详情（用于把钉钉 userId 与 OA 用户对齐）。

        注意：本接口只返回该部门直属成员，不含子部门。要拿全公司需调用方自行遍历子部门，
        或直接把 DINGTALK_ATTENDANCE_DEPT_ID 配成根部门 1 再配合通讯录权限使用。

        参数:
        - dept_id (int): 部门 ID，根部门为 1
        - cursor (int): 分页游标，首次传 0，后续传返回的 next_cursor
        - size (int): 每页条数，最大 100

        返回:
        - dict: {"list": [{"userid","name","mobile",...}], "has_more": bool, "next_cursor": int}
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _DEPT_USER_LIST_URL,
            "钉钉",
            params={"access_token": access_token},
            json={
                "dept_id": dept_id,
                "cursor": cursor,
                "size": min(size, 100),
                "contain_access_limit": False,
                "language": "zh_CN",
            },
        )
        _check_dingtalk_error(data, "拉取通讯录部门用户")
        result = data.get("result") or {}
        return {
            "list": result.get("list") or [],
            "has_more": bool(result.get("has_more")),
            "next_cursor": int(result.get("next_cursor") or 0),
        }

    async def get_user_id_by_mobile(self, *, mobile: str) -> str | None:
        """按手机号查钉钉在职成员 userId；查不到返回 None（不是异常）。

        参数:
        - mobile (str): 手机号

        返回:
        - str | None: 钉钉 userId；钉钉返回「未找到该用户」时为 None
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _USER_GET_BY_MOBILE_URL,
            "钉钉",
            params={"access_token": access_token},
            json={"mobile": mobile},
        )
        if str(data.get("errcode")) == str(_ERRCODE_USER_NOT_FOUND):
            return None
        _check_dingtalk_error(data, "按手机号查询钉钉用户")
        return str((data.get("result") or {}).get("userid") or "") or None

    async def get_user_detail(self, *, userid: str) -> dict[str, Any]:
        """按 userId 取钉钉通讯录成员详情（主要取工号 job_number 与部门）。

        参数:
        - userid (str): 钉钉 userId

        返回:
        - dict[str, Any]: result 内容；取不到时返回空字典
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _USER_GET_URL,
            "钉钉",
            params={"access_token": access_token},
            json={"userid": userid},
        )
        if str(data.get("errcode")) not in ("0", "None"):
            return {}
        return dict(data.get("result") or {})

    async def get_user_group(self, *, userid: str) -> dict[str, Any]:
        """按 userId 取该成员所属考勤组（一个员工只属于一个考勤组）。

        **「空」有两种含义，调用方必须分开处理，混为一谈会删错数据**：
        - 正常返回 `{"group_id": "", "name": ""}` —— 接口通了，这个人**真的**不在任何考勤组里；
        - 抛 `CustomException` —— 接口层面失败（errcode 非 0 或网络问题），
          此时「他有没有考勤组」是**未知**的，绝不能当成"没有"来处理。

        参数:
        - userid (str): 钉钉 userId

        返回:
        - dict[str, Any]: {"group_id": str, "name": str}；不在任何考勤组时两个字段都是空串

        异常:
        - CustomException: 钉钉接口返回 errcode 非 0
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _USER_GROUP_URL,
            "钉钉",
            params={"access_token": access_token},
            json={"userid": userid},
        )
        if str(data.get("errcode")) not in ("0", "None"):
            raise CustomException(msg=f"钉钉获取考勤组失败: [{data.get('errcode')}] {data.get('errmsg') or '未知错误'}")
        result = dict(data.get("result") or {})
        return {"group_id": str(result.get("group_id") or ""), "name": str(result.get("name") or "")}

    async def create_user(self, *, name: str, mobile: str, dept_id_list: str, job_number: str | None = None) -> str:
        """创建钉钉企业通讯录成员。

        参数:
        - name (str): 姓名
        - mobile (str): 手机号（企业内必须唯一）
        - dept_id_list (str): 钉钉部门ID列表，多个用英文逗号分隔
        - job_number (str | None): 工号

        返回:
        - str: 新建成员的 userId
        """
        access_token = await self._get_access_token()
        payload: dict[str, Any] = {
            "name": name,
            "mobile": mobile,
            "dept_id_list": dept_id_list,
        }
        if job_number:
            payload["job_number"] = job_number

        data = await request_json(
            "POST",
            _USER_CREATE_URL,
            "钉钉",
            params={"access_token": access_token},
            json=payload,
        )
        _check_dingtalk_error(data, "创建钉钉通讯录成员")
        userid = str((data.get("result") or {}).get("userid") or "")
        if not userid:
            raise CustomException(msg="钉钉创建通讯录成员返回数据不完整")
        return userid

    async def update_user(
        self,
        *,
        userid: str,
        name: str | None = None,
        mobile: str | None = None,
        dept_id_list: str | None = None,
    ) -> bool:
        """更新钉钉企业通讯录成员资料（只传需要改的字段，不重新绑定/换人）。"""
        payload: dict[str, Any] = {"userid": userid}
        if name:
            payload["name"] = name
        if mobile:
            payload["mobile"] = mobile
        if dept_id_list:
            payload["dept_id_list"] = dept_id_list

        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _USER_UPDATE_URL,
            "钉钉",
            params={"access_token": access_token},
            json=payload,
        )
        _check_dingtalk_error(data, "更新钉钉通讯录成员")
        return True

    async def get_attendance_columns(self) -> list[dict[str, Any]]:
        """拉取智能考勤报表列定义，用于按名称定位列 ID。"""
        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _ATTENDANCE_COLUMNS_URL,
            "钉钉",
            params={"access_token": access_token},
            json={},
        )
        _check_dingtalk_error(data, "获取考勤报表列定义")
        return list((data.get("result") or {}).get("columns") or [])

    async def get_attendance_column_values(
        self,
        *,
        userid: str,
        column_id_list: list[str],
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """拉取单人智能考勤报表列值（一次最多 20 列，由调用方分批）。"""
        if not column_id_list:
            return []

        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _ATTENDANCE_COLUMN_VAL_URL,
            "钉钉",
            params={"access_token": access_token},
            json={
                "userid": userid,
                "column_id_list": ",".join(str(cid) for cid in column_id_list),
                "from_date": from_date,
                "to_date": to_date,
            },
        )
        _check_dingtalk_error(data, "获取考勤报表列值")
        return list((data.get("result") or {}).get("column_vals") or [])

    # ── 离职相关（ADR-0007）─────────────────────────────────────────────
    async def get_user_dept_id_list(self, *, userid: str) -> list[int]:
        """取成员所属的钉钉部门 ID 列表（找部门主管的起点）。"""
        detail = await self.get_user_detail(userid=userid)
        raw = detail.get("dept_id_list") or []
        return [int(i) for i in raw if str(i).lstrip("-").isdigit()]

    async def get_dept_detail(self, *, dept_id: int) -> dict[str, Any]:
        """取部门详情，主要取 `dept_manager_userid_list`（部门主管，多值）与 `parent_id`。

        「部门主管」挂在部门上、不在管理员体系里，所以 `listadmin` 查不到它。
        未设置主管时的返回形态官方文档没写，调用方要防住字段缺失 / null / 空数组三态。
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _DEPT_GET_URL,
            "钉钉",
            params={"access_token": access_token},
            json={"dept_id": dept_id},
        )
        _check_dingtalk_error(data, "查询钉钉部门详情")
        return dict(data.get("result") or {})

    async def list_admin_userids(self) -> dict[int, list[str]]:
        """取企业管理员 userid，按 `sys_level` 分组：1=主管理员，2=子管理员。

        本接口官方明确「不受通讯录权限范围限制」，所以它能拿到全企业的管理员。
        """
        access_token = await self._get_access_token()
        data = await request_json(
            "POST",
            _ADMIN_LIST_URL,
            "钉钉",
            params={"access_token": access_token},
        )
        _check_dingtalk_error(data, "查询钉钉管理员列表")
        grouped: dict[int, list[str]] = {1: [], 2: []}
        for item in data.get("result") or []:
            userid = str(item.get("userid") or "")
            level = int(item.get("sys_level") or 0)
            if userid and level in grouped:
                grouped[level].append(userid)
        return grouped

    async def list_dismission_reasons(self) -> dict[str, list[dict[str, str]]]:
        """取企业已配置的离职原因（新版域名），按主动/被动分组。

        原因 ID 由企业自定义且可增删，官方文档不保证 ID 稳定，**不能硬编码**。
        结果缓存 24 小时 —— 缓存键从已有枚举派生，不为此新增 RedisInitKeyConfig 条目。
        """
        cache_key = f"{RedisInitKeyConfig.DINGTALK_ACCESS_TOKEN.key}:dismission_reasons"
        rc = RedisCURD(self.redis)
        cached = await rc.get(cache_key)
        if cached:
            try:
                raw = cached if isinstance(cached, str) else cached.decode("utf-8")
                return dict(json.loads(raw))
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                logger.warning("钉钉离职原因缓存解析失败，重新拉取")

        access_token = await self._get_v2_access_token()
        data = await request_json(
            "GET",
            _V2_DISMISSION_REASONS_URL,
            "钉钉",
            headers={"x-acs-dingtalk-access-token": access_token},
        )
        _check_v2_error(data, "获取钉钉离职原因")
        result = dict(data.get("result") or {})

        def _pack(items: Any) -> list[dict[str, str]]:
            return [{"id": str(i.get("id") or ""), "name": str(i.get("name") or "")} for i in items or [] if i.get("id")]

        payload = {
            "voluntary": _pack(result.get("voluntaryList")),
            "passive": _pack(result.get("passiveList")),
        }
        await rc.set(
            key=cache_key,
            value=json.dumps(payload, ensure_ascii=False),
            expire=_DISMISSION_REASONS_CACHE_TTL,
        )
        return payload

    async def dismiss_user(
        self,
        *,
        userid: str,
        opt_userid: str,
        last_work_date_ms: int,
        memo: str,
        passive_reason_id: str,
    ) -> bool:
        """智能人事「确认员工离职并删除」。

        **不可逆**：钉钉没有撤销离职的接口，调用前请确认预检已全部通过。
        离职后该员工从通讯录移除、转入智能人事「已离职人员」，
        且 `getcolumnval` 不再返回他的考勤数据 —— 所以考勤要先落库再办离职。
        """
        access_token = await self._get_v2_access_token()
        data = await request_json(
            "POST",
            _V2_TERMINATE_URL,
            "钉钉",
            headers={"x-acs-dingtalk-access-token": access_token},
            json={
                "userId": userid,
                "optUserId": opt_userid,
                "lastWorkDate": last_work_date_ms,
                "dismissionMemo": memo,
                "terminationReasonPassive": [passive_reason_id],
            },
        )
        _check_v2_error(data, "办理钉钉员工离职")
        return True
