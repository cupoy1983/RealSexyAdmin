from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseQueryParam, BaseSchema, UserByQueryParam, UserBySchema


class TagSimpleSchema(BaseModel):
    """负责人(tag) 精简信息，供 Store 列表嵌套展示"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="负责人ID")
    tag_name: str = Field(description="负责人名称")


# ── 店铺(Store) ──────────────────────────────────────────────────────────


class StoreCreateSchema(BaseModel):
    """新增店铺"""

    kuaimai_shop_id: str = Field(..., min_length=1, max_length=64, description="快麦店铺id")
    store_name: str = Field(..., min_length=1, max_length=128, description="店铺名称")
    platform: str = Field(..., min_length=1, max_length=64, description="所属平台")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:正常 1:停用)")
    tag_ids: list[int] = Field(default_factory=list, description="负责人(tag) ID列表")


class StoreUpdateSchema(BaseModel):
    """修改店铺"""

    kuaimai_shop_id: str | None = Field(default=None, min_length=1, max_length=64, description="快麦店铺id")
    store_name: str | None = Field(default=None, min_length=1, max_length=128, description="店铺名称")
    platform: str | None = Field(default=None, min_length=1, max_length=64, description="所属平台")
    status: int | None = Field(default=None, ge=0, le=1, description="状态(0:正常 1:停用)")
    tag_ids: list[int] | None = Field(default=None, description="负责人(tag) ID列表，传入则整体替换")


class StoreBatchTagSchema(BaseModel):
    """批量修改店铺负责人(tag)"""

    ids: list[int] = Field(..., min_length=1, description="店铺ID列表")
    tag_ids: list[int] = Field(default_factory=list, description="负责人(tag) ID列表，整体替换")


class StoreBatchStatusSchema(BaseModel):
    """批量设置店铺状态"""

    ids: list[int] = Field(..., min_length=1, description="店铺ID列表")
    status: int = Field(..., ge=0, le=1, description="状态(0:正常 1:停用)")


class StoreOutSchema(BaseSchema, UserBySchema):
    """店铺响应模型"""

    model_config = ConfigDict(from_attributes=True)

    kuaimai_shop_id: str = Field(description="快麦店铺id")
    store_name: str = Field(description="店铺名称")
    platform: str = Field(description="所属平台")
    status: int = Field(description="状态(0:正常 1:停用)")
    tags: list[TagSimpleSchema] = Field(default_factory=list, description="负责人(tag)列表")


class StoreQueryParam(BaseQueryParam, UserByQueryParam):
    """店铺查询参数"""

    kuaimai_shop_id: str | None = Field(None, description="快麦店铺id", json_schema_extra={"q": "like"})
    store_name: str | None = Field(None, description="店铺名称", json_schema_extra={"q": "like"})
    platform: str | None = Field(None, description="所属平台", json_schema_extra={"q": "eq"})
    status: int | None = Field(None, description="状态", json_schema_extra={"q": "eq"})


# ── 负责人(Tag) ──────────────────────────────────────────────────────────


class TagCreateSchema(BaseModel):
    """新增负责人(tag)"""

    tag_name: str = Field(..., min_length=1, max_length=64, description="负责人名称")
    description: str | None = Field(default=None, max_length=255, description="备注")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:正常 1:停用)")


class TagUpdateSchema(BaseModel):
    """修改负责人(tag)"""

    tag_name: str | None = Field(default=None, min_length=1, max_length=64, description="负责人名称")
    description: str | None = Field(default=None, max_length=255, description="备注")
    status: int | None = Field(default=None, ge=0, le=1, description="状态(0:正常 1:停用)")


class TagOutSchema(TagCreateSchema, BaseSchema, UserBySchema):
    """负责人(tag)响应模型"""

    model_config = ConfigDict(from_attributes=True)


class TagQueryParam(BaseQueryParam, UserByQueryParam):
    """负责人(tag)查询参数"""

    tag_name: str | None = Field(None, description="负责人名称", json_schema_extra={"q": "like"})
    status: int | None = Field(None, description="状态", json_schema_extra={"q": "eq"})
