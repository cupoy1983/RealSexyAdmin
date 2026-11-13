"""数值格子的量化原语（ADR-0039）：方向写死，精度取自落库列。

本目录的准入声明见 ADR-0035——电商多个子域共用、且不是外部系统对接的代码才进 ``common/``。
「读一个 Excel 格子 → 落到某一列」这件事订单与账单两个子域都在做，且必须**同一个方向**，
所以尺子放在这里只有一份。原先它散在 ``order/service.py`` 与 ``order/importers/excel_source.py``
两处各写一遍（注释里明说是「同一把尺的两份拷贝，导入侧不能 import 服务层会成环」）——
拷贝这件事本身就是下一次走岔的入口。

规矩（ADR-0039 / 设计文档 §21.5）：

1. 每个「文件格子 → 落库列」的数值，在**读那一格那一步**就量化到它落库那一列的精度。
   精度以模型列为准，不另外发明：金额列 ``Numeric(*, 2)``、重量列 ``Numeric(10, 3)``、
   成本价列**按表分**：订单侧那两列（快照 ``cost_price``、快麦原值 ``excel_cost_price``）仍是
   ``Numeric(12, 4)``，商品成本主表那一列已于 2026-11-06 收成 ``Numeric(12, 2)``（ADR-0039 修订块）。
2. 方向一律显式 ``ROUND_HALF_UP``（四舍五入、远离零）。**Python 默认的 ``quantize`` 是凑偶**
   （ROUND_HALF_EVEN，``1.025 → 1.02``），而 MySQL 的 DECIMAL 舍入是 half away from zero
   （``1.025 → 1.03``）——不写方向就等于承认「导入交给数据库、更新自己算」两套并存，
   那正是 Z1/AF5/AG 三轮在治的同一个病。选四舍五入因此不是新决定，而是**把数据库一直在
   做的事说到明面上**：存量数字一个都不变，不需要数据订正、不需要重跑导入。
3. 比较、求和、告警、写库只用量化后的值；导入与更新两条路径调用这里的同一个函数。

这里只管「量化到哪一位、用什么方向」，不管「用哪把尺」：**算出来的**金额走
``order/service.py::_ceil_cent``（见位进分，只此一处使用，故留在订单子域），快麦报过来的
源值走这里的 ``round_cent``。两者都不许用默认舍入。
"""

from decimal import ROUND_HALF_UP, Decimal

# 落库列的精度（与 order/model.py、logistics/model.py 的 Numeric scale 一一对应）
MONEY_QUANT = Decimal("0.01")
WEIGHT_QUANT = Decimal("0.001")
COST_QUANT = Decimal("0.0001")


def quantize_half_up(value: Decimal, quant: Decimal) -> Decimal:
    """量化到 ``quant`` 那一格，方向写死四舍五入（远离零）。"""
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def round_cent(value: Decimal) -> Decimal:
    """钱：四舍五入到分。快麦报过来的源值列、账单里的费用列、摊分出来的份额都用它。"""
    return quantize_half_up(value, MONEY_QUANT)


def round_weight(value: Decimal) -> Decimal:
    """重量：四舍五入到千克后三位（``Numeric(10, 3)``）。

    这一格是 ADR-0039 那条规矩的由头：导入侧原先不量化、由 MySQL 收成 3 位，更新侧自己
    ``.quantize(0.001)`` 走默认凑偶，于是同一份文件跑一次更新就把 ``0.003`` 改写成 ``0.002``。
    重量又是运费摊分的权重，所以这 0.001kg 会让运费在子订单之间的归属挪动。
    """
    return quantize_half_up(value, WEIGHT_QUANT)


def round_cost(value: Decimal) -> Decimal:
    """订单侧那一格成本价原值：四舍五入到小数第 4 位（``Numeric(12, 4)``）。

    调用方只剩 ``excel_source.py`` 读快麦「成本价(SKU)」**到 ``excel_cost_price``** 那一格；主表
    的成本价不走这里——它 2026-11-06 收成 2 位，录入侧由 ``over_cent`` 直接拒收第 3 位。
    留 4 位的理由是「别人报给我们的凭据要留原值」：把 `5.0552` 整成 `5.06` 正好抹掉比对
    告警要提示的那一半。**但读 5 位的原值去比库里 4 位的值**会报一条假的「成本价不一致」
    告警，所以照样在读取那一步就整到 4 位（见 docs/ecommerce/CONTEXT.md「成本价」）。
    """
    return quantize_half_up(value, COST_QUANT)


def over_cent(value: Decimal) -> bool:
    """这一格钱是否细过分（小数第 3 位及以后还有非零数字）——**录入**侧用它拒收。

    ADR-0039 的 2026-11-02 修订把这条例外写在录入那一步：商品成本主表的成本价只准到分，
    填出第 3 位说明人自己没想清楚，系统替他进位等于悄悄改账（AN16「不用舍、进位，只需要报错」）。
    尾零不算超位：`3.110` 与 `3.11` 是同一个数，放行（口径同 §25 面单费第 4 条）。
    """
    return quantize_half_up(value, MONEY_QUANT) != value
