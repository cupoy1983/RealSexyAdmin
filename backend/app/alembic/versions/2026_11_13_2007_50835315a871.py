"""电商链与内务链合并为单一 head（同仓同库口径）

Revision ID: 50835315a871
Revises: 23b901e40b7e, 90beee8578a9
Create Date: 2026-11-13 20:07:53.207779

两个根保留、只并一个头：`upgrade head` 需要唯一终点，而两条链建的表互不重叠
（电商 9 张 `ecommerce_*` / 内务 10 张 `iac_*`），所以这个合并点本身不带任何 DDL。

已经手工建齐两域表的库（例如内务那边的 dev 库）不能靠本文件补结构，
要先 `alembic stamp` 把两条链记为已应用，否则 upgrade 会在 create_table 上撞 duplicate。
"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '50835315a871'
down_revision: str | Sequence[str] | None = ('23b901e40b7e', '90beee8578a9')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
