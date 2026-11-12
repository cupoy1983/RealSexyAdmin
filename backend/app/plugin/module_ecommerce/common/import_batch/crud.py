from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import ImportBatchModel


class ImportBatchCRUD(CRUDBase[ImportBatchModel, dict, dict]):
    """导入批次 CRUD（创建只由导入流程内部字典驱动，不走标准 Create/UpdateSchema）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=ImportBatchModel, auth=auth, db=db)
