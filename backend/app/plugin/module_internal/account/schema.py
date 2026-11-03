from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema, UserByQueryParam, UserBySchema
from app.core.validator import DateTimeStr

# 外部系统类型：本期只实现钉钉，其余仅预留扩展位
SYSTEM_DINGTALK: str = "dingtalk"
SYSTEM_KUAIMAI: str = "kuaimai"
SYSTEM_WECOM: str = "wecom"

EXTERNAL_SYSTEM_LABELS: dict[str, str] = {
    SYSTEM_DINGTALK: "钉钉",
    SYSTEM_KUAIMAI: "快麦",
    SYSTEM_WECOM: "企业微信",
}


class ExternalAccountBindingCreateSchema(BaseModel):
    """外部账号绑定创建模型"""

    system_type: str = Field(default=SYSTEM_DINGTALK, max_length=32, description="外部系统类型")
    oa_user_id: int = Field(..., ge=1, description="OA用户ID")
    external_user_id: str = Field(..., min_length=1, max_length=64, description="外部系统用户ID")
    external_name: str | None = Field(default=None, max_length=64, description="外部系统姓名")
    mobile: str | None = Field(default=None, max_length=20, description="手机号")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")

    @field_validator("system_type")
    @classmethod
    def _check_system_type(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in EXTERNAL_SYSTEM_LABELS:
            raise ValueError(f"外部系统类型必须为: {', '.join(EXTERNAL_SYSTEM_LABELS)}")
        return v


class ExternalAccountBindingUpdateSchema(ExternalAccountBindingCreateSchema):
    """外部账号绑定更新模型"""


class ExternalAccountBindingOutSchema(ExternalAccountBindingCreateSchema, BaseSchema, UserBySchema):
    """外部账号绑定响应模型"""

    model_config = ConfigDict(from_attributes=True)

    oa_user_name: str | None = Field(default=None, description="OA用户姓名")
    system_label: str | None = Field(default=None, description="外部系统名称")


class ExternalAccountBindingQueryParam(BaseQueryParam, UserByQueryParam):
    """外部账号绑定查询参数"""

    system_type: str | None = Field(None, description="外部系统类型", json_schema_extra={"q": "eq"})
    oa_user_id: int | None = Field(None, description="OA用户ID", json_schema_extra={"q": "eq"})
    external_user_id: str | None = Field(None, description="外部系统用户ID", json_schema_extra={"q": "like"})
    mobile: str | None = Field(None, description="手机号", json_schema_extra={"q": "like"})
    status: int | None = Field(None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})


class DeptMappingCreateSchema(BaseModel):
    """部门映射创建模型"""

    oa_dept_id: int = Field(..., ge=1, description="OA部门ID")
    dingtalk_dept_id: str = Field(..., min_length=1, max_length=64, description="钉钉部门ID")
    dingtalk_dept_name: str | None = Field(default=None, max_length=128, description="钉钉部门名称")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")

    @field_validator("dingtalk_dept_id")
    @classmethod
    def _strip_dept_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("钉钉部门ID不能为空")
        return v


class DeptMappingUpdateSchema(DeptMappingCreateSchema):
    """部门映射更新模型"""


class DeptMappingOutSchema(DeptMappingCreateSchema, BaseSchema, UserBySchema):
    """部门映射响应模型"""

    model_config = ConfigDict(from_attributes=True)

    oa_dept_name: str | None = Field(default=None, description="OA部门名称")


class DeptMappingQueryParam(BaseQueryParam, UserByQueryParam):
    """部门映射查询参数"""

    oa_dept_id: int | None = Field(None, description="OA部门ID", json_schema_extra={"q": "eq"})
    dingtalk_dept_id: str | None = Field(None, description="钉钉部门ID", json_schema_extra={"q": "like"})
    status: int | None = Field(None, ge=0, le=1, description="状态", json_schema_extra={"q": "eq"})


class DingtalkAccountQueryParam(BaseQueryParam):
    """钉钉账号 Tab 查询参数：以 OA 用户为主体，附带绑定状态"""

    name: str | None = Field(None, description="姓名/账号", json_schema_extra={"q": "like"})
    mobile: str | None = Field(None, description="手机号", json_schema_extra={"q": "like"})
    dept_id: int | None = Field(None, description="部门ID", json_schema_extra={"q": "eq"})
    bound: bool | None = Field(None, description="是否已绑定钉钉", json_schema_extra={"q": "eq"})


class DingtalkAccountOutSchema(BaseModel):
    """钉钉账号 Tab 行模型"""

    oa_user_id: int = Field(..., description="OA用户ID")
    username: str | None = Field(default=None, description="登录账号")
    name: str | None = Field(default=None, description="姓名")
    mobile: str | None = Field(default=None, description="手机号")
    dept_id: int | None = Field(default=None, description="部门ID")
    dept_name: str | None = Field(default=None, description="部门名称")
    user_status: int | None = Field(default=None, description="用户状态(0:启用 1:停用)")
    bound: bool = Field(default=False, description="是否已绑定钉钉")
    binding_id: int | None = Field(default=None, description="绑定ID")
    external_user_id: str | None = Field(default=None, description="钉钉userId")
    external_name: str | None = Field(default=None, description="钉钉姓名")
    bound_time: DateTimeStr | None = Field(default=None, description="绑定时间")
    can_match: bool = Field(default=False, description="是否允许手工匹配")
    match_message: str | None = Field(default=None, description="不可匹配原因")


class DingtalkManualMatchSchema(BaseModel):
    """钉钉账号手工匹配请求"""

    oa_user_id: int = Field(..., ge=1, description="OA用户ID")


class DingtalkManualMatchResultSchema(BaseModel):
    """钉钉账号手工匹配结果"""

    oa_user_id: int = Field(..., description="OA用户ID")
    external_user_id: str = Field(default="", description="钉钉userId")
    created: bool = Field(default=False, description="本次是否新建钉钉成员")
    message: str = Field(default="", description="结果说明")
