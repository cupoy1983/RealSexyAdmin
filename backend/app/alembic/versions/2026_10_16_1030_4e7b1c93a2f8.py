"""订单更新功能：子订单表增加更新备注列（ADR-0032）

Revision ID: 4e7b1c93a2f8
Revises: d9122c72c74b
Create Date: 2026-10-16 10:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4e7b1c93a2f8'
down_revision: str | None = 'd9122c72c74b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('ecommerce_order_detail', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'update_remark', sa.Text(), nullable=True,
                comment='更新备注（订单更新变更日志，「[时间戳] 字段 旧值→新值；」逐批追加，ADR-0032）',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('ecommerce_order_detail', schema=None) as batch_op:
        batch_op.drop_column('update_remark')
