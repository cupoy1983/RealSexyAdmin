from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import StoreModel, TagModel
from .schema import (
    StoreCreateSchema,
    StoreUpdateSchema,
    TagCreateSchema,
    TagUpdateSchema,
)


class StoreCRUD(CRUDBase[StoreModel, StoreCreateSchema, StoreUpdateSchema]):
    """店铺 CRUD"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=StoreModel, auth=auth, db=db)

    async def set_store_tags_crud(self, store_obj: StoreModel, tag_objs: Sequence[TagModel]) -> None:
        """替换店铺的负责人(tag)关联（纯数据操作；store_obj 需预加载 tags）"""
        store_obj.tags.clear()
        store_obj.tags.extend(tag_objs)
        await self.db.flush()


class TagCRUD(CRUDBase[TagModel, TagCreateSchema, TagUpdateSchema]):
    """负责人(tag) CRUD"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=TagModel, auth=auth, db=db)
