from datetime import datetime, timedelta, timezone
from redis.asyncio.client import Redis
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.ap_scheduler import SchedulerUtil
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.modules.system.user.model import UserModel
from app.plugin.module_internal.utils import fetch_dept_names
from app.plugin.infra.integration.dingtalk_client import DingtalkClient
from app.utils.common_util import search_to_dict

from .crud import DeptMappingCRUD, ExternalAccountBindingCRUD
from .model import DeptMappingModel, ExternalAccountBindingModel
from .schema import (
    SYSTEM_DINGTALK,
    DeptMappingCreateSchema,
    DeptMappingOutSchema,
    DeptMappingQueryParam,
    DeptMappingUpdateSchema,
    DingtalkAccountOutSchema,
    DingtalkAccountQueryParam,
    DingtalkManualMatchResultSchema,
    DingtalkManualMatchSchema,
    ExternalAccountBindingOutSchema,
    ExternalAccountBindingQueryParam,
)

# 离职联动（ADR-0007）
_DISMISSION_MEMO = "erp系统离职流程"  # 钉钉 dismissionMemo，ERP 发起的离职统一用这个备注
_PASSIVE_REASON_PREFERRED = ("其他", "协议解除")  # 被动离职原因按名称的匹配优先级
_CST = timezone(timedelta(hours=8))  # 离职日期按东八区自然日算


class DeptMappingService:
    """部门映射服务"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    @staticmethod
    def _enrich(items: list[DeptMappingOutSchema], objs: list[DeptMappingModel]) -> None:
        for item, obj in zip(items, objs, strict=True):
            item.oa_dept_name = obj.dept.name if obj.dept else None

    async def page(self, page_no: int, page_size: int, search: DeptMappingQueryParam | None = None) -> PageResultSchema[DeptMappingOutSchema]:
        offset = (page_no - 1) * page_size
        result = await DeptMappingCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=[{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=DeptMappingOutSchema,
        )
        objs = await DeptMappingCRUD(self.auth, self.db).get_list(search=search_to_dict(search), order_by=[{"id": "desc"}])
        self._enrich(list(result.items), list(objs)[offset : offset + page_size])
        return result

    async def get_list(self, search: DeptMappingQueryParam | None = None) -> list[DeptMappingOutSchema]:
        objs = await DeptMappingCRUD(self.auth, self.db).get_list(search=search_to_dict(search), order_by=[{"id": "desc"}])
        items = [DeptMappingOutSchema.model_validate(o) for o in objs]
        self._enrich(items, list(objs))
        return items

    async def detail(self, id: int) -> DeptMappingOutSchema:
        obj = await DeptMappingCRUD(self.auth, self.db).get_or_404(id=id, msg="该部门映射不存在")
        item = DeptMappingOutSchema.model_validate(obj)
        self._enrich([item], [obj])
        return item

    async def create(self, data: DeptMappingCreateSchema) -> DeptMappingOutSchema:
        crud = DeptMappingCRUD(self.auth, self.db)
        if await crud.get(oa_dept_id=data.oa_dept_id):
            raise CustomException(msg="新增失败，该 OA 部门已配置钉钉部门映射")
        obj = await crud.create(data=data)
        item = DeptMappingOutSchema.model_validate(obj)
        self._enrich([item], [obj])
        return item

    async def update(self, id: int, data: DeptMappingUpdateSchema) -> DeptMappingOutSchema:
        crud = DeptMappingCRUD(self.auth, self.db)
        _ = await crud.get_or_404(id=id, msg="更新失败，该部门映射不存在")
        obj = await crud.update(id=id, data=data)
        item = DeptMappingOutSchema.model_validate(obj)
        self._enrich([item], [obj])
        return item

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        await DeptMappingCRUD(self.auth, self.db).delete(ids=ids)


class ExternalAccountService:
    """外部账号绑定服务（本期只暴露钉钉；快麦/企业微信按系统类型预留）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def page(self, page_no: int, page_size: int, search: ExternalAccountBindingQueryParam | None = None) -> PageResultSchema[ExternalAccountBindingOutSchema]:
        offset = (page_no - 1) * page_size
        return await ExternalAccountBindingCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=[{"id": "desc"}],
            search=search_to_dict(search),
            out_schema=ExternalAccountBindingOutSchema,
        )

    async def get_list(self, search: ExternalAccountBindingQueryParam | None = None) -> list[ExternalAccountBindingOutSchema]:
        objs = await ExternalAccountBindingCRUD(self.auth, self.db).get_list(search=search_to_dict(search), order_by=[{"id": "desc"}])
        return [ExternalAccountBindingOutSchema.model_validate(o) for o in objs]


