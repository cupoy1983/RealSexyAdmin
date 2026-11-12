"""订单 Excel 导入（第一期默认数据源，ADR-0027）。

快麦 ERP 每月默认延后 15 天导出订单 Excel（§5.1）。导出的每一行是一个「订单商品明细」
（子订单），多行可共享同一个「系统订单号」；本数据源按**系统订单号**归并为主订单，
金额（买家已付/退款/运费收入）在归并时求和（ADR-0030）。
这几列的钱在**读格子那一步**就按四舍五入整到分（`common/numbers.py::round_cent`，§20.4 AF5=甲）——
它们是快麦报过来的既成事实，不走见位进分；先整分再求和，主订单那格才与子订单行逐字相等（Z2）。
**每一格数值都照这条规矩**（ADR-0039 / §21.5）：重量整到 3 位、成本价整到 4 位，精度取自落库列，
不等数据库代劳——否则「只导入」与「导入后再更新」就会给同一格算出两个数。

文件末尾有一行「汇总」（序号列 = `汇总`、无系统订单号），其买家已付/退款/运费收入合计
等于全部有效数据行之和，用作**校验和**：不一致则整批报错、不落库。
**必填格子空着一律带行号报错、整份不落库**（2026-11-02 AN21）：系统订单号、店铺编码、订单商品明细ID、
店铺全称、店铺平台、订单状态、订单买家已付金额（可 0 不可空）。
**主商家编码(款)与商品标题(款)不在这份名单里**（AN29，2026-11-03 实测退回）：业务方给的参照文件里
539/5567 行（9.7%）这两格同时为空，是快麦「没录商家编码的商品」的稳定导出形态，拦它等于把正常月份整份挡在门外。
原先其中三列的态度是「整行静默跳过」：脏文件照样报「导入成功」，少掉的那一票利润要等到算账才看得出来，
而「第几行坏了」这条线索已经丢了。店铺编码**填 `0` 是合法的**（2026-11-03 AN34，撤销 AN22 那句「`0` 当没填」）：
那是快麦给「出库单」留的固定编码，业务方手工建一家 id=0 的店，那些行照常进利润；**空着仍然报错**。
每个系统订单号第一次出现的那一行提供「店铺全称」「店铺平台」与「订单状态」，为空一律报错——
不回落成「快麦id 当店名」「空串当平台」（ADR-0024 的 AF3 修订，§20.3）。
出库单那批文件的「店铺全称」写的就是字面 `-`，所以自动建档有两条例外（ADR-0024 的 AN34/AP3 修订块）：
**店铺编码 `0` 那家店，店名与平台两格都不覆盖**（AP3，2026-11-08）；**别的店那一格是占位符时不覆盖店名**、
平台照刷（AN34）。否则每月导一次就把他那家「出库单」改名一次。
汇总行整行豁免（AN27）：它在读第一列（序号=`汇总`）时就 `continue`，走不到这些闸。

「是否ERP发货」列两件事：
- 合法写法只有 `ERP发货 / 其他ERP发货 / 未发货` 三个（AN27）：其余——含空着、含 `0`、含任何别的写法——
  带行号整份报错，不再把空值当未发货默默处理。比对前先把这一格外面的括号剥掉（AN30，
  `_normalize_shipped_flag`）：业务方的参照文件里有 43 行写的是 `[未发货]`，按精确字符串比对时
  那 43 行既不报错也不被「未发货不入库」拦下，直接落进了库；报错文案里读的仍是文件原样。
- `is_erp_shipped` 白名单精确匹配——只有整格（去括号后）恰为 `ERP发货` 才是真，`其他ERP发货`、
  `未发货` 都为假（子串匹配会把 `其他ERP发货` 误判成 ERP 发货，让账单匹配认错人）。
- `未发货` 行**不入库**，但校验和必须先带着它们一起合计——快麦「汇总」行是按
  全部行算的，先扔行再对账必然对不上。剔除之后主订单金额按「= Σ入库子订单」重算。

「订单标签」列（可选，只有新版式有）：归一后子串含 `已生成采购单` → 分销订单（ADR-0036）。
**表头行自动定位**（ADR-0038）：新版式第 1 行是报表标题、第 2 行才是表头，定位逻辑在
`common/excel_head.py`，报错用的「第N行」按真实 Excel 行号算。定位不到时说的是「前 N 行里
找不到表头行」，不是那句会让人去文件里找并不缺的列的「缺少必要的列」（§20.5 AF7）。
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.exceptions import CustomException
from app.plugin.module_ecommerce.common.excel_head import MAX_HEADER_SCAN_ROWS, aread_excel_with_header_scan
from app.plugin.module_ecommerce.common.import_batch.service import EMPTY_IMPORT_FILE_MSG
from app.plugin.module_ecommerce.common.numbers import (
    COST_QUANT,
    MONEY_QUANT,
    WEIGHT_QUANT,
    quantize_half_up,
    round_cent,
)

from .base import (
    OrderDataSource,
    RawOrderDetailRecord,
    RawOrderRecord,
    classify_distribution_order,
    normalize_merchant_code,
    normalize_spec_code,
)

# 快麦导出 Excel 的中文表头（旧版式 35 列、新版式 36 列，列名唯一、按名取值不依赖列序）
H_SEQ = "序号"
H_DETAIL_ID = "订单商品明细ID"
H_SYSTEM_ORDER_NO = "系统订单号"
H_PLATFORM_ORDER_NO = "平台订单号"
H_ORDER_TYPE = "订单类型"
H_STORE_NAME = "店铺全称"
H_PLATFORM = "店铺平台"
H_SHOP_CODE = "店铺编码"
H_WAREHOUSE = "仓库名称"
H_COURIER_TEMPLATE = "快递模版名称"
H_TRACKING_NO = "快递单号"
H_IS_ERP_SHIPPED = "是否ERP发货"
H_ORDER_STATUS = "订单状态"
# 新版式（2026-10-28 实测 `12345.xlsx`）才有的一列，插在「订单状态」与「订单状态(明细)」之间；
# 旧版式没有 → 它是**可选列**，缺列时全部按非分销订单处理并提示（ADR-0036，决议 AC7/AC8）
H_ORDER_TAG = "订单标签"
H_DETAIL_STATUS = "订单状态(明细)"
H_PRODUCT_NAME = "商品标题(款)"
H_MAIN_SKU_CODE = "主商家编码(款)"
H_SKU_CODE = "规格商家编码(SKU)"
H_SKU_NAME = "规格属性名称(SKU)"
H_WEIGHT_KG = "重量kg(SKU)"
H_PAY_TIME = "付款时间"
H_SHIP_TIME = "发货时间"
H_FINISH_TIME = "完成时间"
H_PROVINCE = "省份"
H_CITY = "城市"
H_SHIP_QTY = "系统实发商品数"
H_FREIGHT_INCOME = "运费收入"
H_PAID_AMOUNT = "订单买家已付金额"
H_EXCEL_COST_PRICE = "成本价(SKU)"
H_ERP_SHIPPED_COST = "系统实发成本"
H_AFTER_SALES_NO = "售后工单号"
H_RETURN_QTY = "实退数量"
H_REFUND_AMOUNT = "平台实退金额"
H_ERP_RETURN_COST = "实退成本"
H_SYSTEM_REMARK = "系统备注"
H_SELLER_REMARK = "卖家备注"

_REQUIRED_HEADERS: list[str] = [
    H_DETAIL_ID, H_SYSTEM_ORDER_NO, H_SHOP_CODE, H_STORE_NAME, H_PLATFORM,
    H_ORDER_STATUS, H_MAIN_SKU_CODE, H_SKU_CODE, H_PRODUCT_NAME, H_PAID_AMOUNT,
]

# 定位表头行只拿这两列当尺子（ADR-0038）：快麦新版导出第 1 行是报表标题、第 2 行才是表头，
# 只扫「一定存在、一定唯一」的列名，避免拿店铺平台之类的通用词把标题行误判成表头。
# 它们同时也是定位失败时报错点名的那两个名字（§20.5 AF7）——列名硬编码，改列名要改代码这件事
# 必须在那句话里说清楚，否则用户只会以为文件缺列。
_HEADER_LOCATE_HEADERS: list[str] = [H_DETAIL_ID, H_SYSTEM_ORDER_NO]

_SUMMARY_SEQ = "汇总"

# 「是否ERP发货」的合法写法只有这三个（2026-11-02 AN27：快麦这一格不发空、不发 `0`，
# 认出这三个以外的写法没有业务含义，当成未发货默默丢行等于替业务方决定哪一行不算数）。
# 只有第一个算 ERP 发货；顺序照快麦报表从「已发」到「未发」摆，报错文案直接用它。
# 比对前先把外面套的括号剥掉（AN30，见 `_normalize_shipped_flag`）。
ERP_SHIPPED_VALUE = "ERP发货"
_OTHER_ERP_SHIPPED_VALUE = "其他ERP发货"
UNSHIPPED_VALUE = "未发货"
_ERP_SHIPPED_VALUES: tuple[str, ...] = (ERP_SHIPPED_VALUE, _OTHER_ERP_SHIPPED_VALUE, UNSHIPPED_VALUE)
# 快递账单侧的「这一格没值」词表（`logistics/parsers/generic_parser.py` 同一份，ADR-0037）：真正用得上
# 的只有空白格——2026-11-07 业务方核对真实导出，快麦与快递的数值格子里只有空与数值，`--` `/` `无` `暂无`
# 一种都没出现过。多认几项不会把有值判成空，所以这份词表既不与其他两处合并、也不收窄（R 轮那笔「Excel 脏值
# 原语合并」挂账已定为不做，理由见设计文档 §11「本期挂账」最后一条）。
_EMPTY_TOKENS: frozenset[str] = frozenset({"", "-", "--", "/", "无", "暂无"})

def _to_decimal(value: Any, field_name: str, row_no: int) -> Decimal:
    """将 Excel 单元格值转为 Decimal（空值视为 0），四舍五入整到分（§20.4 AF5 / ADR-0039）。

    本函数只服务「快麦报过来的钱」这几列（买家已付/平台实退/运费收入，以及汇总行的对应格）；
    重量与成本价同样在**读这一格**时量化，只是精度不同，走 `_to_decimal_or_none`。
    """
    if value is None or value == "":
        # 空格子也过同一把尺（AN31）：早return `Decimal("0")` 数值上没错，但 ADR-0039 说的是
        # 「每一格都在读的时候整到落库列精度」，让空值例外就等于给「求和后到底是 0 还是 0.00」
        # 留了个只能靠肉眼确认的口子。
        return round_cent(Decimal("0"))
    try:
        return round_cent(Decimal(str(value)))
    except InvalidOperation:
        raise CustomException(msg=f"第{row_no}行: {field_name} 不是合法的数值")


def _to_decimal_or_none(value: Any, field_name: str, row_no: int, quant: Decimal) -> Decimal | None:
    """可空数值格子：没填返回 None，填了却读不出来**带行号报错**（§20.2 AF2），读到就量化到
    落库列的精度 `quant`、方向四舍五入（ADR-0039 / 设计文档 §21.5）。

    空值与占位符返回 None 是业务语义——「未填写」与「填 0」必须分得开（ADR-0023）。
    但认不出的写法以前也返回 None，等于把一格脏值变成 0 成本、0 重量塞进利润，
    还不告诉任何人第几行坏了；同一份文件里买家已付/退款脏值却会报错，两套态度。

    `quant` 由调用方按落库列点名（重量 `WEIGHT_QUANT`、成本价 `COST_QUANT`、钱 `MONEY_QUANT`）。
    这一格原先不带精度、把「整到 3 位」留给 MySQL，而更新侧自己 `.quantize(Decimal("0.001"))`
    走默认凑偶，于是同一份文件跑一次更新就把 `0.003` 改写成 `0.002`——重量正是运费摊分的权重，
    那 0.001kg 够让一票运费在几条子订单之间挪 1 分（§21.1 AG1）。
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in _EMPTY_TOKENS:
        return None
    try:
        return quantize_half_up(Decimal(text), quant)
    except InvalidOperation:
        raise CustomException(msg=f"第{row_no}行: {field_name} 不是合法的数值（读到「{text}」），整份不落库")


