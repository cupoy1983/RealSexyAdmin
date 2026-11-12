from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, UserMixin

FILE_CATEGORY_ORDER = "order"
FILE_CATEGORY_BILL = "bill"

BATCH_STATUS_UNCONFIRMED = "unconfirmed"
BATCH_STATUS_CONFIRMED = "confirmed"


def build_batch_no(file_category: str, batch_month: str, sub_category: str | None = None) -> str:
    """批次号固定拼法：`order-2026-09` / `bill-yto-2026-09`（ADR-0033）。

    「同文件类别 + 同子类别 + 同月份只允许一个批次」必须由数据库强制，而 MySQL 的组合唯一
    索引对可空列不生效（订单批次没有子类别），所以把这条约束编码进一个非空的派生字符串，
    让它成为唯一索引的列。
    """
    parts = [file_category, sub_category] if sub_category else [file_category]
    parts.append(batch_month)
    return "-".join(parts)


class ImportBatchModel(ModelMixin, UserMixin):
    """导入批次 - 订单导入与账单导入共用的确认单位（ADR-0033）

    批次是人工确认的单位，也是利润的准入门槛：未确认批次的只是原始数据，利润不认。
    月份列两类共用（语义随 file_category 而变），因为任一类别永远只用其中一个，
    拆成两列会有一半恒空、且录入时容易填错。
    """

    __tablename__: str = "ecommerce_import_batch"
    __table_args__: dict[str, str] = {"comment": "导入批次（订单/账单共用的确认单位，ADR-0033）"}

    batch_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="批次号（唯一，拼法见 build_batch_no）")
    file_category: Mapped[str] = mapped_column(String(16), nullable=False, index=True, comment="文件类别(order:订单导入 bill:账单导入)")
    sub_category: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True, comment="子类别（仅账单填快递公司编码；订单为空）")
    batch_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True, comment="批次月份(YYYY-MM)：订单=发货月份，账单=出账月份")
    status: Mapped[str] = mapped_column(
        String(16), default=BATCH_STATUS_UNCONFIRMED, nullable=False, index=True,
        comment="批次状态(unconfirmed:未确认 confirmed:已确认)；确认后不可变、不支持反确认",
    )
    confirmed_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, nullable=True, comment="确认时间")
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="导入行数（订单=子订单行数，账单=账单行数）")
    order_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="业务对象数（订单=主订单数，账单=运单票数）")
    remark: Mapped[str | None] = mapped_column(
        Text, default=None, nullable=True,
        comment="批次提示（只往后拼、从不删旧内容；如账单回写触及已确认订单批次时记「改动了 order-2026-09，需重算利润」）",
    )
