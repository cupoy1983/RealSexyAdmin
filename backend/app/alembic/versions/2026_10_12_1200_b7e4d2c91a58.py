"""电商利润第一期：订单两表重构 + 删除店铺映射表（ADR-0024/0025/0030）

Revision ID: b7e4d2c91a58
Revises: 81a1dc4e4ef8
Create Date: 2026-10-12 12:00:00.000000

草案说明（仅供人工复核，未应用）：
- 本迁移按「以真实 ERP 导出为准」的两表模型重塑 ecommerce_order / ecommerce_order_detail，
  给 ecommerce_freight_match 增加子订单外键 order_detail_id，并删除已废弃的 ecommerce_store_mapping 表。
- ⚠️ 破坏性：upgrade() 开头会清空 ecommerce_order / ecommerce_order_detail 数据。
  经确认「之前导入器列名对不上，大概率没成功导入过真实数据，可清空重来」，故在此直接清表，
  以便安全地把 store_id 改为 NOT NULL、给子订单加 source_detail_id(NOT NULL, UNIQUE) 等约束。
  若目标库确有需保留的订单数据，请先备份再执行。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e4d2c91a58'
down_revision: str | None = '81a1dc4e4ef8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 0. 清表（破坏性，见模块 docstring）：FK 安全顺序 ──────────────────
    # ecommerce_order_detail.order_id 对 order 是 CASCADE，删 order 会级联删 detail；
    # ecommerce_freight_match.order_id 对 order 是 SET NULL，删 order 会置空。
    op.execute("DELETE FROM ecommerce_order_detail")
    op.execute("DELETE FROM ecommerce_order")

    # ── 1. 删除废弃的店铺映射表（ADR-0024：店铺从订单数据自动建档） ─────────
    op.drop_table('ecommerce_store_mapping')

    # ── 2. ecommerce_order：删列 / 加列 / store_id 改非空+CASCADE ──────────
    with op.batch_alter_table('ecommerce_order', schema=None) as batch_op:
        # 2a. 先解除 store_id 外键，稍后按 CASCADE 重建
        batch_op.drop_constraint('fk_ecommerce_order_store_id_ecommerce_store', type_='foreignkey')
        # 2b. 删除随列存在的索引
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_platform_order_no'))
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_kuaimai_shop_id'))
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_ship_time'))
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_waybill_no'))
        # 2c. 删除下沉到子订单或废弃的列
        batch_op.drop_column('platform_order_no')
        batch_op.drop_column('kuaimai_shop_id')
        batch_op.drop_column('pay_time')
        batch_op.drop_column('ship_time')
        batch_op.drop_column('finish_time')
        batch_op.drop_column('waybill_no')
        # 2d. 新增表头级汇总/地域列
        batch_op.add_column(sa.Column('shipped_cost_amount', sa.Numeric(precision=12, scale=2), nullable=False, comment='发货成本（= Σ子订单 computed_product_cost，利润中减项）'))
        batch_op.add_column(sa.Column('return_cost_amount', sa.Numeric(precision=12, scale=2), nullable=False, comment='退货成本（= Σ子订单 computed_return_cost，利润中加回项）'))
        batch_op.add_column(sa.Column('freight_income', sa.Numeric(precision=12, scale=2), nullable=False, comment='运费收入（= Σ子订单运费收入，仅统计展示，已含在买家已付里，不叠加进实际销售额）'))
        batch_op.add_column(sa.Column('province', sa.String(length=64), nullable=True, comment='省份'))
        batch_op.add_column(sa.Column('city', sa.String(length=64), nullable=True, comment='城市'))
        # 2e. store_id 改为 NOT NULL（自动建档后订单必有店铺）
        batch_op.alter_column('store_id', existing_type=sa.Integer(), nullable=False, comment='店铺ID（导入时按快麦店铺id自动建档得到，ADR-0024）')
        # 2f. 按 CASCADE 重建 store_id 外键
        batch_op.create_foreign_key('fk_ecommerce_order_store_id_ecommerce_store', 'ecommerce_store', ['store_id'], ['id'], onupdate='CASCADE', ondelete='CASCADE')

    # ── 3. ecommerce_order_detail：加子订单级列 + 索引 ─────────────────────
    with op.batch_alter_table('ecommerce_order_detail', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_detail_id', sa.String(length=64), nullable=False, comment='订单商品明细ID（数据源唯一键）'))
        batch_op.add_column(sa.Column('platform_order_no', sa.String(length=64), nullable=True, comment='平台订单号'))
        batch_op.add_column(sa.Column('order_type', sa.String(length=32), nullable=True, comment='订单类型（平台/线下/补发/空单/拆单等，一律计入收入，ADR-0030）'))
        batch_op.add_column(sa.Column('detail_status', sa.String(length=32), nullable=True, comment='订单状态(明细级原始值)'))
        batch_op.add_column(sa.Column('main_sku_code', sa.String(length=64), nullable=True, comment='主商家编码(款)'))
        batch_op.add_column(sa.Column('weight_kg', sa.Numeric(precision=10, scale=3), nullable=True, comment='重量kg(SKU)'))
        batch_op.add_column(sa.Column('paid_amount', sa.Numeric(precision=12, scale=2), nullable=False, comment='订单买家已付金额（行级）'))
        batch_op.add_column(sa.Column('refund_amount', sa.Numeric(precision=12, scale=2), nullable=False, comment='平台实退金额（行级）'))
        batch_op.add_column(sa.Column('warehouse_name', sa.String(length=128), nullable=True, comment='仓库名称'))
        batch_op.add_column(sa.Column('courier_template', sa.String(length=128), nullable=True, comment='快递模版名称'))
        batch_op.add_column(sa.Column('tracking_no', sa.String(length=64), nullable=True, comment='快递单号（账单匹配键，ADR-0025）'))
        batch_op.add_column(sa.Column('is_erp_shipped', sa.Boolean(), nullable=False, comment='是否ERP发货'))
        batch_op.add_column(sa.Column('pay_time', sa.DateTime(timezone=True), nullable=True, comment='付款时间'))
        batch_op.add_column(sa.Column('ship_time', sa.DateTime(timezone=True), nullable=True, comment='发货时间（运费归属月份的依据，ADR-0025）'))
        batch_op.add_column(sa.Column('finish_time', sa.DateTime(timezone=True), nullable=True, comment='完成时间'))
        batch_op.add_column(sa.Column('after_sales_no', sa.String(length=64), nullable=True, comment='售后工单号'))
        batch_op.add_column(sa.Column('system_remark', sa.Text(), nullable=True, comment='系统备注'))
        batch_op.add_column(sa.Column('seller_remark', sa.Text(), nullable=True, comment='卖家备注'))
        batch_op.add_column(sa.Column('erp_shipped_cost', sa.Numeric(precision=12, scale=2), nullable=True, comment='系统实发成本快麦原值（仅比对告警，ADR-0022）'))
        batch_op.add_column(sa.Column('erp_return_cost', sa.Numeric(precision=12, scale=2), nullable=True, comment='实退成本快麦原值（仅比对告警，ADR-0022）'))
        # 索引
        batch_op.create_index(batch_op.f('ix_ecommerce_order_detail_source_detail_id'), ['source_detail_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_ecommerce_order_detail_platform_order_no'), ['platform_order_no'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_order_detail_main_sku_code'), ['main_sku_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_order_detail_tracking_no'), ['tracking_no'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_order_detail_ship_time'), ['ship_time'], unique=False)

    # ── 4. ecommerce_freight_match：新增子订单外键 order_detail_id ─────────
    with op.batch_alter_table('ecommerce_freight_match', schema=None) as batch_op:
        batch_op.add_column(sa.Column('order_detail_id', sa.Integer(), nullable=True, comment='匹配到的子订单ID'))
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_order_detail_id'), ['order_detail_id'], unique=False)
        # 约定全名 fk_ecommerce_freight_match_order_detail_id_ecommerce_order_detail 为 65 字符，
        # 超过 MySQL 标识符 64 上限；此处显式采用 SQLAlchemy 截断+md5 摘要后的名字（与建模约定一致）
        batch_op.create_foreign_key('fk_ecommerce_freight_match_order_detail_id_ecommerce_ord_cd73', 'ecommerce_order_detail', ['order_detail_id'], ['id'], onupdate='CASCADE', ondelete='SET NULL')


def downgrade() -> None:
    # 注意：upgrade() 清空的订单数据无法在 downgrade() 中恢复。

    # ── 4'. ecommerce_freight_match：移除 order_detail_id ──────────────────
    with op.batch_alter_table('ecommerce_freight_match', schema=None) as batch_op:
        batch_op.drop_constraint('fk_ecommerce_freight_match_order_detail_id_ecommerce_ord_cd73', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_ecommerce_freight_match_order_detail_id'))
        batch_op.drop_column('order_detail_id')

    # ── 3'. ecommerce_order_detail：移除新增列 + 索引 ──────────────────────
    with op.batch_alter_table('ecommerce_order_detail', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_detail_ship_time'))
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_detail_tracking_no'))
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_detail_main_sku_code'))
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_detail_platform_order_no'))
        batch_op.drop_index(batch_op.f('ix_ecommerce_order_detail_source_detail_id'))
        batch_op.drop_column('erp_return_cost')
        batch_op.drop_column('erp_shipped_cost')
        batch_op.drop_column('seller_remark')
        batch_op.drop_column('system_remark')
        batch_op.drop_column('after_sales_no')
        batch_op.drop_column('finish_time')
        batch_op.drop_column('ship_time')
        batch_op.drop_column('pay_time')
        batch_op.drop_column('is_erp_shipped')
        batch_op.drop_column('tracking_no')
        batch_op.drop_column('courier_template')
        batch_op.drop_column('warehouse_name')
        batch_op.drop_column('refund_amount')
        batch_op.drop_column('paid_amount')
        batch_op.drop_column('weight_kg')
        batch_op.drop_column('main_sku_code')
        batch_op.drop_column('detail_status')
        batch_op.drop_column('order_type')
        batch_op.drop_column('platform_order_no')
        batch_op.drop_column('source_detail_id')

    # ── 2'. ecommerce_order：还原旧列结构 ─────────────────────────────────
    with op.batch_alter_table('ecommerce_order', schema=None) as batch_op:
        batch_op.drop_constraint('fk_ecommerce_order_store_id_ecommerce_store', type_='foreignkey')
        batch_op.alter_column('store_id', existing_type=sa.Integer(), nullable=True, comment='店铺ID（未映射店铺时为空，挂起待处理）')
        batch_op.drop_column('city')
        batch_op.drop_column('province')
        batch_op.drop_column('freight_income')
        batch_op.drop_column('return_cost_amount')
        batch_op.drop_column('shipped_cost_amount')
        batch_op.add_column(sa.Column('waybill_no', sa.String(length=64), nullable=True, comment='快递单号（账单匹配键，ADR-0025）'))
        batch_op.add_column(sa.Column('finish_time', sa.DateTime(timezone=True), nullable=True, comment='完成时间'))
        batch_op.add_column(sa.Column('ship_time', sa.DateTime(timezone=True), nullable=True, comment='发货时间（运费归属月份的依据，ADR-0025）'))
        batch_op.add_column(sa.Column('pay_time', sa.DateTime(timezone=True), nullable=True, comment='支付时间'))
        batch_op.add_column(sa.Column('kuaimai_shop_id', sa.String(length=64), nullable=False, comment='快麦店铺id（导入时原始值，用于未映射店铺排查）'))
        batch_op.add_column(sa.Column('platform_order_no', sa.String(length=64), nullable=False, comment='平台订单号'))
        batch_op.create_index(batch_op.f('ix_ecommerce_order_waybill_no'), ['waybill_no'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_order_ship_time'), ['ship_time'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_order_platform_order_no'), ['platform_order_no'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_order_kuaimai_shop_id'), ['kuaimai_shop_id'], unique=False)
        batch_op.create_foreign_key('fk_ecommerce_order_store_id_ecommerce_store', 'ecommerce_store', ['store_id'], ['id'], onupdate='CASCADE', ondelete='SET NULL')

    # ── 1'. 重建店铺映射表 ────────────────────────────────────────────────
    op.create_table('ecommerce_store_mapping',
    sa.Column('kuaimai_shop_id', sa.String(length=64), nullable=False, comment='快麦店铺id'),
    sa.Column('store_id', sa.Integer(), nullable=False, comment='店铺ID'),
    sa.Column('status', sa.Integer(), nullable=False, comment='状态(0:正常 1:停用)'),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='主键ID'),
    sa.Column('uuid', sa.String(length=64), nullable=False, comment='UUID全局唯一标识'),
    sa.Column('is_deleted', sa.Boolean(), nullable=False, comment='是否已删除(0:未删除 1:已删除)'),
    sa.Column('created_time', sa.DateTime(timezone=True), nullable=False, comment='创建时间'),
    sa.Column('updated_time', sa.DateTime(timezone=True), nullable=False, comment='更新时间'),
    sa.Column('deleted_time', sa.DateTime(timezone=True), nullable=True, comment='删除时间'),
    sa.Column('created_id', sa.Integer(), nullable=True, comment='创建人ID'),
    sa.Column('updated_id', sa.Integer(), nullable=True, comment='更新人ID'),
    sa.Column('deleted_id', sa.Integer(), nullable=True, comment='删除人ID'),
    sa.ForeignKeyConstraint(['created_id'], ['sys_user.id'], name=op.f('fk_ecommerce_store_mapping_created_id_sys_user'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['deleted_id'], ['sys_user.id'], name=op.f('fk_ecommerce_store_mapping_deleted_id_sys_user'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['store_id'], ['ecommerce_store.id'], name=op.f('fk_ecommerce_store_mapping_store_id_ecommerce_store'), onupdate='CASCADE', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_id'], ['sys_user.id'], name=op.f('fk_ecommerce_store_mapping_updated_id_sys_user'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ecommerce_store_mapping')),
    comment='店铺映射(快麦店铺id -> Store)'
    )
    with op.batch_alter_table('ecommerce_store_mapping', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_created_id'), ['created_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_created_time'), ['created_time'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_deleted_id'), ['deleted_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_is_deleted'), ['is_deleted'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_kuaimai_shop_id'), ['kuaimai_shop_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_store_id'), ['store_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_updated_id'), ['updated_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_store_mapping_uuid'), ['uuid'], unique=True)