def _to_int(value: Any, field_name: str, row_no: int) -> int:
    """数量格子：没填按 0 算（快麦无值即无数量），读不出来带行号报错（§20.2 AF2）。"""
    if value is None:
        return 0
    text = str(value).strip()
    if text in _EMPTY_TOKENS:
        return 0
    try:
        return int(float(text))
    except (ValueError, TypeError):
        raise CustomException(msg=f"第{row_no}行: {field_name} 不是合法的整数（读到「{text}」），整份不落库")


def _to_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _require_cell(row: dict[str, Any], header: str, row_no: int) -> str:
    """必填文本列：空着就带行号报错、整份不落库（2026-11-02 AN21）。

    这一格原先的态度是「空 = 落个空串 / 整行 `continue` 跳过」：一份缺数据的脏文件照样报「导入成功」，
    脏数字躺在库里要等到算利润才被发现，而「第几行坏了」这条线索已经被丢掉了。
    汇总行在更前面就 `continue` 掉了，走不到这里（AN27：那一行整行豁免）。
    """
    value = _to_str_or_none(row.get(header))
    if value is None:
        raise CustomException(msg=f"第{row_no}行: {header}不能为空，整份不落库")
    return value


def _normalize_shipped_flag(value: str) -> str:
    """剥掉「是否ERP发货」那一格外面的括号，再拿去比对白名单（2026-11-03 AN30 实测）。

    业务方给的参照文件（9月那份 5567 行）里有 **43 行写的是 `[未发货]`**，不是 `未发货`。
    以前这一格按精确字符串比对，于是那 43 行既没被白名单拦下、也没被「未发货不入库」丢掉：
    它们带着 `is_erp_shipped=False` 落了库，还通过 `_keep_shipped_totals` 参与了主订单金额——
    正是 ADR-0034 明令不许的那件事。剥括号只改「认不认得出」这一层：认出来的写法照 ADR-0034 不入库，
    认不出来的写法照 AN27 报错，而报错里读的仍是文件里的原样。
    """
    text = value.strip()
    for open_ch, close_ch in (("[", "]"), ("【", "】"), ("(", ")"), ("（", "）")):
        if len(text) >= 2 and text.startswith(open_ch) and text.endswith(close_ch):
            return text[1:-1].strip()
    return text


