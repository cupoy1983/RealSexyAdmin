from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import DeptMappingModel, ExternalAccountBindingModel
from .schema import (
    DeptMappingCreateSchema,
    DeptMappingUpdateSchema,
    ExternalAccountBindingCreateSchema,
    ExternalAccountBindingUpdateSchema,
)


class ExternalAccountBindingCRUD(
    CRUDBase[ExternalAccountBindingModel, ExternalAccountBindingCreateSchema, ExternalAccountBindingUpdateSchema]
):
    """外部账号绑定数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ExternalAccountBindingModel, auth=auth, db=db)

    async def get_by_oa_user(self, system_type: str, oa_user_id: int) -> ExternalAccountBindingModel | None:
        """按外部系统 + OA 用户查绑定"""
        return await self.get(system_type=system_type, oa_user_id=oa_user_id)

    async def get_by_external_user(self, system_type: str, external_user_id: str) -> ExternalAccountBindingModel | None:
        """按外部系统 + 外部账号查绑定"""
        return await self.get(system_type=system_type, external_user_id=external_user_id)

    async def list_by_system(self, system_type: str) -> list[ExternalAccountBindingModel]:
        """按外部系统列出启用中的绑定"""
        rows = await self.get_list(search={"system_type": system_type, "status": 0}, order_by=[{"id": "asc"}])
        return list(rows)


class DeptMappingCRUD(CRUDBase[DeptMappingModel, DeptMappingCreateSchema, DeptMappingUpdateSchema]):
    """部门映射数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=DeptMappingModel, auth=auth, db=db)

    async def get_by_oa_dept(self, oa_dept_id: int) -> DeptMappingModel | None:
        """按 OA 部门查钉钉部门映射"""
        return await self.get(oa_dept_id=oa_dept_id, status=0)
