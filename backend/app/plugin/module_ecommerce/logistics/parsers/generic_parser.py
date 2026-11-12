"""通用 Excel 账单解析器（默认实现，覆盖绝大多数快递公司的账单格式）。

列名由 ``BillParseRuleModel.column_mapping``（``BillColumnMapping``）配置，不同快递
公司只是表头文字不同，解析逻辑本身通用。收件省在这里就完成归一化，落库值与主订单
province 同一口径，判异常时才能直接比。

「非账单行」（账单日期那格空着或读不出合法日期）在这里摘掉，不入库、不报错，只带出
行号与那一行的重量/费用供上层对账（ADR-0037）。

**数值格子在读这一格时就量化到落库列的精度**（ADR-0039 / 设计文档 §21.2）：账单重量 3 位、
账单其它费用 2 位，方向四舍五入，与订单侧共用 `common/numbers.py`。原先靠 MySQL 落库时代劳，
批次备注里报的就是「文件原值之和」，而界面/库里是数据库舍入之后的行，两个数对不上看起来像
系统算错；合计行那一格同样要同尺，因为它是**核对基准**。
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.exceptions import CustomException
from app.plugin.module_ecommerce.common.excel_head import MAX_HEADER_SCAN_ROWS, read_excel_with_header_scan
from app.plugin.module_ecommerce.common.numbers import MONEY_QUANT, WEIGHT_QUANT, quantize_half_up

from .base import BillParser, NonBillRow, RawBillRow

# 长后缀在前，避免 "广西壮族自治区" 被 "自治区" 截成 "广西壮族"
_PROVINCE_SUFFIXES: tuple[str, ...] = (
    "维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区",
    "自治区", "省", "市",
)

_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y年%m月%d日")

# 快递账单里表示"没有值"的常见占位符。真实导出只用得上空白格（2026-11-07 业务方核对），多认的几项是非数值
# 字符串、不会把有值判成空，所以照旧不收窄也不与订单侧合并——R 轮「Excel 脏值原语合并」挂账已定为不做。
_EMPTY_TOKENS: frozenset[str] = frozenset({"", "-", "--", "/", "无", "暂无"})


def normalize_province(value: str) -> str:
    """去掉省级行政区的官方尾缀，只留可比较的省名（只比省不比市）。"""
    v = value.strip()
    for suffix in _PROVINCE_SUFFIXES:
        if v.endswith(suffix) and len(v) > len(suffix):
            return v[: -len(suffix)]
    return v


def parse_bill_date(value: Any) -> date | None:
    """把 Excel 单元格里的账单日期转成 date。

    空值返回 None，认不出的写法抛 ``ValueError``——**两种都是「这一行不是账单行」**
    （ADR-0037）：快递合计行的日期格要么空着、要么写着「合计」之类不是日期的字样。
    """
    if value is None or (isinstance(value, str) and value.strip() in _EMPTY_TOKENS):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text in _EMPTY_TOKENS:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法识别的日期: {text}")


def parse_optional_decimal(value: Any, quant: Decimal) -> Decimal | None:
    """把可能为空的数值单元格转 Decimal 并量化到 `quant`；空/占位符返回 None（ADR-0039）。

    `quant` 由调用方按落库列点名（`账单重量` → `WEIGHT_QUANT`、`账单其它费用` → `MONEY_QUANT`）：
    不量就得由 MySQL 在落库那一刻代劳，而内存里那个值仍是原精度，批次备注与库里的行于是各说一套。
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in _EMPTY_TOKENS:
        return None
    return quantize_half_up(Decimal(text), quant)


def _soft_decimal(value: Any, quant: Decimal) -> Decimal | None:
    """非账单行上的数值格子：认不出就当没有，不报错。

    合计行的格子通常是数字，但快递也可能写 `45,6kg`、`合计` 这种文本；这一行本来就不入库、
    不算钱，为它抛异常等于把「合计行导致整份导不进来」这个 bug 换个地方再犯一次。
    量化照做（ADR-0039）：这一格是**对账基准**，与账单行不同尺就会报出一条纯由舍入差造成的
    「合计行与逐行求和不一致」。
    """
    try:
        return parse_optional_decimal(value, quant)
    except InvalidOperation:
        return None