class ExcelOrderDataSource(OrderDataSource):
    """快麦 ERP 导出 Excel 订单数据源（第一期默认，ADR-0027）。

    一行 Excel = 一条子订单（订单商品明细）；同一「系统订单号」的多行归并为一个主订单。
    """

    # 本次解析有没有读到 `序号=汇总` 那一行；没有则校验和被整体跳过，由 service 报给界面（§16 B2）
    summary_row_found: bool = False
    # 本次解析有没有「订单标签」这一列；没有 → 每条子订单的 `distribution_order_type` 是 None
    # （= 无从判断，更新路径因此不碰这一列），由 service 报给界面（ADR-0036，决议 AC8/AC15）
    order_tag_column_found: bool = False

    async def fetch(self, *, contents: bytes, **_: object) -> list[RawOrderRecord]:
        """解析订单 Excel 字节内容，按系统订单号归并子订单行。

        参数:
        - contents (bytes): 上传的 Excel 文件字节内容

        返回:
        - list[RawOrderRecord]: 按系统订单号归并后的主订单记录列表
        """
        rows: list[dict[str, Any]]
        header_row: int
        located: bool
        rows, header_row, located = await aread_excel_with_header_scan(contents, _HEADER_LOCATE_HEADERS)
        # 空文件先报「文件为空」：对它说「找不到表头行」同样是误导（§20.5 AF7）
        if not rows:
            raise CustomException(msg=EMPTY_IMPORT_FILE_MSG)
        if not located:
            probe = "」「".join(_HEADER_LOCATE_HEADERS)
            raise CustomException(
                msg=f"这份文件前 {MAX_HEADER_SCAN_ROWS} 行里找不到同时含「{probe}」的表头行，一行都没解析；"
                    f"可能这不是快麦「销售统计分析-按订单商品明细」导出，也可能快麦把这两列改了名"
                    f"（列名硬编码在 order/importers/excel_source.py）"
            )

        missing_headers = [h for h in _REQUIRED_HEADERS if h not in rows[0]]
        if missing_headers:
            raise CustomException(msg=f"导入文件缺少必要的列: {', '.join(missing_headers)}")

        # 「订单标签」是可选列（只有新版式有）：缺列时每条子订单的分类落 None，让 service 分得清
        # 「这份文件没给标签」与「这份文件说全是非分销」——更新路径靠这个决定要不要动那一列（AC15）。
        self.order_tag_column_found = H_ORDER_TAG in rows[0]

        summary_row: dict[str, Any] | None = None
        orders_by_no: dict[str, RawOrderRecord] = {}
        order_sequence: list[str] = []

        # 报错用的行号 = Excel 里的真实行号：表头在第 header_row 行，数据就从下一行起（ADR-0038）
        for i, row in enumerate(rows, start=header_row + 1):
            seq = _to_str_or_none(row.get(H_SEQ))
            if seq == _SUMMARY_SEQ:
                summary_row = row
                continue

            system_order_no = _require_cell(row, H_SYSTEM_ORDER_NO, i)

            # 店铺编码必填，但 `0` 是合法值（2026-11-03 AN34 撤销 AN22）：快麦给「出库单」这类
            # 无源单据留的固定编码就是 0，业务方手工建一家 id=0 的店挂负责人，那些行照常进利润。
            # 「空着」仍然整份报错——AF3 防的是拿编码冒充店名、拿空串冒充平台，那件事由后面两格守。
            shop_code = _require_cell(row, H_SHOP_CODE, i)

            detail_id = _to_str_or_none(row.get(H_DETAIL_ID))
            if not detail_id:
                raise CustomException(msg=f"第{i}行: 订单商品明细ID不能为空")

            # 买家已付金额可 0 不可空（AN21）：`_to_decimal` 把空格读成 0，「没填」就成了「这行不收钱」，
            # 主订单金额当场少一笔——所以「空」要在进那把尺之前单独拦下来。
            # 平台实退金额/运费收入不在 AN21 的必填名单里，照旧「空=0」。
            paid_raw = row.get(H_PAID_AMOUNT)
            if paid_raw is None or str(paid_raw).strip() == "":
                raise CustomException(msg=f"第{i}行: {H_PAID_AMOUNT}不能为空（可以填 0），整份不落库")
            paid_amount = _to_decimal(paid_raw, H_PAID_AMOUNT, i)
            refund_amount = _to_decimal(row.get(H_REFUND_AMOUNT), H_REFUND_AMOUNT, i)
            freight_income = _to_decimal(row.get(H_FREIGHT_INCOME), H_FREIGHT_INCOME, i)

            order = orders_by_no.get(system_order_no)
            if order is None:
                # 店名/平台空着**不回落**（§20.3 AF3）：主订单的店铺标识只取这一行（ADR-0030 归并口径），
                # 回落成「快麦id 当店名」「空串当平台」会往店铺档案里造一条名字是数字、平台为空的记录，
                # 而这两列是人工认店与按平台汇总的唯一凭据——脏在档案里比导不进来难发现得多。
                store_name = _to_str_or_none(row.get(H_STORE_NAME))
                if not store_name:
                    raise CustomException(
                        msg=f"第{i}行: 店铺全称不能为空（快麦店铺id {shop_code}），整份不落库"
                    )
                platform = _to_str_or_none(row.get(H_PLATFORM))
                if not platform:
                    raise CustomException(
                        msg=f"第{i}行: 店铺平台不能为空（店铺「{store_name}」，快麦店铺id {shop_code}），整份不落库"
                    )
                order = RawOrderRecord(
                    system_order_no=system_order_no,
                    kuaimai_shop_id=shop_code,
                    store_name=store_name,
                    platform=platform,
                    order_status=_require_cell(row, H_ORDER_STATUS, i),
                    province=_to_str_or_none(row.get(H_PROVINCE)),
                    city=_to_str_or_none(row.get(H_CITY)),
                    # 备注归主订单级，取第一行（首行建单时写入，后续行不覆盖，ADR-0031）
                    system_remark=_to_str_or_none(row.get(H_SYSTEM_REMARK)),
                    seller_remark=_to_str_or_none(row.get(H_SELLER_REMARK)),
                    # 刷单标记逻辑待需求方最终确认（§5.1 第10点），第一期先默认非刷单
                    is_shua_order=False,
                )
                orders_by_no[system_order_no] = order
                order_sequence.append(system_order_no)

            order.buyer_paid_amount += paid_amount
            order.refund_amount += refund_amount
            order.freight_income += freight_income

            erp_shipped_cell = _require_cell(row, H_IS_ERP_SHIPPED, i)
            erp_shipped = _normalize_shipped_flag(erp_shipped_cell)
            if erp_shipped not in _ERP_SHIPPED_VALUES:
                raise CustomException(
                    msg=f"第{i}行: {H_IS_ERP_SHIPPED}只能是「{' / '.join(_ERP_SHIPPED_VALUES)}」之一"
                        f"（读到「{erp_shipped_cell}」），整份不落库"
                )
            # 主商家编码(款)与商品标题(款)**保持可空**（AN29，2026-11-03 实测把 AN27 里我列的这两格退回来）：
            # 业务方给的参照文件（9月那份 5567 行）里有 539 行（9.7%）这两格**同时为空**，库里那 539 行就是
            # 同一份文件导进去的——这是快麦「没录商家编码的商品」的稳定导出形态，不是哪一笔录错。
            # 空编码这一行的成本永远算不出，靠 cost_missing 的口径排除在「待补充成本」清单之外（ADR-0023：
            # 没有编码就没有可补的对象），所以这一格拦下来只会让整份文件进不来，不会让任何一个数变干净。
            main_sku_code = normalize_merchant_code(_to_str_or_none(row.get(H_MAIN_SKU_CODE)))
            product_name = _to_str_or_none(row.get(H_PRODUCT_NAME))
            # 实发/实退数量为负没有业务含义 → 整批中止（§15.4，口径同「撞已存在订单号」，不做跳过该行）
            ship_qty = _to_int(row.get(H_SHIP_QTY), H_SHIP_QTY, i)
            return_qty = _to_int(row.get(H_RETURN_QTY), H_RETURN_QTY, i)
            if ship_qty < 0 or return_qty < 0:
                raise CustomException(
                    msg=f"第{i}行: 实发/实退数量不能为负（收到 实发 {ship_qty}、实退 {return_qty}），整份不落库"
                )

            detail = RawOrderDetailRecord(
                source_detail_id=detail_id,
                sku_code=normalize_spec_code(main_sku_code, _to_str_or_none(row.get(H_SKU_CODE))),
                product_name=product_name,
                platform_order_no=_to_str_or_none(row.get(H_PLATFORM_ORDER_NO)),
                order_type=_to_str_or_none(row.get(H_ORDER_TYPE)),
                detail_status=_to_str_or_none(row.get(H_DETAIL_STATUS)),
                main_sku_code=main_sku_code,
                sku_name=_to_str_or_none(row.get(H_SKU_NAME)),
                weight_kg=_to_decimal_or_none(row.get(H_WEIGHT_KG), H_WEIGHT_KG, i, WEIGHT_QUANT),
                ship_qty=ship_qty,
                return_qty=return_qty,
                paid_amount=paid_amount,
                refund_amount=refund_amount,
                freight_income=freight_income,
                warehouse_name=_to_str_or_none(row.get(H_WAREHOUSE)),
                courier_template=_to_str_or_none(row.get(H_COURIER_TEMPLATE)),
                tracking_no=_to_str_or_none(row.get(H_TRACKING_NO)),
                is_erp_shipped=erp_shipped == ERP_SHIPPED_VALUE,
                distribution_order_type=(
                    classify_distribution_order(_to_str_or_none(row.get(H_ORDER_TAG)))
                    if self.order_tag_column_found
                    else None
                ),
                pay_time=_to_str_or_none(row.get(H_PAY_TIME)),
                ship_time=_to_str_or_none(row.get(H_SHIP_TIME)),
                finish_time=_to_str_or_none(row.get(H_FINISH_TIME)),
                after_sales_no=_to_str_or_none(row.get(H_AFTER_SALES_NO)),
                excel_cost_price=_to_decimal_or_none(row.get(H_EXCEL_COST_PRICE), H_EXCEL_COST_PRICE, i, COST_QUANT),
                erp_shipped_cost=_to_decimal_or_none(row.get(H_ERP_SHIPPED_COST), H_ERP_SHIPPED_COST, i, MONEY_QUANT),
                erp_return_cost=_to_decimal_or_none(row.get(H_ERP_RETURN_COST), H_ERP_RETURN_COST, i, MONEY_QUANT),
            )
            if erp_shipped != UNSHIPPED_VALUE:
                order.details.append(detail)

        if not orders_by_no:
            raise CustomException(msg="导入文件没有有效订单数据")

        all_orders = [orders_by_no[no] for no in order_sequence]
        self.summary_row_found = summary_row is not None
        self._validate_checksum(all_orders, summary_row)
        return self._keep_shipped_totals(all_orders)

    @staticmethod
    def _keep_shipped_totals(orders: list[RawOrderRecord]) -> list[RawOrderRecord]:
        """剔除未发货行之后重算主订单金额，整单都未发货的订单直接丢弃。

        主订单金额必须回到「= Σ入库子订单」这条不变式上（ADR-0030），否则展开行里
        子订单求和与列表上的主订单金额对不上，利润按哪边算就成了二义。
        """
        kept: list[RawOrderRecord] = []
        for order in orders:
            if not order.details:
                continue
            order.buyer_paid_amount = sum((d.paid_amount for d in order.details), Decimal("0"))
            order.refund_amount = sum((d.refund_amount for d in order.details), Decimal("0"))
            order.freight_income = sum((d.freight_income for d in order.details), Decimal("0"))
            kept.append(order)
        if not kept:
            raise CustomException(msg="导入文件里全是未发货行，没有可入库的订单")
        return kept

    @staticmethod
    def _validate_checksum(orders: list[RawOrderRecord], summary_row: dict[str, Any] | None) -> None:
        """用「汇总」行合计校验数据行求和，不一致则整批报错（不落库）。"""
        if summary_row is None:
            return  # 无汇总行则跳过校验（容忍不含汇总行的导出）

        sum_paid = sum((o.buyer_paid_amount for o in orders), Decimal("0"))
        sum_refund = sum((o.refund_amount for o in orders), Decimal("0"))
        sum_freight = sum((o.freight_income for o in orders), Decimal("0"))

        exp_paid = _to_decimal(summary_row.get(H_PAID_AMOUNT), "汇总买家已付", -1)
        exp_refund = _to_decimal(summary_row.get(H_REFUND_AMOUNT), "汇总退款", -1)
        exp_freight = _to_decimal(summary_row.get(H_FREIGHT_INCOME), "汇总运费收入", -1)

        if sum_paid != exp_paid or sum_refund != exp_refund or sum_freight != exp_freight:
            raise CustomException(
                msg=(
                    "导入校验失败：数据行合计与「汇总」行不一致。"
                    f"买家已付 {sum_paid} vs {exp_paid}；退款 {sum_refund} vs {exp_refund}；"
                    f"运费收入 {sum_freight} vs {exp_freight}"
                )
            )
