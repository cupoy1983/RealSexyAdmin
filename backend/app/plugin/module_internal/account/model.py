from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin, UserMixin

if TYPE_CHECKING:
    from app.modules.system.dept.model import DeptModel
    from app.modules.system.user.model import UserModel


class ExternalAccountBindingModel(ModelMixin, UserMixin):
    """外部系统账号绑定表

    一个 OA 用户在每个外部系统最多绑定一个外部账号，一个外部账号也只能绑定一个 OA 用户。
    绑定成功后锁定：页面不提供改绑与解绑入口。
    """

    __tablename__: str = "iac_account_binding"
    __table_args__: tuple = (
        UniqueConstraint("system_type", "oa_user_id", name="uk_iac_account_binding_system_oa_user"),
        UniqueConstraint("system_type", "external_user_id", name="uk_iac_account_binding_system_external_user"),
        UniqueConstraint("system_type", "mobile", name="uk_iac_account_binding_system_mobile"),
    )

    system_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="外部系统类型(dingtalk/kuaimei/wecom)")
    oa_user_id: Mapped[int] = mapped_column(
        ForeignKey("sys_user.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="OA用户ID",
    )
    external_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="外部系统用户ID")
    external_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="外部系统姓名")
    mobile: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True, comment="手机号(匹配依据)")
    # ── 钉钉侧快照：每次考勤月报同步时顺带刷新（A5）─────────────────
    job_number: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="工号(钉钉通讯录 job_number)")
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组ID(钉钉)")
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组名称(钉钉)")
    dingtalk_dept_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="钉钉部门名称(报表取它，与OA部门名需保持一致)")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:启用 1:停用)")

    user: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[oa_user_id], lazy="selectin")


class DeptMappingModel(ModelMixin, UserMixin):
    """OA 部门与钉钉部门映射表

    新建 OA 用户时按所属 OA 部门找到钉钉部门，再创建钉钉通讯录成员。
    """

    __tablename__: str = "iac_account_dept_mapping"
    __table_args__: tuple = (
        UniqueConstraint("oa_dept_id", name="uk_iac_account_dept_mapping_oa_dept_id"),
    )

    oa_dept_id: Mapped[int] = mapped_column(
        ForeignKey("sys_dept.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="OA部门ID",
    )
    dingtalk_dept_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="钉钉部门ID")
    dingtalk_dept_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="钉钉部门名称")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:启用 1:停用)")

    dept: Mapped["DeptModel | None"] = relationship("DeptModel", foreign_keys=[oa_dept_id], lazy="selectin")