class GenericExcelBillParser(BillParser):
    """通用 Excel 账单解析器：按 column_mapping 把中文表头映射为统一字段。"""

    def parse(self, contents: bytes, column_mapping: dict) -> list[RawBillRow]:
        # 表头行自动定位（ADR-0038）：账单文件第 1 行也可能是快递导出的报表标题。拿规则里配的
        # 运单号 + 账单日期当尺子；定位不到时把这件事如实带下去（§20.5 AF7），由 _parse_rows 报
        # 「前 N 行找不到表头行、去核对这两个列名」——那句「账单文件缺少必要的列」会指错方向。
        probe = [h for h in (column_mapping.get("waybill_no"), column_mapping.get("bill_date")) if h]
        rows, _header_row, located = read_excel_with_header_scan(contents, probe)
        result, self.non_bill_rows = self._parse_rows(rows, column_mapping, header_located=located)
        return result

    @staticmethod
    def _parse_rows(
        rows: list[dict[str, Any]], column_mapping: dict, *, header_located: bool,
    ) -> tuple[list[RawBillRow], list[NonBillRow]]:
        """把表格行分成「账单行」与「非账单行」（ADR-0037）；一行账单行都没有就整批中止。"""
        if not rows:
            raise CustomException(msg="账单文件为空")

        waybill_header = column_mapping.get("waybill_no")
        date_header = column_mapping.get("bill_date")
        weight_header = column_mapping.get("bill_weight")
        fee_header = column_mapping.get("bill_other_fee")
        province_header = column_mapping.get("receiver_province")
        # 五项全部必填（ADR-0029）：这里再查一遍，是因为建档时的 Pydantic 校验管不到
        # 收紧之前就已存在的旧结构规则——它们缺 receiver_province，宁可导入时拦下来。
        # 这一句也排在「定位不到表头」之前：规则本身没配全时，探针就是残缺的，那句
        # 「前 N 行找不到表头行」会把人支到「核对这两个列名」上，而真正少的是另外三格。
        if not all((waybill_header, date_header, weight_header, fee_header, province_header)):
            raise CustomException(
                msg="账单解析规则缺少必要的列映射配置"
                    "(waybill_no/bill_date/bill_weight/bill_other_fee/receiver_province)，请在「解析规则管理」重配"
            )

        # 定位不到表头行 ≠ 文件少列（§20.5 AF7）：这时上面那句「账单文件缺少必要的列」会把规则里
        # 配的五个列名全报一遍，可这些列可能好好地躺在扫描窗口之外，错的是规则里那两个探针列名。
        # 所以这里先说清「没找到表头行、该去核对规则里这两个名字」，少列那句话留给定位到了之后再说。
        if not header_located:
            raise CustomException(
                msg=f"账单文件前 {MAX_HEADER_SCAN_ROWS} 行里找不到同时含规则所配「{waybill_header}」"
                    f"「{date_header}」两列的表头行，一行都没入库；"
                    "请到「解析规则管理」核对『运单号』『账单日期』映射的列名是否与文件里一字不差，"
                    f"或确认表头是不是在第 {MAX_HEADER_SCAN_ROWS} 行之后"
            )

        required = {
            "运单号": waybill_header,
            "账单日期": date_header,
            "账单重量": weight_header,
            "账单其它费用": fee_header,
            "收件省": province_header,
        }
        missing_headers = [f"{label}({name})" for label, name in required.items() if name not in rows[0]]
        if missing_headers:
            raise CustomException(msg=f"账单文件缺少必要的列: {', '.join(missing_headers)}")

        result: list[RawBillRow] = []
        non_bill_rows: list[NonBillRow] = []
        for i, row in enumerate(rows, start=1):
            waybill_no = str(row.get(waybill_header) or "").strip()

            # 日期判定必须排在「运单号不能为空」那道闸**之前**（ADR-0037）：快递账单末尾的合计行
            # 恰好是运单号为空的那一行，先查运单号就会让整份文件报 `第N行: 运单号不能为空` 被拒
            # ——2026-10-27 用户在界面上报的 bug 就是这么来的。日期读不出（空或认不出的写法）就是
            # 「非账单行」：跳过、不算钱、不报错，但记下行号与重量/费用供上层对账。
            try:
                bill_date = parse_bill_date(row.get(date_header))
            except ValueError:
                bill_date = None
            if bill_date is None:
                non_bill_rows.append(
                    NonBillRow(
                        row_no=i,
                        bill_weight=_soft_decimal(row.get(weight_header), WEIGHT_QUANT),
                        bill_other_fee=_soft_decimal(row.get(fee_header), MONEY_QUANT),
                    )
                )
                continue

            # 日期合法却缺运单号：那是真的数据缺陷，照原样整份不落库
            if not waybill_no:
                raise CustomException(msg=f"第{i}行: 运单号不能为空")

            try:
                bill_weight = parse_optional_decimal(row.get(weight_header), WEIGHT_QUANT)
            except InvalidOperation:
                raise CustomException(msg=f"第{i}行: 账单重量不是合法的数值")

            # 负数没有业务含义 → 整份不落库（§16 A3，与订单侧「实发/实退数量为负」同一把尺子）：
            # 其它费用是直接进成本的钱，-2 会把一条真实子订单的 freight_cost 落成 -2.00；
            # 负重量还会让摊分算出负份额（一票运费拆成 -5.00 与 15.00）。
            # 判的是 `is_signed()` 而不是 `< 0`：ADR-0039 之后这一格已经整到列精度，
            # `-0.0004` 会变成 `-0.000`，它不 `< 0` 却仍是快递给的负数——用大小判会把脏值静默收成 0。
            if bill_weight is not None and bill_weight.is_signed():
                raise CustomException(msg=f"第{i}行: 账单重量不能为负（收到 {bill_weight}），整份不落库")

            try:
                raw_fee = parse_optional_decimal(row.get(fee_header), MONEY_QUANT)
            except InvalidOperation:
                raise CustomException(msg=f"第{i}行: 账单其它费用不是合法的数值")
            # 负数闸在「空值兜 0」之前：`-0.001` 量化后是 `-0.00`，它不 `< 0`、而且当假值被
            # `or Decimal("0.00")` 静默换成正 0.00——先兜底再判负，等于把这一格脏值收成 0 落库。
            if raw_fee is not None and raw_fee.is_signed():
                raise CustomException(msg=f"第{i}行: 账单其它费用不能为负（收到 {raw_fee}），整份不落库")
            bill_other_fee = raw_fee if raw_fee is not None else Decimal("0.00")

            province_raw = str(row[province_header]).strip() if row.get(province_header) is not None else ""
            receiver_province = normalize_province(province_raw) if province_raw not in _EMPTY_TOKENS else None

            result.append(
                RawBillRow(
                    waybill_no=waybill_no,
                    bill_date=bill_date,
                    bill_weight=bill_weight,
                    bill_other_fee=bill_other_fee,
                    receiver_province=receiver_province,
                    row_no=i,
                )
            )

        # 兜住本轮最危险的那条代价（ADR-0037）：账单日期那一列一旦被映射错、或快递改了日期格式，
        # 所有行都会被判成非账单行静默消失，钱一分不剩却「导入成功」。全部行都不像账单时必须报错，
        # 而且不许复用 Z15 那句「上传文件为空」——文件明明有几十行，那句话会把人指错方向。
        if not result and non_bill_rows:
            raise CustomException(
                msg=f"{len(non_bill_rows)} 行数据行的账单日期全都读不出来，一行都没入库；"
                    f"请检查解析规则里『账单日期』映射的是哪一列（当前配的是「{date_header}」）、"
                    "或那一列的日期写法是不是改了"
            )
        return result, non_bill_rows


# bill_type -> 解析器类，默认全部使用通用解析器；未来某快递公司账单格式明显不同
# （如非表格式导出）时，在此注册专属解析器类，不改变调用方（logistics.service）的接口
_BILL_PARSERS: dict[str, type[BillParser]] = {}


def get_bill_parser(bill_type: str) -> BillParser:
    """按 bill_type 取解析器实例；未注册专属解析器时回退到通用 Excel 解析器。"""
    parser_cls = _BILL_PARSERS.get(bill_type, GenericExcelBillParser)
    return parser_cls()
