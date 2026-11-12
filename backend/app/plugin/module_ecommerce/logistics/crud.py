from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import BillParseRuleModel, CourierBillModel
from .schema import BillParseRuleCreateSchema, BillParseRuleUpdateSchema


class BillParseRuleCRUD(CRUDBase[BillParseRuleModel, BillParseRuleCreateSchema, BillParseRuleUpdateSchema]):
    """账单解析规则 CRUD"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=BillParseRuleModel, auth=auth, db=db)

    async def active_bill_types(self) -> list[str]:
        """启用中的账单类型编码——「这个月该有几家账单」的事实来源（账单门禁与空批次声明都读它）。"""
        rules: Sequence[BillParseRuleModel] = await self.get_list(search={"status": 0})
        return [r.bill_type for r in rules]


class CourierBillCRUD(CRUDBase[CourierBillModel, dict, dict]):
    """快递账单原始行 CRUD（创建走导入批处理内部字典，不走标准 Create/UpdateSchema）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=CourierBillModel, auth=auth, db=db)

    async def bulk_create(self, rows: Sequence[dict]) -> None:
        """批量插入账单行。

        月账单约 3000 行，逐条 `create()` 每行要付 flush + refresh + 回读审计人共 3 条语句，
        导入会被拖成几分钟；这里只 add_all + 一次 flush，审计人按 CRUDBase.create 同样口径填。
        """
        user_id = self.auth.user.id
        objs = []
        for row in rows:
            obj = CourierBillModel(**row)
            if user_id:
                obj.created_id = user_id
                obj.updated_id = user_id
            objs.append(obj)
        self.db.add_all(objs)
        await self.db.flush()
