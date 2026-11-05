import calendar
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from redis.asyncio.client import Redis
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import settings
from app.core.ap_scheduler import SchedulerUtil
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.modules.system.user.model import UserModel
from app.plugin.module_internal.account.crud import DeptMappingCRUD, ExternalAccountBindingCRUD
from app.plugin.module_internal.account.model import ExternalAccountBindingModel
from app.plugin.module_internal.account.schema import SYSTEM_DINGTALK
from app.plugin.infra.integration.dingtalk_client import DingtalkClient

from ..utils import audit_load_options, fetch_dept_names, fetch_user_profiles
from .crud import DailyReportCRUD, MonthlyReportCRUD, RestDayCRUD
from .model import DailyReportModel, MonthlyReportModel, RestDayModel
from .schema import (
    AttendanceGroupOptionSchema,
    DailyReportOutSchema,
    MonthlyReportOutSchema,
    MonthlyReportQueryParam,
    MonthlyReportSyncResultSchema,
    MonthlyReportSyncStateSchema,
    RestDayCreateResultSchema,
    RestDayCreateSchema,
    RestDayOutSchema,
)

# 智能考勤报表的统计列。列 ID 每个企业可能不同，先用列定义接口按名称定位，
# 定位不到再退回这里的兜底 ID（取自已跑通的参考实现）。
STAT_COLUMNS: list[tuple[str, str]] = [
    ("应出勤天数", "8150408"),
    ("出勤天数", "8150410"),
    ("休息天数", "8150411"),
    ("迟到次数", "8150413"),
    ("严重迟到次数", "8150415"),
    ("旷工迟到天数", "8150417"),
    ("早退次数", "8150418"),
    ("上班缺卡次数", "8150420"),
    ("下班缺卡次数", "8150421"),
    ("旷工天数", "8150422"),
]

STAT_FIELD_MAP: dict[str, str] = {
    "应出勤天数": "required_days",
    "出勤天数": "actual_days",
    "休息天数": "rest_days",
    "迟到次数": "late_count",
    "严重迟到次数": "serious_late_count",
    "旷工迟到天数": "absent_late_days",
    "早退次数": "early_leave_count",
    "上班缺卡次数": "missing_checkin_count",
    "下班缺卡次数": "missing_checkout_count",
    "旷工天数": "absent_days",
}

DAY_APPROVE_KEY = "关联的审批单"
DAY_RESULT_KEY = "考勤结果"

# getcolumnval 一次最多 20 列
MAX_COLUMN_PER_REQUEST = 20

_LEAVE_RE = re.compile(r"(事假|病假|年假).*?([\d.]+)\s*天")
_LEAVE_FIELD_MAP: dict[str, str] = {"事假": "leave_personal", "病假": "leave_sick", "年假": "leave_annual"}

# 公司休息日的明细展示文案（ADR-0009）。含「休息」二字，前端配色规则据此归到绿色。
COMPANY_REST_TEXT = "公司休息"

# 月报里参与「钉钉基准快照 / 休息日重算」的统计列（10 个整数列 + 3 个请假列）。
# dingtalk_raw 快照和重算都只动这几列；name/dept 等快照列不参与。
STAT_SNAPSHOT_FIELDS: list[str] = [
    "required_days",
    "actual_days",
    "rest_days",
    "late_count",
    "serious_late_count",
    "absent_late_days",
    "early_leave_count",
    "missing_checkin_count",
    "missing_checkout_count",
    "absent_days",
    "leave_personal",
    "leave_sick",
    "leave_annual",
]


def _sum_values(values: dict[str, Any]) -> int | float:
    """把按天返回的列值累加成月度合计；空值与非数字跳过。"""
    total = 0.0
    for value in values.values():
        try:
            total += float(value)
        except (TypeError, ValueError):
            continue
    return int(total) if total == int(total) else total


def _aggregate_leaves(approve_texts: list[Any]) -> dict[str, float]:
    """从审批单原文聚合一整月的请假天数（事假/病假/年假）。

    同一条审批单可能跨多天、在每天的审批列里重复出现，按 (类型, 原文) 去重只算一次。
    重算休息日时也用它 —— 只聚合「非休息日」的审批单，休息那天的请假随之不计（ADR-0009 A5）。
    """
    leaves = {"事假": 0.0, "病假": 0.0, "年假": 0.0}
    counted: set[tuple[str, str]] = set()
    for text in approve_texts:
        content = str(text or "").strip()
        if not content:
            continue
        for kind, amount in _LEAVE_RE.findall(content):
            signature = (kind, content)
            if signature in counted:
                continue
            counted.add(signature)
            try:
                leaves[kind] = leaves.get(kind, 0.0) + float(amount)
            except ValueError:
                continue
    return leaves


