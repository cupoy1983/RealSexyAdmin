"""删运费匹配结果表 + 订单历史数据回填导入批次

Revision ID: 8f2d47ac1b60
Revises: db18b40dc48d
Create Date: 2026-10-20 00:50:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8f2d47ac1b60'
down_revision: str | None = 'db18b40dc48d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 运费匹配结果表（ADR-0034）：判定与摊分结果现在直接写在账单行上，反向指针表没有读者。
    op.drop_table('ecommerce_freight_match')

    # 订单表的 import_batch_id 原先是每次导入随机生成的 uuid，不是批次号。跨批次校验
    # （更新扫到不属于本批次的订单即整批中止）会把这类值判成「外来批次」，历史数据一更新就全月中止，
    # 所以按「主订单 = 其最早发货子订单所在月」回填成 order-YYYY-MM，再补建对应的未确认批次行。
    op.execute(
        """
        UPDATE ecommerce_order o
        JOIN (
            SELECT d.order_id, DATE_FORMAT(MIN(d.ship_time), '%Y-%m') AS ship_month
            FROM ecommerce_order_detail d
            WHERE d.is_deleted = 0 AND d.ship_time IS NOT NULL
            GROUP BY d.order_id
        ) t ON t.order_id = o.id
        SET o.import_batch_id = CONCAT('order-', t.ship_month)
        WHERE (o.import_batch_id IS NULL OR o.import_batch_id NOT LIKE 'order-%')
        """
    )
    op.execute(
        """
        INSERT INTO ecommerce_import_batch
            (uuid, batch_no, file_category, sub_category, batch_month, status,
             row_count, order_count, is_deleted, created_time, updated_time)
        SELECT LOWER(REPLACE(UUID(), '-', '')),
               b.batch_no, 'order', NULL, SUBSTRING(b.batch_no, 7), 'unconfirmed',
               b.row_count, b.order_count, 0, NOW(), NOW()
        FROM (
            SELECT o.import_batch_id AS batch_no,
                   COUNT(d.id) AS row_count,
                   COUNT(DISTINCT o.id) AS order_count
            FROM ecommerce_order o
            LEFT JOIN ecommerce_order_detail d ON d.order_id = o.id AND d.is_deleted = 0
            WHERE o.is_deleted = 0 AND o.import_batch_id LIKE 'order-%'
            GROUP BY o.import_batch_id
        ) b
        WHERE NOT EXISTS (SELECT 1 FROM ecommerce_import_batch e WHERE e.batch_no = b.batch_no)
        """
    )


def downgrade() -> None:
    # 回填不可逆：原来的 uuid 批次号已经不存在，回滚只能重建表结构，
    # import_batch_id 保持 order-YYYY-MM、补建的批次行留在库里（旧代码不读这两样）。
    op.create_table('ecommerce_freight_match',
    sa.Column('courier_bill_id', sa.Integer(), nullable=True, comment='来源账单行ID'),
    sa.Column('waybill_no', sa.String(length=64), nullable=False, comment='运单号'),
    sa.Column('order_id', sa.Integer(), nullable=True, comment='匹配到的订单ID（账单有单无对应订单时为空）'),
    sa.Column('store_id', sa.Integer(), nullable=True, comment='店铺ID（随匹配订单带出）'),
    sa.Column('attributed_month', sa.String(length=7), nullable=False, comment='运费归属月份(按订单发货月，ADR-0025)'),
    sa.Column('waybill_fee', sa.Numeric(precision=10, scale=2), nullable=False, comment='面单费'),
    sa.Column('bill_other_fee', sa.Numeric(precision=10, scale=2), nullable=False, comment='账单其它费用'),
    sa.Column('actual_freight', sa.Numeric(precision=10, scale=2), nullable=False, comment='=waybill_fee+bill_other_fee'),
    sa.Column('anomaly_flag', sa.String(length=32), nullable=True, comment='异常标注'),
    sa.Column('is_recomputed', sa.Boolean(), nullable=False, comment='是否为已锁定月重算产生'),
    sa.Column('late_bill_notified_at', sa.DateTime(timezone=True), nullable=True, comment='命中已锁定月时的通知时间'),
    sa.Column('order_detail_id', sa.Integer(), nullable=True, comment='匹配到的子订单ID'),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='主键ID'),
    sa.Column('uuid', sa.String(length=64), nullable=False, comment='UUID全局唯一标识'),
    sa.Column('is_deleted', sa.Boolean(), nullable=False, comment='是否已删除(0:未删除 1:已删除)'),
    sa.Column('created_time', sa.DateTime(timezone=True), nullable=False, comment='创建时间'),
    sa.Column('updated_time', sa.DateTime(timezone=True), nullable=False, comment='更新时间'),
    sa.Column('deleted_time', sa.DateTime(timezone=True), nullable=True, comment='删除时间'),
    sa.Column('created_id', sa.Integer(), nullable=True, comment='创建人ID'),
    sa.Column('updated_id', sa.Integer(), nullable=True, comment='更新人ID'),
    sa.Column('deleted_id', sa.Integer(), nullable=True, comment='删除人ID'),
    sa.ForeignKeyConstraint(['courier_bill_id'], ['ecommerce_courier_bill.id'], name=op.f('fk_ecommerce_freight_match_courier_bill_id_ecommerce_courier_bill'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_id'], ['sys_user.id'], name=op.f('fk_ecommerce_freight_match_created_id_sys_user'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['deleted_id'], ['sys_user.id'], name=op.f('fk_ecommerce_freight_match_deleted_id_sys_user'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['order_id'], ['ecommerce_order.id'], name=op.f('fk_ecommerce_freight_match_order_id_ecommerce_order'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['order_detail_id'], ['ecommerce_order_detail.id'], name=op.f('fk_ecommerce_freight_match_order_detail_id_ecommerce_order_detail'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['store_id'], ['ecommerce_store.id'], name=op.f('fk_ecommerce_freight_match_store_id_ecommerce_store'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_id'], ['sys_user.id'], name=op.f('fk_ecommerce_freight_match_updated_id_sys_user'), onupdate='CASCADE', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ecommerce_freight_match')),
    comment='运费匹配结果'
    )
    with op.batch_alter_table('ecommerce_freight_match', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_anomaly_flag'), ['anomaly_flag'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_attributed_month'), ['attributed_month'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_courier_bill_id'), ['courier_bill_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_created_id'), ['created_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_created_time'), ['created_time'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_deleted_id'), ['deleted_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_is_deleted'), ['is_deleted'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_order_detail_id'), ['order_detail_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_order_id'), ['order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_store_id'), ['store_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_updated_id'), ['updated_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_uuid'), ['uuid'], unique=True)
        batch_op.create_index(batch_op.f('ix_ecommerce_freight_match_waybill_no'), ['waybill_no'], unique=False)
