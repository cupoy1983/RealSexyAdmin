"""iac 前缀统一 + 考勤日明细表

1. 内务中心 4 张表统一为 IAC_ 前缀（Internal Affairs Center）
   internal_account_binding            -> iac_account_binding
   internal_account_dept_mapping       -> iac_account_dept_mapping
   internal_attendance_monthly_report  -> iac_attendance_monthly_report
2. 绑定表新增钉钉侧快照：工号 / 考勤组ID / 考勤组名称 / 钉钉部门名
3. 月报表新增 group_id
4. 新建日明细表 iac_attendance_daily_report（一行 = 钉钉导出报表的一个日期格）

Revision ID: 90cbddd7a99d
Revises: fbb66947f9f5
Create Date: 2026-10-28 00:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "90cbddd7a99d"
down_revision: Union[str, None] = "fbb66947f9f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. 表改名 ────────────────────────────────────────────────
    op.rename_table("internal_account_dept_mapping", "iac_account_dept_mapping")
    op.rename_table("internal_account_binding", "iac_account_binding")
    op.rename_table("internal_attendance_monthly_report", "iac_attendance_monthly_report")

    # 唯一约束随表改名同步改名（MySQL 支持 RENAME INDEX）
    op.execute("ALTER TABLE iac_account_dept_mapping RENAME INDEX uk_internal_account_dept_mapping_oa_dept_id TO uk_iac_account_dept_mapping_oa_dept_id")
    op.execute("ALTER TABLE iac_account_binding RENAME INDEX uk_internal_account_binding_system_oa_user TO uk_iac_account_binding_system_oa_user")
    op.execute("ALTER TABLE iac_account_binding RENAME INDEX uk_internal_account_binding_system_external_user TO uk_iac_account_binding_system_external_user")
    op.execute("ALTER TABLE iac_account_binding RENAME INDEX uk_internal_account_binding_system_mobile TO uk_iac_account_binding_system_mobile")
    op.execute("ALTER TABLE iac_attendance_monthly_report RENAME INDEX uk_internal_attendance_monthly_report_user_month TO uk_iac_attendance_monthly_report_user_month")

    # ── 2. 绑定表加钉钉侧快照 ────────────────────────────────────
    op.add_column("iac_account_binding", sa.Column("job_number", sa.String(length=32), nullable=True, comment="工号(钉钉通讯录 job_number)"))
    op.add_column("iac_account_binding", sa.Column("group_id", sa.String(length=64), nullable=True, comment="考勤组ID(钉钉)"))
    op.add_column("iac_account_binding", sa.Column("group_name", sa.String(length=64), nullable=True, comment="考勤组名称(钉钉)"))
    op.add_column("iac_account_binding", sa.Column("dingtalk_dept_name", sa.String(length=128), nullable=True, comment="钉钉部门名称(报表取它，与OA部门名需保持一致)"))

    # ── 3. 月报表加考勤组ID ──────────────────────────────────────
    op.add_column("iac_attendance_monthly_report", sa.Column("group_id", sa.String(length=64), nullable=True, comment="考勤组ID快照(钉钉)"))

    # ── 4. 日明细表 ──────────────────────────────────────────────
    op.create_table(
        "iac_attendance_daily_report",
        sa.Column("oa_user_id", sa.Integer(), nullable=False, comment="OA用户ID"),
        sa.Column("work_date", sa.Date(), nullable=False, comment="考勤日期"),
        sa.Column("month", sa.String(length=7), nullable=False, comment="所属月份(YYYY-MM)，冗余便于按月查"),
        sa.Column("day_of_month", sa.Integer(), nullable=True, comment="日号，冗余便于按列展示排序"),
        sa.Column("group_id", sa.String(length=64), nullable=True, comment="考勤组ID快照"),
        sa.Column("group_name", sa.String(length=64), nullable=True, comment="考勤组名称快照"),
        sa.Column("raw_text", sa.String(length=512), nullable=True, comment="钉钉考勤结果原文"),
        sa.Column("approve_text", sa.String(length=512), nullable=True, comment="关联的审批单原文"),
        sa.Column("late_minutes", sa.Integer(), nullable=True, comment="迟到分钟(预留)"),
        sa.Column("early_minutes", sa.Integer(), nullable=True, comment="早退分钟(预留)"),
        sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True, comment="实际上班打卡(预留)"),
        sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True, comment="实际下班打卡(预留)"),
        sa.Column("is_workday", sa.Integer(), nullable=True, comment="是否应出勤日(预留)"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True, comment="最近同步时间"),
        sa.Column("status", sa.Integer(), nullable=False, comment="状态(0:启用 1:停用)"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
        sa.Column("uuid", sa.String(length=64), nullable=False, comment="UUID全局唯一标识"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, comment="是否已删除(0:未删除 1:已删除)"),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False, comment="创建时间"),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False, comment="更新时间"),
        sa.Column("deleted_time", sa.DateTime(timezone=True), nullable=True, comment="删除时间"),
        sa.Column("created_id", sa.Integer(), nullable=True, comment="创建人ID"),
        sa.Column("updated_id", sa.Integer(), nullable=True, comment="更新人ID"),
        sa.Column("deleted_id", sa.Integer(), nullable=True, comment="删除人ID"),
        sa.ForeignKeyConstraint(["created_id"], ["sys_user.id"], name=op.f("fk_iac_attendance_daily_report_created_id_sys_user"), onupdate="CASCADE", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_id"], ["sys_user.id"], name=op.f("fk_iac_attendance_daily_report_deleted_id_sys_user"), onupdate="CASCADE", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["oa_user_id"], ["sys_user.id"], name=op.f("fk_iac_attendance_daily_report_oa_user_id_sys_user"), onupdate="CASCADE", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_id"], ["sys_user.id"], name=op.f("fk_iac_attendance_daily_report_updated_id_sys_user"), onupdate="CASCADE", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_iac_attendance_daily_report")),
        sa.UniqueConstraint("oa_user_id", "work_date", name="uk_iac_attendance_daily_report_user_date"),
    )
    with op.batch_alter_table("iac_attendance_daily_report", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_created_id"), ["created_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_created_time"), ["created_time"], unique=False)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_deleted_id"), ["deleted_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_is_deleted"), ["is_deleted"], unique=False)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_month"), ["month"], unique=False)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_oa_user_id"), ["oa_user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_updated_id"), ["updated_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_uuid"), ["uuid"], unique=True)
        batch_op.create_index(batch_op.f("ix_iac_attendance_daily_report_work_date"), ["work_date"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("iac_attendance_daily_report", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_work_date"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_uuid"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_updated_id"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_oa_user_id"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_month"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_is_deleted"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_id"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_deleted_id"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_created_time"))
        batch_op.drop_index(batch_op.f("ix_iac_attendance_daily_report_created_id"))
    op.drop_table("iac_attendance_daily_report")

    op.drop_column("iac_attendance_monthly_report", "group_id")
    op.drop_column("iac_account_binding", "dingtalk_dept_name")
    op.drop_column("iac_account_binding", "group_name")
    op.drop_column("iac_account_binding", "group_id")
    op.drop_column("iac_account_binding", "job_number")

    op.execute("ALTER TABLE iac_attendance_monthly_report RENAME INDEX uk_iac_attendance_monthly_report_user_month TO uk_internal_attendance_monthly_report_user_month")
    op.execute("ALTER TABLE iac_account_binding RENAME INDEX uk_iac_account_binding_system_mobile TO uk_internal_account_binding_system_mobile")
    op.execute("ALTER TABLE iac_account_binding RENAME INDEX uk_iac_account_binding_system_external_user TO uk_internal_account_binding_system_external_user")
    op.execute("ALTER TABLE iac_account_binding RENAME INDEX uk_iac_account_binding_system_oa_user TO uk_internal_account_binding_system_oa_user")
    op.execute("ALTER TABLE iac_account_dept_mapping RENAME INDEX uk_iac_account_dept_mapping_oa_dept_id TO uk_internal_account_dept_mapping_oa_dept_id")

    op.rename_table("iac_attendance_monthly_report", "internal_attendance_monthly_report")
    op.rename_table("iac_account_binding", "internal_account_binding")
    op.rename_table("iac_account_dept_mapping", "internal_account_dept_mapping")