class DingtalkAccountService:
    """钉钉账号服务

    负责：钉钉账号 Tab 列表、未绑定用户的手工匹配、新建用户联动创建/绑定、
    绑定后手机号变更同步。真实逻辑预写在这里，定时任务节点只调用它。
    """

    def __init__(self, auth: AuthSchema, db: AsyncSession, redis: Redis | None = None) -> None:
        self.auth = auth
        self.db = db
        self.redis = redis

    # ── 公共 ──────────────────────────────────────────────────────
    @staticmethod
    def _require_configured() -> None:
        if not (settings.DINGTALK_APP_KEY and settings.DINGTALK_APP_SECRET):
            raise CustomException(msg="钉钉未配置，请先在服务端环境变量中设置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET")

    def _client(self) -> DingtalkClient:
        self._require_configured()
        redis = self.redis or SchedulerUtil.redis_instance
        if redis is None:
            raise CustomException(msg="Redis 不可用，无法获取钉钉 access_token")
        return DingtalkClient(redis)

    async def _load_user(self, oa_user_id: int) -> UserModel:
        user = (await self.db.execute(select(UserModel).where(UserModel.id == oa_user_id, UserModel.is_deleted == false()))).scalars().first()
        if not user:
            raise CustomException(msg=f"用户[{oa_user_id}]不存在")
        return user

    @staticmethod
    def _match_state(*, bound: bool, mobile: str | None) -> tuple[bool, str | None]:
        """计算是否允许手工匹配，以及不可匹配原因"""
        if bound:
            return False, "已绑定钉钉账号，不允许修改或解除"
        if not mobile:
            return False, "该用户没有手机号，无法按手机号匹配"
        return True, None

    # ── 钉钉账号 Tab ───────────────────────────────────────────────
    async def page_oa_users(
        self,
        page_no: int = 1,
        page_size: int = 20,
        search: DingtalkAccountQueryParam | None = None,
    ) -> PageResultSchema[DingtalkAccountOutSchema]:
        """以 OA 用户为主体分页展示钉钉绑定状态"""
        conditions = [UserModel.is_deleted == false()]
        join_cond = and_(
            ExternalAccountBindingModel.oa_user_id == UserModel.id,
            ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
            ExternalAccountBindingModel.is_deleted == false(),
        )

        if search:
            if search.name:
                keyword = f"%{search.name}%"
                conditions.append(or_(UserModel.name.like(keyword), UserModel.username.like(keyword)))
            if search.mobile:
                conditions.append(UserModel.mobile.like(f"%{search.mobile}%"))
            if search.dept_id:
                conditions.append(UserModel.dept_id == search.dept_id)
            if search.bound is True:
                conditions.append(ExternalAccountBindingModel.oa_user_id.isnot(None))
            if search.bound is False:
                conditions.append(ExternalAccountBindingModel.oa_user_id.is_(None))

        base_sql = select(UserModel, ExternalAccountBindingModel).outerjoin(ExternalAccountBindingModel, join_cond).where(*conditions)
        total = (await self.db.execute(select(func.count(UserModel.id)).select_from(UserModel).outerjoin(ExternalAccountBindingModel, join_cond).where(*conditions))).scalar() or 0

        offset = (page_no - 1) * page_size
        rows = (await self.db.execute(base_sql.order_by(UserModel.id.asc()).offset(offset).limit(page_size))).all()

        # 部门名单独成批查一次：直接访问 user.dept 会命中未配加载策略的
        # relationship，在 async 会话里抛 MissingGreenlet
        dept_names = await fetch_dept_names(self.db, [user.dept_id for user, _ in rows])

        items: list[DingtalkAccountOutSchema] = []
        for user, binding in rows:
            bound = binding is not None
            can_match, message = self._match_state(bound=bound, mobile=(user.mobile or "").strip() or None)
            items.append(
                DingtalkAccountOutSchema(
                    oa_user_id=user.id,
                    username=user.username,
                    name=user.name,
                    mobile=user.mobile,
                    dept_id=user.dept_id,
                    dept_name=dept_names.get(user.dept_id) if user.dept_id else None,
                    user_status=user.status,
                    bound=bound,
                    binding_id=binding.id if binding else None,
                    external_user_id=binding.external_user_id if binding else None,
                    external_name=binding.external_name if binding else None,
                    bound_time=binding.created_time if binding else None,
                    can_match=can_match,
                    match_message=message,
                )
            )

        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + page_size < total,
            items=items,
        )

    # ── 绑定 ──────────────────────────────────────────────────────
    async def _create_binding(self, *, user: UserModel, external_user_id: str, mobile: str | None) -> ExternalAccountBindingModel:
        crud = ExternalAccountBindingCRUD(self.auth, self.db)
        if await crud.get_by_oa_user(SYSTEM_DINGTALK, user.id):
            raise CustomException(msg=f"用户[{user.name}]已绑定钉钉账号")
        if await crud.get_by_external_user(SYSTEM_DINGTALK, external_user_id):
            raise CustomException(msg=f"钉钉成员[{external_user_id}]已绑定其他系统用户")
        return await crud.create(
            data={
                "system_type": SYSTEM_DINGTALK,
                "oa_user_id": user.id,
                "external_user_id": external_user_id,
                "external_name": user.name,
                "mobile": mobile,
                "status": 0,
            }
        )

    async def manual_match(self, data: DingtalkManualMatchSchema) -> DingtalkManualMatchResultSchema:
        """未绑定用户按手机号手工匹配钉钉成员（只匹配已存在的成员，不创建）"""
        self._require_configured()
        user = await self._load_user(data.oa_user_id)
        mobile = (user.mobile or "").strip()
        if not mobile:
            raise CustomException(msg="该用户没有手机号，无法按手机号匹配")
        if await ExternalAccountBindingCRUD(self.auth, self.db).get_by_oa_user(SYSTEM_DINGTALK, user.id):
            raise CustomException(msg="该用户已绑定钉钉账号，不允许重新匹配")

        client = self._client()
        external_user_id = await client.get_user_id_by_mobile(mobile=mobile)
        if not external_user_id:
            raise CustomException(msg="钉钉中未找到相同手机号的成员，无法手工匹配")

        await self._create_binding(user=user, external_user_id=external_user_id, mobile=mobile)
        logger.info(f"钉钉账号手工匹配完成: user={user.name}({user.id}) -> {external_user_id}")
        return DingtalkManualMatchResultSchema(
            oa_user_id=user.id,
            external_user_id=external_user_id,
            created=False,
            message="已按手机号绑定钉钉成员",
        )

    async def bind_or_create_for_new_user(self, oa_user_id: int) -> DingtalkManualMatchResultSchema:
        """新建 OA 用户后联动钉钉：已有成员直接绑定，没有则创建通讯录成员再绑定"""
        self._require_configured()
        user = await self._load_user(oa_user_id)
        mobile = (user.mobile or "").strip()
        if not mobile:
            raise CustomException(msg="新建用户联动钉钉失败：手机号不能为空")
        if not user.dept_id:
            raise CustomException(msg="新建用户联动钉钉失败：未设置所属部门")

        mapping = await DeptMappingCRUD(self.auth, self.db).get_by_oa_dept(oa_dept_id=user.dept_id)
        if not mapping:
            raise CustomException(msg=f"新建用户联动钉钉失败：部门[{user.dept_id}]未配置钉钉部门映射")

        client = self._client()
        external_user_id = await client.get_user_id_by_mobile(mobile=mobile)
        created = False
        if not external_user_id:
            external_user_id = await client.create_user(name=user.name, mobile=mobile, dept_id_list=mapping.dingtalk_dept_id)
            created = True

        await self._create_binding(user=user, external_user_id=external_user_id, mobile=mobile)
        logger.info(f"新建用户联动钉钉完成: user={user.name}({user.id}) -> {external_user_id} (created={created})")
        return DingtalkManualMatchResultSchema(
            oa_user_id=user.id,
            external_user_id=external_user_id,
            created=created,
            message="已创建钉钉通讯录成员并绑定" if created else "已绑定钉钉已有成员",
        )

    async def sync_mobile_change(self, oa_user_id: int, old_mobile: str | None, new_mobile: str | None) -> None:
        """绑定用户修改手机号：同步钉钉成员资料，不重新匹配、不换绑"""
        old_mobile = (old_mobile or "").strip()
        new_mobile = (new_mobile or "").strip()
        if not new_mobile or old_mobile == new_mobile:
            return

        binding = await ExternalAccountBindingCRUD(self.auth, self.db).get_by_oa_user(SYSTEM_DINGTALK, oa_user_id)
        if not binding:
            return

        self._require_configured()
        client = self._client()
        await client.update_user(userid=binding.external_user_id, mobile=new_mobile)
        await ExternalAccountBindingCRUD(self.auth, self.db).update(id=binding.id, data={"mobile": new_mobile})
        logger.info(f"钉钉成员手机号同步完成: user={oa_user_id} -> {new_mobile}")

    # ── 离职联动（ADR-0007）─────────────────────────────────────────────
    async def dismiss_users(self, oa_user_ids: list[int]) -> list[str]:
        """删除 OA 用户时，把对应钉钉成员办离职，并软删绑定记录。

        顺序是设计的一部分（别调换）：
        1. 全量预检 —— 任一失败就整批不动，OA 也不删；
        2. 考勤预同步（当月 + 上月）—— 离职后 `getcolumnval` 查不到该员工数据；
        3. 逐个办离职 —— 钉钉没有撤销离职的接口，**不可逆**；
        4. 软删绑定记录 —— 否则留下幽灵绑定，同手机号重新建档会被挡。

        参数:
        - oa_user_ids (list[int]): 待删除的 OA 用户 ID

        返回:
        - list[str]: 跳过钉钉的人的说明（没绑定钉钉的成员），供调用方提示
        """
        if not oa_user_ids:
            return []

        self._require_configured()
        crud = ExternalAccountBindingCRUD(self.auth, self.db)
        wanted = set(oa_user_ids)
        bindings = {b.oa_user_id: b for b in await crud.list_by_system(SYSTEM_DINGTALK) if b.oa_user_id in wanted}

        skipped = [f"用户[{uid}] 未绑定钉钉账号，跳过钉钉离职" for uid in oa_user_ids if uid not in bindings]
        targets = [uid for uid in oa_user_ids if uid in bindings]
        if not targets:
            return skipped

        client = self._client()

        # 阶段一：预检（把能提前知道的失败都拦在这里，避免钉钉侧出现"办了一半"）
        passive_reason_id = await self._pick_passive_reason_id(client)
        admins = await client.list_admin_userids()
        main_admin = (admins.get(1) or [None])[0]
        checked: list[tuple[int, str, str]] = []
        for uid in targets:
            dingtalk_userid = bindings[uid].external_user_id
            if dingtalk_userid in admins.get(1, []):
                raise CustomException(msg=f"用户[{uid}] 预检失败：钉钉主管理员不支持通过该接口离职")
            if not await client.get_user_detail(userid=dingtalk_userid):
                raise CustomException(msg=f"用户[{uid}] 预检失败：钉钉侧查不到该成员")
            opt_userid = await self._resolve_opt_userid(client, userid=dingtalk_userid, main_admin=main_admin)
            if not opt_userid or opt_userid == dingtalk_userid:
                raise CustomException(msg=f"用户[{uid}] 预检失败：找不到合法的离职操作人")
            checked.append((uid, dingtalk_userid, opt_userid))

        # 阶段二：考勤预同步（失败不阻断离职，只记日志 —— 别让考勤数据缺失把人卡在系统里）
        await self._sync_attendance_before_dismiss(oa_user_ids=targets)

        # 阶段三：逐个办离职（不可逆）
        last_work_date_ms = int(
            datetime.now(_CST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        )
        for uid, dingtalk_userid, opt_userid in checked:
            await client.dismiss_user(
                userid=dingtalk_userid,
                opt_userid=opt_userid,
                last_work_date_ms=last_work_date_ms,
                memo=_DISMISSION_MEMO,
                passive_reason_id=passive_reason_id,
            )
            await crud.delete(ids=[bindings[uid].id])
            logger.info(f"钉钉离职完成: user={uid} -> {dingtalk_userid}")

        return skipped

    @staticmethod
    async def _pick_passive_reason_id(client: DingtalkClient) -> str:
        """挑一个被动离职原因 ID。

        ERP 发起、员工本人没提申请，按 HR 通用口径归**被动**。
        原因 ID 是企业自定义的、官方文档不保证稳定，所以按名称匹配、不硬编码。
        """
        reasons = await client.list_dismission_reasons()
        passive = reasons.get("passive") or []
        if not passive:
            raise CustomException(msg="未配置钉钉被动离职原因，无法执行离职")
        for preferred in _PASSIVE_REASON_PREFERRED:
            for item in passive:
                if item.get("name") == preferred:
                    return str(item["id"])
        return str(passive[0]["id"])

    @staticmethod
    async def _resolve_opt_userid(client: DingtalkClient, *, userid: str, main_admin: str | None) -> str | None:
        """定离职操作人：部门主管 → 向上递归父部门 → 企业主管理员。

        钉钉没定义"上级部门主管可以代办"这种继承规则，向上递归是本系统的产品口径。
        """
        queue = await client.get_user_dept_id_list(userid=userid)
        seen: set[int] = set()
        while queue:
            dept_id = queue.pop(0)
            if dept_id in seen:
                continue
            seen.add(dept_id)
            detail = await client.get_dept_detail(dept_id=dept_id)
            managers = [m for m in (detail.get("dept_manager_userid_list") or []) if m and m != userid]
            if managers:
                return str(managers[0])
            parent_id = detail.get("parent_id")
            if parent_id and int(parent_id) not in seen:
                queue.append(int(parent_id))
        return main_admin

    async def _sync_attendance_before_dismiss(self, *, oa_user_ids: list[int]) -> None:
        """离职前把当月 + 上月的考勤月报落库（离职后就查不到了）。

        失败只记日志、不抛错：考勤数据缺失不该把人卡在 OA 里删不掉。
        """
        from app.plugin.module_internal.attendance.service import MonthlyReportService

        now = datetime.now(_CST)
        months = [
            now.strftime("%Y-%m"),
            (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m"),
        ]
        service = MonthlyReportService(self.auth, self.db)
        for month in months:
            try:
                await service.sync_month(month=month, user_ids=oa_user_ids)
            except Exception as e:  # noqa: BLE001 - 考勤缺失不阻断离职
                logger.warning(f"离职前考勤预同步失败: month={month}, users={oa_user_ids}: {e!s}")