def _categorize_raw(raw_text: str | None) -> str:
    """把钉钉考勤结果原文归类，是公司休息日重算规则表（ADR-0009 第三节）的输入。

    返回: rest / normal / field / absent / leave / late / early / missing / other / empty
    匹配有先后顺序：先判复合词（如「旷工迟到」归 late 而不是 absent），再判单词。
    """
    text = (raw_text or "").strip()
    if not text:
        return "empty"
    if "休息" in text:
        return "rest"
    if "假" in text:  # 事假 / 病假 / 年假
        return "leave"
    if "缺卡" in text:
        return "missing"
    if "早退" in text:
        return "early"
    if "迟到" in text:  # 含 严重迟到 / 旷工迟到
        return "late"
    if "旷工" in text:
        return "absent"
    if "外勤" in text:
        return "field"
    if "正常" in text:
        return "normal"
    return "other"


def _derive_actual_days(stats: dict[str, Any], worked_rest_days: int = 0) -> float | None:
    """出勤天数（ADR-0010，A 方案）：应出勤 − 旷工 − 请假天数 + 休息日里干净上班的天数。

    请假（病假/事假/年假）不算出勤，但半天假里「工作了的那半」算 —— 所以减的是
    请假**天数**（浮点），不是请假的日历日数。这与钉钉「出勤天数」口径不同
    （钉钉把带薪病假也算出勤），因此不直接用钉钉原值，而是按这个公式推导。

    参数:
    - stats (dict): 含 required_days / absent_days / leave_* 的统计值（基准或重算中间态）
    - worked_rest_days (int): 公司休息日里钉钉判「正常/外勤」（干净上班）的天数（A4 照算）

    返回:
    - float | None: 出勤天数；required/absent 任一缺失时退回钉钉原值 actual_days
    """
    required = stats.get("required_days")
    absent = stats.get("absent_days")
    if required is None or absent is None:
        return stats.get("actual_days")
    leave_total = sum(float(stats.get(f) or 0) for f in ("leave_personal", "leave_sick", "leave_annual"))
    return required - absent - leave_total + worked_rest_days


