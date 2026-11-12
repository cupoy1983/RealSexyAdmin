"""Excel 表头行自动定位（ADR-0038）：电商插件自己的「读一张表」入口。

本目录的准入声明见 ADR-0035——电商多个子域共用、且不是外部系统对接的代码才进 ``common/``。
订单导入与账单导入两个子域都要读 Excel，也都要绕开框架的同一个限制，所以落在这里。

为什么不复用框架的 ``app/utils/excel_util.py``：``ExcelUtil.read_excel_to_dicts`` 把表头
**写死在第 1 行**，而快麦同一张报表有两种版式并存——旧导出第 1 行就是表头，新导出第 1 行是
报表标题、第 2 行才是表头（2026-10-28 实测 ``12345.xlsx`` 完全导不进来，报的是那句把人指错
方向的「导入文件缺少必要的列」）。``ExcelUtil`` 被系统模块的导入导出共用，按 ADR-0019
「只做业务代码」不动它，定位逻辑留在业务侧。

行构建规则与 ``ExcelUtil.read_excel_to_dicts`` 保持一致（``data_only=True`` 取公式算好的值、
整行全空跳过），只多一处收紧：表头格子里的空串/纯空格不建键，且建键前把表头文字 ``strip()``
一次——定位用的名字与取值用的名字必须是同一把尺子，否则「扫到了表头却读不出值」。
"""

import asyncio
import io
from collections.abc import Sequence
from typing import Any

from openpyxl import load_workbook

# 往下最多扫这么多行找表头。真实版式的表头在第 1 或第 2 行；留余量是为了万一哪家快递在表头
# 之上塞了几行说明文字，也不会被当成数据行读进来。扫不到时照旧按第 1 行是表头建键，
# 但必须如实告诉调用方「没定位到」（ADR-0038 的 AF7 修订）——让它去报那句「缺少必要的列」
# 会把人支到文件里去找其实好好躺在后面的列，真正该查的是解析规则的列名配置。
MAX_HEADER_SCAN_ROWS = 10


def _cell_name(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _locate_header_index(rows: list[tuple[Any, ...]], required_headers: Sequence[str]) -> int | None:
    """前 ``MAX_HEADER_SCAN_ROWS`` 行里，第一个同时含全部必需列名的行（0 基）；找不到返回 None。"""
    required = {_cell_name(h) for h in required_headers if _cell_name(h)}
    if not required:
        return None
    for index, row in enumerate(rows[:MAX_HEADER_SCAN_ROWS]):
        if required <= {_cell_name(cell) for cell in row}:
            return index
    return None


def read_excel_with_header_scan(
    contents: bytes, required_headers: Sequence[str],
) -> tuple[list[dict[str, Any]], int, bool]:
    """按必需列名定位表头行，返回 ``(数据行字典列表, 表头行号(1 基), 是否定位到)``。

    - 定位到 → 那一行当表头，它上面的行（报表标题之类）全部丢弃，第三个返回值为 ``True``
    - 定位不到 → 仍按「第 1 行是表头」建键、第三个返回值为 ``False``（ADR-0038 的 AF7 修订）：
      调用方据此报「前 N 行里找不到表头行、去核对那两个列名」，而不是那句会让人去文件里
      找并不缺的列的「缺少必要的列」
    - 空工作簿 / 没有活动表 → 返回 ``([], 1, False)``，由调用方决定自己的空文件文案
      （空文件必须先报「文件为空」：对它说「找不到表头」同样是误导）
    """
    workbook = load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        all_rows = list(sheet.iter_rows(values_only=True)) if sheet else []
    finally:
        workbook.close()

    header_index = _locate_header_index(all_rows, required_headers)
    located = header_index is not None
    if header_index is None:
        header_index = 0

    result: list[dict[str, Any]] = []
    for raw_row in all_rows[header_index + 1 :]:
        if all(cell is None for cell in raw_row):
            continue
        row_dict: dict[str, Any] = {}
        for i, cell in enumerate(raw_row):
            if i >= len(all_rows[header_index]):
                break
            name = _cell_name(all_rows[header_index][i])
            if name:
                row_dict[name] = cell
        if row_dict:
            result.append(row_dict)
    return result, header_index + 1, located


async def aread_excel_with_header_scan(
    contents: bytes, required_headers: Sequence[str],
) -> tuple[list[dict[str, Any]], int, bool]:
    """``read_excel_with_header_scan`` 的异步版本：openpyxl 解析（zip 解压 + 逐单元格构建）是
    纯同步 CPU + 内存操作，大文件会在事件循环里独占数秒，async 调用点必须走本方法。
    """
    return await asyncio.to_thread(read_excel_with_header_scan, contents, required_headers)
