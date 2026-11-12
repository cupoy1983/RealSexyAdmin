"""可插拔快递账单解析器（ADR-0029）。

照抄 ``app/modules/task/storage/core/factory.py`` 的「协议 → 类」注册表分发模式：
按 ``bill_type`` 分发到具体解析器类；大多数快递公司的账单都是同一种"Excel + 可配置列名"
结构，差异只在表头命名（已由 ``BillParseRuleModel.column_mapping`` 承载），所以默认
``GenericExcelBillParser`` 覆盖绝大多数场景——仅当某快递公司账单格式明显不同（如非表格式
导出）时才需要注册专属解析器类。
"""

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class RawBillRow:
    """账单解析器返回的原始行中间表示（非 ORM）"""

    waybill_no: str
    bill_date: date
    bill_other_fee: Decimal
    bill_weight: Decimal | None = None
    receiver_province: str | None = None
    # 这一行在文件里的「数据行序号」（表头之上的说明行不计、非账单行也占一个号）；
    # 报错文案、异常原因、批次备注里的「第N行」全都用它，同一把尺子。
    row_no: int = 0


@dataclass
class NonBillRow:
    """被摘掉的「非账单行」（ADR-0037）：账单日期那格空着、或读不出合法日期的行。

    不入库、不算钱、不报错，但要把行号与那一行的重量/费用带出来——快递账单末尾的**合计行**
    天然就是这种形态（运单号/日期/省份全空，只带重量合计与费用合计），对账时正好拿这两个数
    跟前面各行的求和核一遍。
    """

    row_no: int
    bill_weight: Decimal | None = None
    bill_other_fee: Decimal | None = None


class BillParser:
    """账单解析器抽象接口。

    实例属性 ``non_bill_rows``：最近一次 ``parse`` 摘掉的非账单行，供上层写批次备注。
    ``parse`` 的返回值仍然只有账单行——换返回值要把每个调用点与既有回归脚本一起改写，
    而这里用的是订单侧 ``ExcelOrderDataSource.summary_row_found`` 同一个套路：一次导入
    新建一个解析器实例（``get_bill_parser``），实例状态不会串到下一次；``parse`` 在返回前
    **必定重新赋值**这个属性，所以下一次导入读到的永远是这一次的摘除结果。
    """

    non_bill_rows: list[NonBillRow] = []

    def parse(self, contents: bytes, column_mapping: dict) -> list[RawBillRow]:
        """解析账单文件字节内容为统一行表示（同步、CPU 密集，async 调用点走 ``aparse``）。

        参数:
        - contents (bytes): 账单文件字节内容
        - column_mapping (dict): 该账单类型的列映射配置（BillColumnMapping 结构）

        返回:
        - list[RawBillRow]: 解析后的账单行列表（不含非账单行）
        """
        raise NotImplementedError

    async def aparse(self, contents: bytes, column_mapping: dict) -> list[RawBillRow]:
        """``parse`` 的异步包装：openpyxl 解析是同步 CPU 操作，丢线程池避免阻塞事件循环。"""
        return await asyncio.to_thread(self.parse, contents, column_mapping)