class MonthlyReportService:
    """钉钉考勤月报服务

    结果完全来自钉钉智能考勤报表；本系统只做保存、查询和覆盖更新，不重新计算考勤规则。
    """

    def __init__(self, auth: AuthSchema, db: AsyncSession, redis: Redis | None = None) -> None:
        self.auth = auth
        self.db = db
        self.redis = redis

    @property
    def can_view_all(self) -> bool:
        """管理侧能看全员；只有 query 权限的普通员工强制限定为本人。"""
        manager_permissions = {
            "module_internal:salary-attendance:manage",
            "module_internal:salary-attendance:sync",
            "module_internal:salary-attendance:rest",
            "module_internal:salary-performance:manage",
        }
        return bool(self.auth.user.is_superuser or manager_permissions.intersection(self.auth.permissions or []))

    def _assert_user_access(self, oa_user_id: int) -> None:
        if not self.can_view_all and oa_user_id != self.auth.user.id:
            raise CustomException(msg="只能查看自己的考勤信息")

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

    @staticmethod
    def _month_range(month: str) -> tuple[date, date]:
        year, mon = (int(part) for part in month.split("-"))
        first = date(year, mon, 1)
        last = date(year, mon, calendar.monthrange(year, mon)[1])
        return first, last

    @classmethod
    def _previous_month(cls, today: date | None = None) -> str:
        """返回「上个月」的 YYYY-MM"""
        today = today or date.today()
        first_day_this_month = today.replace(day=1)
        return (first_day_this_month - timedelta(days=1)).strftime("%Y-%m")

    # ── 查询 ──────────────────────────────────────────────────────
    def _build_conditions(self, search: MonthlyReportQueryParam | None) -> list[Any]:
        # 没有考勤组的人不参与考勤统计：他们的月报统计列全 0，显示出来只会误导。
        # 这一层过滤与同步时「跳过无考勤组的人」是双保险 —— 万一历史脏数据没清干净，
        # 页面也不会把它们露出来（ADR-0008）。
        conditions: list[Any] = [
            MonthlyReportModel.is_deleted == False,  # noqa: E712
            MonthlyReportModel.group_id.isnot(None),
        ]
        if not self.can_view_all:
            conditions.append(MonthlyReportModel.oa_user_id == self.auth.user.id)
        if not search:
            return conditions
        if search.month:
            conditions.append(MonthlyReportModel.month == search.month)
        if search.oa_user_id:
            conditions.append(MonthlyReportModel.oa_user_id == search.oa_user_id)
        if search.name:
            keyword = f"%{search.name}%"
            conditions.append(or_(MonthlyReportModel.name.like(keyword), UserModel.name.like(keyword)))
        if search.dept_id:
            conditions.append(UserModel.dept_id == search.dept_id)
        return conditions

    async def _enrich(self, items: list[MonthlyReportOutSchema], objs: list[MonthlyReportModel]) -> None:
        """补齐用户名与部门名。

        走 fetch_user_profiles 成批查一次，不要访问 `user.dept`——那是没配
        加载策略的 relationship，在 async 会话里会触发同步 IO 抛 MissingGreenlet。
        """
        profiles = await fetch_user_profiles(self.db, [obj.oa_user_id for obj in objs])
        for item, obj in zip(items, objs, strict=True):
            profile = profiles.get(obj.oa_user_id) or {}
            item.user_name = profile.get("name")
            item.current_dept_name = profile.get("dept_name") or obj.dept_name

    async def page(
        self,
        page_no: int = 1,
        page_size: int = 20,
        search: MonthlyReportQueryParam | None = None,
    ) -> PageResultSchema[MonthlyReportOutSchema]:
        conditions = self._build_conditions(search)
        join_cond = UserModel.id == MonthlyReportModel.oa_user_id
        total = (
            await self.db.execute(
                select(func.count(MonthlyReportModel.id)).select_from(MonthlyReportModel).join(UserModel, join_cond).where(*conditions)
            )
        ).scalar() or 0

        offset = (page_no - 1) * page_size
        rows = (
            await self.db.execute(
                select(MonthlyReportModel)
                .join(UserModel, join_cond)
                .options(*audit_load_options(MonthlyReportModel))
                .where(*conditions)
                .order_by(MonthlyReportModel.month.desc(), MonthlyReportModel.oa_user_id.asc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
        items = [MonthlyReportOutSchema.model_validate(obj) for obj in rows]
        await self._enrich(items, list(rows))
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + page_size < total,
            items=items,
        )

    async def get_list(self, search: MonthlyReportQueryParam | None = None) -> list[MonthlyReportOutSchema]:
        conditions = self._build_conditions(search)
        rows = (
            await self.db.execute(
                select(MonthlyReportModel)
                .join(UserModel, UserModel.id == MonthlyReportModel.oa_user_id)
                .options(*audit_load_options(MonthlyReportModel))
                .where(*conditions)
                .order_by(MonthlyReportModel.month.desc(), MonthlyReportModel.oa_user_id.asc())
            )
        ).scalars().all()
        items = [MonthlyReportOutSchema.model_validate(obj) for obj in rows]
        await self._enrich(items, list(rows))
        return items

    async def detail(self, id: int) -> MonthlyReportOutSchema:
        conditions = [
            MonthlyReportModel.id == id,
            MonthlyReportModel.is_deleted == False,  # noqa: E712
            MonthlyReportModel.group_id.isnot(None),
        ]
        if not self.can_view_all:
            conditions.append(MonthlyReportModel.oa_user_id == self.auth.user.id)
        obj = (
            await self.db.execute(
                select(MonthlyReportModel).options(*audit_load_options(MonthlyReportModel)).where(*conditions)
            )
        ).scalars().first()
        if not obj:
            raise CustomException(msg="该考勤月报不存在或无权查看")
        item = MonthlyReportOutSchema.model_validate(obj)
        await self._enrich([item], [obj])
        return item

    # ── 同步 ──────────────────────────────────────────────────────
    async def sync_month(self, month: str | None = None, user_ids: list[int] | None = None) -> MonthlyReportSyncResultSchema:
        """同步指定月份（默认上个月）的钉钉智能考勤月报"""
        self._require_configured()
        month = month or self._previous_month()
        first, last = self._month_range(month)
        result = MonthlyReportSyncResultSchema(
            month=month,
            start_date=first.strftime("%Y-%m-%d"),
            end_date=last.strftime("%Y-%m-%d"),
        )

        bindings = await ExternalAccountBindingCRUD(self.auth, self.db).list_by_system(SYSTEM_DINGTALK)
        targets = [b for b in bindings if b.oa_user_id]
        if user_ids:
            allow = set(user_ids)
            targets = [b for b in targets if b.oa_user_id in allow]
        result.total = len(targets)
        if not targets:
            result.messages.append("没有已绑定钉钉的OA用户，无需同步")
            return result

        client = self._client()
        columns = await client.get_attendance_columns()
        column_by_name: dict[str, str] = {}
        for col in columns:
            name = str(col.get("name") or "").strip()
            col_id = col.get("id")
            if name and col_id:
                column_by_name.setdefault(name, str(col_id))

        stat_ids: list[tuple[str, str]] = []
        for label, fallback in STAT_COLUMNS:
            col_id = column_by_name.get(label, fallback)
            if col_id:
                stat_ids.append((label, str(col_id)))
        day_approve_id = column_by_name.get(DAY_APPROVE_KEY)
        day_result_id = column_by_name.get(DAY_RESULT_KEY)

        all_ids = [cid for _, cid in stat_ids]
        if day_approve_id:
            all_ids.append(str(day_approve_id))
        if day_result_id:
            all_ids.append(str(day_result_id))
        chunks = [all_ids[i : i + MAX_COLUMN_PER_REQUEST] for i in range(0, len(all_ids), MAX_COLUMN_PER_REQUEST)]

        for binding in targets:
            user_name = binding.user.name if binding.user else str(binding.oa_user_id)

            # 考勤组判定必须在拉数据之前：不在考勤组的人钉钉根本不会给他考勤数据，
            # 不提前挡住就会写出一整行全 0 的空壳月报（ADR-0008）。
            # get_user_group 抛异常时这里刻意不接 —— 接口层面失败时「有没有考勤组」是未知的，
            # 必须让异常冒泡出去由外层事务把整批作废，绝不能当成"没有考勤组"放过去。
            group = await client.get_user_group(userid=binding.external_user_id)
            if not group.get("name"):
                result.skipped += 1
                purged = await self._purge_ungrouped_rows(oa_user_id=binding.oa_user_id)
                if purged:
                    result.messages.append(f"{user_name}({binding.oa_user_id}): 无考勤组，清理空壳 {purged} 行")
                continue

            values_by_column = await self._fetch_user_column_values(
                client=client,
                userid=binding.external_user_id,
                chunks=chunks,
                start=first,
                end=last,
            )
            # 钉钉侧快照（工号 / 考勤组 / 部门名）每次同步顺带刷新。
            # 考勤组上面已经取过了，直接传进去，不重复打同一个接口。
            snapshot = await self._refresh_binding_snapshot(client=client, binding=binding, group=group)
            payload = self._build_report_payload(
                oa_user_id=binding.oa_user_id,
                user_name=user_name,
                dept_name=snapshot["dept_name"],
                job_number=snapshot["job_number"],
                group_id=snapshot["group_id"],
                group_name=snapshot["group_name"],
                month=month,
                stat_ids=stat_ids,
                day_approve_id=day_approve_id,
                values_by_column=values_by_column,
            )
            # 钉钉原始月值快照：这是公司休息日重算的基准，必须存钉钉原值，
            # 不能存重算后的值。所以快照在重算之前、用刚算出来的钉钉值写入（ADR-0009）。
            payload["dingtalk_raw"] = {f: payload.get(f) for f in STAT_SNAPSHOT_FIELDS}
            # 展示用的出勤天数改用新口径（ADR-0010）：应出勤 − 旷工 − 请假天数，
            # 不用钉钉原值（钉钉把带薪病假也算出勤）。钉钉原值已留在上面的快照里可对账。
            payload["actual_days"] = _derive_actual_days(payload)
            await self._save_report(oa_user_id=binding.oa_user_id, month=month, payload=payload)
            await self._save_daily_rows(
                oa_user_id=binding.oa_user_id,
                month=month,
                start=first,
                end=last,
                day_result_id=day_result_id,
                day_approve_id=day_approve_id,
                group_id=snapshot["group_id"],
                group_name=snapshot["group_name"],
                values_by_column=values_by_column,
            )
            result.success += 1

        # 当月若已存在公司休息日，落库后立即叠加规则重算（A2：先同步后登记、先登记后同步，结果一致）。
        if await RestDayCRUD(self.auth, self.db).month_has_rest_day(month):
            recalced = await self.recalc_month(month)
            logger.info(f"钉钉考勤月报同步后叠加公司休息日重算: month={month}, 重算 {recalced} 人")

        # 整批要么全成、要么全废：这里刻意不吞单人异常，失败由调用方的事务回滚兜住。
        # 所以「库里存在某个月的数据」就等价于「那个月拉全了」（ADR-0008）。
        logger.info(f"钉钉考勤月报同步完成: month={month}, 成功{result.success} 无考勤组跳过{result.skipped}")
        return result

    async def sync_previous_month(self, user_ids: list[int] | None = None) -> MonthlyReportSyncResultSchema:
        """系统定时任务入口：每月 7 号同步上个月。

        与手动拉取互斥：上个月**已经有数据**就说明这个月被拉过了（无论自动还是手动拉的），
        直接跳过。自动任务不回头补历史月份，对同一个月重复拉没有意义（ADR-0008）。

        参数:
        - user_ids (list[int] | None): 限定同步的 OA 用户；为空表示全部已绑定钉钉的用户

        返回:
        - MonthlyReportSyncResultSchema: 同步结果，跳过时 success 为 0 且 messages 里有说明
        """
        month = self._previous_month()
        if await self.month_synced(month):
            logger.info(f"钉钉考勤月报同步跳过: month={month} 已有数据（自动/手动拉取互斥）")
            result = MonthlyReportSyncResultSchema(month=month)
            result.messages.append(f"{month} 已同步过，本次自动同步跳过")
            return result
        return await self.sync_month(month=month, user_ids=user_ids)

    async def month_synced(self, month: str) -> bool:
        """某个月的考勤数据是否已经拉过。

        「拉过」= 库里存在该月未软删的月报行。因为同步是整批原子提交的，
        有数据就说明那次同步全部成功，不存在「拉了一半」的中间状态（ADR-0008）。

        参数:
        - month (str): 月份(YYYY-MM)

        返回:
        - bool: 已拉过为 True
        """
        count = (
            await self.db.execute(
                select(func.count(MonthlyReportModel.id)).where(
                    MonthlyReportModel.month == month,
                    MonthlyReportModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar() or 0
        return count > 0

    async def has_group_member(self) -> bool:
        """是否存在已绑定钉钉的用户，用于决定手动拉取按钮是否显示。

        **不要再用 `group_id IS NOT NULL` 判定**：该字段只在同步成功时由
        `_refresh_binding_snapshot` 回写，全新环境下永远是 NULL，会造成
        「没同步过 → 按钮不显示 → 永远无法首次同步」的死锁（ADR-0008 第八节）。

        考勤组的真正判定在 `sync_month` 里逐个调 `get_user_group` 完成，
        没有考勤组的人会被跳过，不产生空壳月报，无需在按钮这一层再挡一次。

        返回:
        - bool: 至少有一个已绑定钉钉的用户为 True
        """
        count = (
            await self.db.execute(
                select(func.count(ExternalAccountBindingModel.id)).where(
                    ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
                    ExternalAccountBindingModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar() or 0
        return count > 0

    async def previous_month_sync_state(self) -> MonthlyReportSyncStateSchema:
        """上个月的同步状态，供考勤统计页面决定要不要显示手动拉取按钮。

        返回:
        - MonthlyReportSyncStateSchema: {month, synced, can_sync}
        """
        month = self._previous_month()
        synced = await self.month_synced(month)
        return MonthlyReportSyncStateSchema(
            month=month,
            synced=synced,
            # 自动任务在每月 3 日 00:00 先跑；到同一时点仍未拉取成功，才允许手动补拉。
            can_sync=date.today().day >= 3 and await self.has_group_member() and not synced,
        )

    async def _purge_ungrouped_rows(self, *, oa_user_id: int) -> int:
        """物理删掉某用户在「没有考勤组」时期留下的空壳考勤数据。

        这些月报统计列全 0、列表查询也按 group_id 过滤掉了，留在表里纯属垃圾。
        日明细跟着一起删：月报都没了，明细留着就是孤儿，查看明细弹窗还能把它们捞出来。

        用物理删而不是软删：这些行本来就不该存在，没有保留价值（ADR-0008 用户拍板）。

        参数:
        - oa_user_id (int): OA 用户 ID

        返回:
        - int: 删掉的月报行数
        """
        rows = (
            await self.db.execute(
                select(MonthlyReportModel.id, MonthlyReportModel.month).where(
                    MonthlyReportModel.oa_user_id == oa_user_id,
                    MonthlyReportModel.group_id.is_(None),
                )
            )
        ).all()
        if not rows:
            return 0

        months = {row[1] for row in rows}
        await self.db.execute(
            delete(DailyReportModel).where(
                DailyReportModel.oa_user_id == oa_user_id,
                DailyReportModel.month.in_(months),
            )
        )
        await self.db.execute(delete(MonthlyReportModel).where(MonthlyReportModel.id.in_([row[0] for row in rows])))
        await self.db.flush()
        logger.info(f"清理无考勤组空壳数据: user={oa_user_id}, 月报 {len(rows)} 行, 月份={sorted(months)}")
        return len(rows)

    async def _fetch_user_column_values(
        self,
        *,
        client: DingtalkClient,
        userid: str,
        chunks: list[list[str]],
        start: date,
        end: date,
    ) -> dict[str, dict[str, Any]]:
        """拉取单人的报表列值，按列 ID 合并成 {列ID: {日期: 值}}"""
        values_by_column: dict[str, dict[str, Any]] = {}
        for chunk in chunks:
            if not chunk:
                continue
            data = await client.get_attendance_column_values(
                userid=userid,
                column_id_list=chunk,
                from_date=f"{start.strftime('%Y-%m-%d')} 00:00:00",
                to_date=f"{end.strftime('%Y-%m-%d')} 23:59:59",
            )
            for cv in data:
                col_id = str((cv.get("column_vo") or {}).get("id"))
                day_values = values_by_column.setdefault(col_id, {})
                for item in cv.get("column_vals") or []:
                    day = str(item.get("date") or "")[:10]
                    day_values[day] = item.get("value")
        return values_by_column

    @staticmethod
    def _build_report_payload(  # noqa: PLR0913
        *,
        oa_user_id: int,
        user_name: str | None,
        dept_name: str | None,
        job_number: str | None,
        group_id: str | None,
        group_name: str | None,
        month: str,
        stat_ids: list[tuple[str, str]],
        day_approve_id: str | None,
        values_by_column: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "oa_user_id": oa_user_id,
            "month": month,
            "name": user_name,
            "dept_name": dept_name,
            "job_number": job_number,
            "group_id": group_id,
            "group_name": group_name,
            "synced_at": datetime.now(UTC),
            "status": 0,
        }

        for label, col_id in stat_ids:
            field = STAT_FIELD_MAP.get(label)
            if not field:
                continue
            if col_id in values_by_column:
                payload[field] = _sum_values(values_by_column[col_id])
            else:
                payload[field] = None

        approve_texts = list(values_by_column[day_approve_id].values()) if (day_approve_id and day_approve_id in values_by_column) else []
        leaves = _aggregate_leaves(approve_texts)
        for kind, field in _LEAVE_FIELD_MAP.items():
            payload[field] = leaves.get(kind, 0.0)
        return payload

    async def _save_report(self, *, oa_user_id: int, month: str, payload: dict[str, Any]) -> None:
        crud = MonthlyReportCRUD(self.auth, self.db)
        exists = await crud.get_by_user_month(oa_user_id=oa_user_id, month=month)
        if exists:
            await crud.update(id=exists.id, data=payload)
        else:
            await crud.create(data=payload)

    # ── 日明细 ────────────────────────────────────────────────────
    async def daily_list(self, *, oa_user_id: int, month: str) -> list[DailyReportOutSchema]:
        """取某员工某月的日明细（报表「查看明细」弹窗用），按日期升序"""
        self._assert_user_access(oa_user_id)
        rows = await DailyReportCRUD(self.auth, self.db).list_by_user_month(oa_user_id=oa_user_id, month=month)
        return [DailyReportOutSchema.model_validate(obj) for obj in rows]

    async def _refresh_binding_snapshot(
        self,
        *,
        client: DingtalkClient,
        binding: ExternalAccountBindingModel,
        group: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """刷新绑定表上的钉钉侧快照：工号、考勤组、部门名。

        工号、部门名取不到时保留旧值、不抛异常——它们只是报表前缀列，
        不值得因为它让整行考勤数据同步失败。

        **考勤组是唯一的例外**，它决定这个人要不要参与考勤统计，所以
        「接口失败」与「确实没有考勤组」必须分开处理（ADR-0008）：
        - 接口失败 → `get_user_group` 抛异常，这里**刻意不接**，让异常冒泡到
          `sync_month` 把整批作废。「有没有考勤组」未知时不能当成没有。
        - 接口通了但返回空 → 确实不在任何考勤组 → **清空**快照。
          保留旧值的话，人被移出考勤组后这条记录永远判不出来。

        参数:
        - client (DingtalkClient): 钉钉客户端
        - binding (ExternalAccountBindingModel): 钉钉绑定记录
        - group (dict[str, Any] | None): 调用方已取到的考勤组，传进来可省一次请求

        返回:
        - dict[str, Any]: {"job_number": ..., "group_id": ..., "group_name": ..., "dept_name": ...}
        """
        snapshot: dict[str, Any] = {
            "job_number": binding.job_number,
            "group_id": binding.group_id,
            "group_name": binding.group_name,
            "dept_name": binding.dingtalk_dept_name,
        }

        # 工号：钉钉通讯录 job_number
        try:
            detail = await client.get_user_detail(userid=binding.external_user_id)
            if detail:
                snapshot["job_number"] = str(detail.get("job_number") or "") or None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"钉钉工号获取失败, userid={binding.external_user_id}: {e!s}")

        # 考勤组：一个员工只属于一个考勤组。
        # 这里刻意不 try —— 接口失败要冒泡出去让整批作废，只有"接口通了但为空"才清空快照。
        # 详见方法 docstring 里那两条。
        if group is None:
            group = await client.get_user_group(userid=binding.external_user_id)
        snapshot["group_id"] = group.get("group_id") or None
        snapshot["group_name"] = group.get("name") or None

        # 部门名：取钉钉部门名（与 OA 部门名通过「部门映射」保持一致）；
        # 没配映射时退回 OA 部门名，避免报表这一列空着
        dept_name = binding.dingtalk_dept_name
        oa_dept_id = binding.user.dept_id if binding.user else None
        if oa_dept_id:
            mapping = await DeptMappingCRUD(self.auth, self.db).get_by_oa_dept(oa_dept_id)
            if mapping and mapping.dingtalk_dept_name:
                dept_name = mapping.dingtalk_dept_name
        if not dept_name and oa_dept_id:
            dept_name = (await fetch_dept_names(self.db, [oa_dept_id])).get(oa_dept_id)
        snapshot["dept_name"] = dept_name

        await ExternalAccountBindingCRUD(self.auth, self.db).update(
            id=binding.id,
            data={
                "job_number": snapshot["job_number"],
                "group_id": snapshot["group_id"],
                "group_name": snapshot["group_name"],
                "dingtalk_dept_name": snapshot["dept_name"],
            },
        )
        return snapshot

    async def _save_daily_rows(  # noqa: PLR0913
        self,
        *,
        oa_user_id: int,
        month: str,
        start: date,
        end: date,
        day_result_id: str | None,
        day_approve_id: str | None,
        group_id: str | None,
        group_name: str | None,
        values_by_column: dict[str, dict[str, Any]],
    ) -> None:
        """把「考勤结果」「关联的审批单」两列的日粒度值写成日明细行。

        只写钉钉有内容的日期；重复同步按 (用户, 日期) 覆盖，保证幂等。
        """
        if not day_result_id and not day_approve_id:
            return

        results = values_by_column.get(str(day_result_id)) if day_result_id else {}
        approves = values_by_column.get(str(day_approve_id)) if day_approve_id else {}
        crud = DailyReportCRUD(self.auth, self.db)
        synced_at = datetime.now(UTC)

        day = start
        while day <= end:
            key = day.strftime("%Y-%m-%d")
            raw_text = str((results or {}).get(key) or "").strip() or None
            approve_text = str((approves or {}).get(key) or "").strip() or None
            if raw_text or approve_text:
                payload = {
                    "oa_user_id": oa_user_id,
                    "work_date": day,
                    "month": month,
                    "day_of_month": day.day,
                    "group_id": group_id,
                    "group_name": group_name,
                    "raw_text": raw_text,
                    "approve_text": approve_text,
                    # 展示值默认 = 钉钉原文；若当月有公司休息日，sync 末尾的 recalc 会把它改成「公司休息」
                    "effective_text": raw_text,
                    "synced_at": synced_at,
                    "status": 0,
                }
                exists = await crud.get_by_user_date(oa_user_id=oa_user_id, work_date=day)
                if exists:
                    await crud.update(id=exists.id, data=payload)
                else:
                    await crud.create(data=payload)
            day += timedelta(days=1)

    # ── 公司休息日（ADR-0009）────────────────────────────────
    async def recalc_month(self, month: str) -> int:
        """以钉钉基准快照 + 当月公司休息日，重算该月所有月报与日明细展示值。幂等。

        基准是 dingtalk_raw（同步时写入的钉钉原值），重算 = 基准 + 休息日调整，
        所以增删休息日可以反复跑而不漂移，删除也能精确还原。

        返回:
        - int: 重算了多少人的月报
        """
        rest_days = await RestDayCRUD(self.auth, self.db).list_rest_days(month=month)
        rest_by_group: dict[str, set[date]] = {}
        for rd in rest_days:
            rd_date = rd.rest_date.date() if isinstance(rd.rest_date, datetime) else rd.rest_date
            rest_by_group.setdefault(rd.group_id, set()).add(rd_date)

        reports = await MonthlyReportCRUD(self.auth, self.db).list_by_month(month)
        for report in reports:
            await self._recalc_one_report(report, rest_by_group.get(report.group_id, set()))
        if reports:
            await self.db.flush()
        return len(reports)

    async def _recalc_one_report(self, report: MonthlyReportModel, rest_dates: set[date]) -> None:
        """重算单个员工的月报统计列 + 该月日明细的展示值"""
        # 基准：dingtalk_raw 为 NULL 的历史行先把当前列值当基准回填
        # （本功能上线前同步的行还没有休息日，那时列值就是钉钉原值）。
        baseline = report.dingtalk_raw
        if not baseline:
            baseline = {f: getattr(report, f) for f in STAT_SNAPSHOT_FIELDS}
            report.dingtalk_raw = dict(baseline)

        stats = dict(baseline)
        daily_rows = await DailyReportCRUD(self.auth, self.db).list_for_recalc(oa_user_id=report.oa_user_id, month=report.month)
        daily_by_date = {row.work_date: row for row in daily_rows}

        # 先全部还原成钉钉原文，再对休息日覆盖成「公司休息」—— 这样删除休息日也能正确还原
        for row in daily_rows:
            row.effective_text = row.raw_text

        worked_rest_days = 0
        for d in sorted(rest_dates):
            row = daily_by_date.get(d)
            if not row:
                logger.warning(f"公司休息日重算: user={report.oa_user_id} 在 {d} 没有日明细，跳过这天")
                continue
            category = _categorize_raw(row.raw_text)
            self._apply_rest_day(stats, category, row.raw_text)
            if category in ("normal", "field"):
                worked_rest_days += 1  # A4：休息日里干净上班的，出勤照算
            if category != "rest":
                row.effective_text = COMPANY_REST_TEXT

        # 请假单独重算：只聚合非休息日的审批单（休息那天的请假不计，A5）
        leaves = _aggregate_leaves([r.approve_text for r in daily_rows if r.work_date not in rest_dates])
        for kind, field in _LEAVE_FIELD_MAP.items():
            stats[field] = leaves.get(kind, 0.0)

        # 出勤天数按新口径统一推导（ADR-0010）：应出勤 − 旷工 − 请假 + 休息日里干净上班天数
        stats["actual_days"] = _derive_actual_days(stats, worked_rest_days)

        for field in STAT_SNAPSHOT_FIELDS:
            if field in stats:
                setattr(report, field, stats[field])

    @staticmethod
    def _apply_rest_day(stats: dict[str, Any], category: str, raw_text: str | None) -> None:
        """把一个休息日按钉钉原文类别折算进月报统计列（规则表见 ADR-0009 第三节）。

        只动 应出勤 / 休息 / 旷工 / 异常计数 / 请假；**出勤天数不在这里动** ——
        它由 `_derive_actual_days` 按「应出勤 − 旷工 − 请假 + 休息日里干净上班天数」统一推导（ADR-0010）。
        应出勤对全组统一减（A4）；「正常 / 外勤」算上班（不加休息）；
        「旷工 / 请假 / 迟到 / 早退 / 缺卡」算休息（清异常 / 请假）。
        """
        if category in ("rest", "empty", "other"):
            return  # 本就是钉钉休息日 / 无数据 / 不认识：不动
        stats["required_days"] = (stats.get("required_days") or 0) - 1
        if category in ("normal", "field"):
            return  # A4：算上班，不加休息（出勤由 worked_rest_days 在推导时加回）
        # 以下都「算休息」
        stats["rest_days"] = (stats.get("rest_days") or 0) + 1
        if category == "absent":
            stats["absent_days"] = (stats.get("absent_days") or 0) - 1
            return
        # 病假 / 迟到 / 早退 / 缺卡：清对应异常计数（请假量由外层按非休息日重新聚合）
        text = raw_text or ""
        if category == "late":
            if "严重" in text:
                stats["serious_late_count"] = (stats.get("serious_late_count") or 0) - 1
            elif "旷工" in text:
                stats["absent_late_days"] = (stats.get("absent_late_days") or 0) - 1
            else:
                stats["late_count"] = (stats.get("late_count") or 0) - 1
        elif category == "early":
            stats["early_leave_count"] = (stats.get("early_leave_count") or 0) - 1
        elif category == "missing":
            if "上班" in text:
                stats["missing_checkin_count"] = (stats.get("missing_checkin_count") or 0) - 1
            else:
                stats["missing_checkout_count"] = (stats.get("missing_checkout_count") or 0) - 1

    # ── 休息日管理接口 ────────────────────────────────────────
    async def list_attendance_groups(self) -> list[AttendanceGroupOptionSchema]:
        """考勤组下拉选项：绑定表里有考勤组的钉钉绑定记录去重"""
        rows = (
            await self.db.execute(
                select(ExternalAccountBindingModel.group_id, ExternalAccountBindingModel.group_name)
                .where(
                    ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
                    ExternalAccountBindingModel.is_deleted == False,  # noqa: E712
                    ExternalAccountBindingModel.group_id.isnot(None),
                )
                .distinct()
            )
        ).all()
        return [AttendanceGroupOptionSchema(group_id=row[0], group_name=row[1]) for row in rows]

    async def list_rest_days(self, *, month: str | None = None, group_id: str | None = None) -> list[RestDayOutSchema]:
        """按月 / 考勤组查休息日列表"""
        rows = await RestDayCRUD(self.auth, self.db).list_rest_days(month=month, group_id=group_id)
        return [RestDayOutSchema.model_validate(obj) for obj in rows]

    async def create_rest_days(self, data: RestDayCreateSchema) -> RestDayCreateResultSchema:
        """批量登记公司休息日，随后立即重算受影响月份（A2 立即生效）"""
        group_name = await self._resolve_group_name(data.group_id)
        crud = RestDayCRUD(self.auth, self.db)
        created = 0
        skipped: list[str] = []
        months: set[str] = set()
        for d in data.dates:
            if await crud.get_by_group_date(group_id=data.group_id, rest_date=d):
                skipped.append(d.strftime("%Y-%m-%d"))
                continue
            await crud.create(
                data={
                    "group_id": data.group_id,
                    "group_name": group_name,
                    "rest_date": d,
                    "month": d.strftime("%Y-%m"),
                    "remark": data.remark,
                    "status": 0,
                }
            )
            created += 1
            months.add(d.strftime("%Y-%m"))
        for m in sorted(months):
            await self.recalc_month(m)
        return RestDayCreateResultSchema(created=created, skipped_dates=skipped, months=sorted(months))

    async def delete_rest_days(self, ids: list[int]) -> dict[str, Any]:
        """删除休息日，随后立即反向重算受影响月份（还原回钉钉原值）"""
        removed = await RestDayCRUD(self.auth, self.db).physical_delete(ids)
        months = sorted({r.month for r in removed})
        for m in months:
            await self.recalc_month(m)
        return {"deleted": len(removed), "months": months}

    async def _resolve_group_name(self, group_id: str) -> str | None:
        """按考勤组 ID 查名称（以绑定表为准，不信任前端传入）"""
        row = (
            await self.db.execute(
                select(ExternalAccountBindingModel.group_name)
                .where(
                    ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
                    ExternalAccountBindingModel.is_deleted == False,  # noqa: E712
                    ExternalAccountBindingModel.group_id == group_id,
                )
                .limit(1)
            )
        ).first()
        return row[0] if row else None
