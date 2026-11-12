# AR 轮审计报告 · 利润模块开工前（2026-11-08）

审计范围：`backend/app/plugin/module_ecommerce/` 全部业务代码 + `frontend/web/src/views/module_ecommerce/` + `docs/ecommerce-profit-phase1-design.md`（§1–§27）+ `docs/adr/0019–0039`（审计当时那 21 份还叫 `docs/adr/ecommerce/0001–0021`，2026-11-12 统一化后平铺并各加 18）+ `CONTEXT.md`。

方法：五路子代理并行扫（约 40 条疑点）→ 逐条亲自读码复核 → 9 个只读探针（全部事务内 `rollback`，脚本在 `%TEMP%\probe_ar_*.py`）。子代理结论中 **3 条被推翻、4 条改了级别**，单独记在 §6，不进缺陷清单。

**编号约定**：本轮前缀 `AR`。`AR缺N` 是缺陷，`ARN` 是需要业务方拍板的问题。已定决议一律不重编号。

**用法**：每条最后的「处置」一行是给业务方标注的，可写 `修` / `不修（理由）` / `挂账` / `待定`。填完后代理按标注分批实施，实施完在同行追加落地位置。

---

## 1. 会动钱的缺陷（8 条）

### AR缺1 分销翻转不联动运费
子订单从「非分销」刷成「分销」时，[order/service.py:693-738](../../backend/app/plugin/module_ecommerce/order/service.py) 只重算 `cost_price` 与两个 `computed_*`，**不动已经摊好的 `freight_cost`**，也不重算主订单 `freight_cost_amount`。而账单侧取候选子订单时永远过滤掉分销行（[logistics/service.py:494-499](../../backend/app/plugin/module_ecommerce/logistics/service.py)），那笔运费就永久留在一个不再被任何链路认领的格里；主订单求和又是按「全部子订单」算的（[logistics/service.py:921-936](../../backend/app/plugin/module_ecommerce/logistics/service.py)），所以它照样进利润减项。

- 现状影响：今天 **0 行分销**（探针 E1），一分钱还没脏；分销数据一进来就是必炸。
- **处置（待标注）**：

### AR缺2 「更新订单」不刷新四个关键字段，成本却按新字段算
`_UPDATE_DIFF_FIELDS`（[order/service.py:113-123](../../backend/app/plugin/module_ecommerce/order/service.py)）只有 9 项，`tracking_no` / `is_erp_shipped` / `main_sku_code` / `sku_code` **永不刷新**。但成本回查用的是**文件里**的编码（[order/service.py:718](../../backend/app/plugin/module_ecommerce/order/service.py)），于是落库的 `cost_price` 来自一个新编码，库里存的编码还是旧的——这一行的成本和它自己的编码对不上，下一轮「待补充成本」按旧编码去查主表，两轮的数互相解释不了。

- 单号变 → 账单永远匹配不上，只能靠人工认领；发货状态变（未发货→ERP发货）→ 该行不参与运费摊分，运费少摊。
- **处置（待标注）**：

### AR缺3 主编码刷成空 → 成本 0、且不进待补充清单
[order/service.py:732](../../backend/app/plugin/module_ecommerce/order/service.py)：`new_missing = bool(d.main_sku_code) and fresh_cost is None`。主编码一旦被清空，判据直接 `False` → `cost_price` NULL、发货成本 0.00、`cost_missing` 0，系统宣称这条不缺数。等于「没编码就白拿」。

- 与 11-08 定性的 AN32 那 15 行同源（存量已定为不管），**但这条是增量**：业务方改一次编码就新造一条。
- **处置（待标注）**：

### AR缺4 NaN 通过两道数值闸
`Decimal("nan")` 构造不报错，`quantize_half_up` 照样返回 NaN，于是解析阶段放行：
- 订单侧 [excel_source.py:150-171](../../backend/app/plugin/module_ecommerce/order/importers/excel_source.py)
- 账单侧 [generic_parser.py:71-82](../../backend/app/plugin/module_ecommerce/logistics/parsers/generic_parser.py)

之后在**比较**或**落库**时抛未捕获异常 → HTTP 500，报错里带 SQL 原文（框架 `base_crud.py:218` 会把异常原文包进消息）。`inf` / `-inf` / `1e999` 反而已经被 `InvalidOperation` 挡住了。

- 实测修正：不会静默变 0，是当场崩。设计文档 §26.3:1267 写了「仍补 NaN/Inf 闸」，**代码里没有**（另见 AR缺23）。
- **处置（待标注）**：

### AR缺5 件数解析：截断 + 未捕获的 OverflowError
[excel_source.py:174-184](../../backend/app/plugin/module_ecommerce/order/importers/excel_source.py) 用 `int(float(text))`：`2.9` 件 → **截断成 2**；`1e999` → `OverflowError`，而 `except` 只接 `(ValueError, TypeError)` → 500。

- 件数是运费摊分的权重、也是成本的乘数，截掉就是钱。实测库内非整数件数 0 行（快麦件数目前确实是整数），所以是**闸缺失**而不是现行脏数据。
- **处置（待标注）**：

### AR缺6 表头重名列后者静默覆盖
[excel_head.py:80](../../backend/app/plugin/module_ecommerce/common/excel_head.py) 建键时 `row_dict[name] = cell`，同名列写第二次就把第一次盖掉。快麦导出偶有「买家已付金额」出现两列（含税/不含税）的情况，撞上就是整份金额错位且**一声不响**。

- 订单导入、账单导入、成本导入三条路径共用这一处。
- **处置（待标注）**：

### AR缺7 退款状态判据的分母含运费收入
[order/service.py:97-103](../../backend/app/plugin/module_ecommerce/order/service.py) 拿 `buyer_paid_amount` 比退款，而这一列按模型注释**含运费收入**（[order/model.py:24](../../backend/app/plugin/module_ecommerce/order/model.py)）→ 只要收过运费，整单退款最多判成「部分退款」。

- 现在只影响 `refund_status` 这一格展示，不动钱；**但利润若按 `refund_status` 挑整单退款就会漏**。
- **处置（待标注）**：

### AR缺8 认领改对后，账单行的 `store_id` 恒为 NULL
`store_id` 只在导入那一刻写（[logistics/service.py:477](../../backend/app/plugin/module_ecommerce/logistics/service.py)：`details[0].order.store_id if original ...`），认领改对运单号那条链路（[logistics/service.py:574+](../../backend/app/plugin/module_ecommerce/logistics/service.py)）不回填。

- 探针 D6 实测：**现存 2 行 `store_id = NULL`**，正是那 2 条已恢复行。账单按店铺筛会漏这 2 票。
- **处置（待标注）**：

---

## 2. 会让人读错数 / 流程走不通（5 条）

### AR缺9 归属月对「读不出的时间」静默放行
`_parse_datetime`（[order/service.py:68-83](../../backend/app/plugin/module_ecommerce/order/service.py)）认不出的写法返回 `None`；月份校验里 `if st` 把它滤掉（[order/service.py:330](../../backend/app/plugin/module_ecommerce/order/service.py)）→ **全单时间都读不出的订单不参与月份校验**，落进用户随手选的那个月，且 `ship_time` 为空。docstring 自己承认「跳过」。

- 后果：这批行在利润里按「发货月」归集时无处可放（§5.4 运费按发货月）。
- **处置（待标注）**：

### AR缺10 `order_count` 不随删除递减（潜在）
`delete_bill_rows`（[logistics/service.py:510-535](../../backend/app/plugin/module_ecommerce/logistics/service.py)）只减 `row_count`，票数那一格不动。

- 探针 D1 逐批核对：**当前 6 个账单批次全吻合、0 漂移**——因为还没有票被删过。改判为「潜在」，不是现行缺陷。
- **处置（待标注）**：

### AR缺11 批次确认时间写 naive 本地时间，同行 `created_time` 写 UTC
[import_batch/service.py:188](../../backend/app/plugin/module_ecommerce/common/import_batch/service.py) 用 `datetime.now()`，框架 `base_model.py:104` 用 `datetime.now(UTC)`，列却是 `DateTime(timezone=True)`。探针 C1 实测值形如 `2026-10-29 22:09:35`——差 8 小时语义。

- **处置（待标注）**：

### AR缺12 两处留痕时间精度不一致
账单侧 `%H:%M`（[logistics/service.py:594](../../backend/app/plugin/module_ecommerce/logistics/service.py)）vs 订单侧 `%H:%M:%S`（[order/service.py:589](../../backend/app/plugin/module_ecommerce/order/service.py)）。同一张表里两种粒度，人工比对留痕时会误判先后。

- **处置（待标注）**：

### AR缺13 `list_for_month` 全库 0 个调用方
[import_batch/service.py:91](../../backend/app/plugin/module_ecommerce/common/import_batch/service.py)（grep 全仓确认无人调用）。死方法——但它恰好是利润「按月取批次」正要用的那把钥匙。

- 建议：别删，改成利润模块的地基（另见 AR7）。
- **处置（待标注）**：

---

## 3. 配置与环境地雷（7 条）

### AR缺14 数据权限一开就炸自动建档，并把 SQL 原文回显
非超管账号走 `ensure_store` 时，框架按 `created_id == user.id` 兜底过滤（`app/core/permission.py:62-64`、`app/core/base_crud.py:321-325`）→ 查不到别人建的店 → 走 `create`（[store/service.py:113 → :126](../../backend/app/plugin/module_ecommerce/store/service.py)）→ 撞 `kuaimai_shop_id` 唯一索引 → `base_crud.py:218` 把异常原文包成 `创建失败: {SQL...}` 回显给前端。

- 框架文件按约定不动。对策只能在业务侧：把「电商模块用超管账号跑」抬成硬约束写进文档，或给 `ensure_store` 换一条不带权限过滤的取店路径。
- **处置（待标注）**：

### AR缺15 空串筛选静默失效
`_build_conditions` 对 `""` 直接 `continue`（`base_crud.py:327-329`）→ 店铺列表传 `kuaimai_shop_id: ""` 等于没传，返回全量。框架文件，不动，记进文档。

- **处置（待标注）**：

### AR缺16 「编码 0」例外是精确字符串比对
[store/service.py:115](../../backend/app/plugin/module_ecommerce/store/service.py) 比的是 `kuaimai_shop_id == OUTBOUND_SHOP_CODE`；导入侧 `_to_str_or_none` 只做 `str(v).strip()`（[excel_source.py:187-191](../../backend/app/plugin/module_ecommerce/order/importers/excel_source.py)）。快麦那一格若哪天被存成数值 `0.0`，读出来是 `"0.0"` → 例外失效 → 按 `"0.0"` **新建一家没挂负责人的店**，那批出库单从此不进利润。

- **处置（待标注）**：

### AR缺17 `batch_set_tags` 不带 `tag_ids` 就清空全部负责人
[store/service.py:176-181](../../backend/app/plugin/module_ecommerce/store/service.py) 无条件整替，而 schema 里那一格非必填（[store/schema.py:42](../../backend/app/plugin/module_ecommerce/store/schema.py)）。前端少传一个字段 = 静默摘掉一批店的负责人 = **一批店整家掉出利润**。

- **处置（待标注）**：

### AR缺18 `TagService.update` 少了 `exclude_none`
[:263](../../backend/app/plugin/module_ecommerce/store/service.py) 只用 `exclude_unset`，店铺那侧 [:159-163](../../backend/app/plugin/module_ecommerce/store/service.py) 两把都用了 → 弹窗里备注留空提交会把已有备注清成 NULL。

- **处置（待标注）**：

### AR缺19 负责人重名的两道闸判据不同
Python 侧精确比字符串（[:241-251](../../backend/app/plugin/module_ecommerce/store/service.py)），数据库是大小写不敏感唯一索引（[store/model.py:31](../../backend/app/plugin/module_ecommerce/store/model.py)）→ 建「张三」撞已有「zhangsan/张三」变体时绕过友好报错，直接 500。

- **处置（待标注）**：

### AR缺20 9 个电商模型把框架的两个复合索引顶掉了
每个电商模型的 `__table_args__` 都写成 `dict`（如 [store/model.py:11](../../backend/app/plugin/module_ecommerce/store/model.py)、[order/model.py:15](../../backend/app/plugin/module_ecommerce/order/model.py)），而框架 `base_model.py:71-77` 的 `declared_attr` 返回的是 `Index(...)` 元组——子类别名一被覆盖，`ix_{table}_status_deleted` / `ix_{table}_created_deleted` 就不建了。

- 现在数据量小无感；利润页一按月扫全表会显形。
- **处置（待标注）**：

### AR缺21 门禁里根本没有「店铺/负责人」这一条
`assert_bill_batches_confirmed`（[import_batch/service.py:160-184](../../backend/app/plugin/module_ecommerce/common/import_batch/service.py)）只查同月账单批次齐全已确认 + 启用解析规则，**没有任何 store/tag 检查**。

- 这条从「现有缺陷」**改判为「待建」**：它本来是给利润写的闸门（§5.5 第 4 条），还没开始实现不算坏。但文档三个版本必须先对齐（见 AR缺24）。
- **处置（待标注）**：

---

## 4. 文档与实现不一致（11 条，改哪边都要先定）

| # | 冲突 | 文档说的 | 代码做的 | 处置（待标注） |
|---|---|---|---|---|
| AR缺22 | 「经营类型」列 | §26.2:1252「类型这一列照建」；AN4/AN5/AN7 三处过滤全靠它 | `ecommerce_tag` 只有 `tag_name/description/status`（[store/model.py:23-37](../../backend/app/plugin/module_ecommerce/store/model.py)）；§4:48 的表结构反倒按「只有三列」写——**文档两处口径相反，代码站在错的那一边** | |
| AR缺23 | 垃圾行跳过 | §4:123 与 §5.1:154 写「跳过垃圾行（编码=0/¥0）」 | 除 `汇总` 行外**没有任何跳行逻辑**（[excel_source.py:272-276](../../backend/app/plugin/module_ecommerce/order/importers/excel_source.py)）；编码 0 早在 AN34 撤销 | |
| AR缺24 | NaN/Inf 闸 | §26.3:1267「仍补 NaN/Inf 闸」 | 没做（＝AR缺4） | |
| AR缺25 | 结算门禁条数 | §5.5:233 四条 / ADR-0026 两条 / CONTEXT 三条 | 三个版本互相不一致，代码只有账单侧那两条 | |
| AR缺26 | 待补充清单数字 | §11:383「2624 行 = 63 + 2561」 | 实测当前 `cost_missing=1` 是 **2604 行**（11-06 删 43、11-07 落 9 之后） | |
| AR缺27 | 分摊分母的列 | §5.3:223-230 用 `actual_revenue` 占比 | 模型里没有 `actual_revenue`，只有 `buyer_paid_amount/refund_amount/freight_income`（[order/model.py:24-30](../../backend/app/plugin/module_ecommerce/order/model.py)） | |
| AR缺28 | `freight_income` 的落点 | §5.4 说「仅统计展示」 | §4:142-143 的 profit 表结构里没有它，「指定归属成本」也没有落列 | |
| AR缺29 | 认领月份窗口 | §22.3 的旧闸表 | §26.6 已改成「往前一档」，旧表未更新 | |
| AR缺30 | 批次备注例句 | `ImportBatchModel.remark` 注释举例「改动了 order-2026-09，需重算利润」（[import_batch/model.py:51](../../backend/app/plugin/module_ecommerce/common/import_batch/model.py)） | 写这句话的代码在（[logistics/service.py:938-951](../../backend/app/plugin/module_ecommerce/logistics/service.py)），**读它的利润页不存在**——提示是给人看的，没有重算入口 | |
| AR缺31 | 利润重算通知 | §11 挂账「迟到账单的自动通知与自动重算」 | `_notify_profit_recalc` 是空方法（只有 docstring 的 TODO，[logistics/service.py:953-958](../../backend/app/plugin/module_ecommerce/logistics/service.py)），却在确认流程里被真实调用（[:867](../../backend/app/plugin/module_ecommerce/logistics/service.py)） | |
| AR缺32 | 负数量闸 | §5.1:159 承诺闸住负数量 | `_to_int` 不做符号判定（＝AR缺5）。实测库内负重量 0 行、非整数件数 0 行——**文档说的比代码做的多** | |

---

## 5. 库内现状数字（利润模块的底盘，2026-11-08 实测）

| 项 | 值 |
|---|---|
| 订单批次 | 只有 1 个：`order-2026-09`，**状态＝未确认**，5524 子订单行 / 3743 主订单 |
| 账单批次 | 6 个，**全部已确认**，2302 票；批次记的行数/票数与实际逐批吻合 |
| 买家已付合计 | 315,375.64（交易成功 3451 单 / 284,085.99；交易关闭 174 / 7,698.79；已发货 118 / 23,590.86） |
| 退款合计 | 3,349.08 |
| 发货成本合计 | 109,382.82 |
| 运费成本合计 | 5,749.45 —— 账单应付、`actual_freight` 求和、子订单摊分**三个数一模一样** |
| 分销订单行 | **0 行**（ADR-0036 整条例外链路现在空转） |
| 待补充成本 | `cost_missing=1` 共 **2604 行 / 已付 145,482.14** |
| 缺运费 | `freight_cost IS NULL` 1815 行；其中 **ERP发货且有运单号 609 行**——这 609 票账单里根本没有，运费至今不在系统内 |
| 账单状态 | matched 1301 / weight_province 999 / restored 2；fake 0、duplicate 0 |
| 异常票的钱 | 仅 SKU 重量缺失 552 票 1,313.30；仅超重 435 票 1,415.05；其它 12 票 22.20 —— 都是「只标记，钱照算」 |
| 店铺 / 负责人 | 31 家活店、30 家有单、停用且有单 0 家；负责人**只有 2 个**（负责人甲 14 店、负责人乙 2 店） |
| **AN7 会整家跳过的盘子** | 无负责人 **15 家店 / 1789 单 / 已付 173,561.39 ＝ 全部收入的 55%**；有负责人 15 家 / 1954 单 / 141,814.25 |
| 成本主表 | 1744 行，其中成本价 NULL 290 行、商品级（无规格）152 行，全部 `cost_source=import` |
| 聚合一致性 | 主订单 vs Σ子订单：成本 / 运费 / 已付 / 退款 **4 把尺 0 笔不符** |
| 月份分布 | 全库 `ship_time` 只落在 2026-09 一个月，NULL 0 行；跨月主订单 **0 张**；运单号跨批次重复 **0 票** |

---

## 6. 被推翻 / 改级别的子代理结论（不进上面的清单）

1. ❌ **店铺 `update()` 带 `tag_ids` 会 `MissingGreenlet` 500（子代理标高）**——探针实测：`preload` 后 `tags` 已在 `__dict__`，`refresh` 后是 `InstrumentedList`，`clear()` 两次都不报错。**假**。
2. ⬇️ **NaN 会让「超重判据恒不成立 / 份额全变 NaN 落 0」**——直接测出 `nan > 1`、`s <= 0` 抛 `InvalidOperation`，真结果是当场 500，不脏数据；`Inf`/溢出本来就被挡。级别从「高·动钱」降到「中·报错难看」（仍进 AR缺4）。
3. ⬇️ **省份归一只剥一层后缀会大量误判异常（[generic_parser.py:39-45](../../backend/app/plugin/module_ecommerce/logistics/parsers/generic_parser.py)）**——实测订单侧带「市」的省份 **0 行**，999 异常票里只有 **12 票**是「仅省份不符」，且这 12 票查不出订单侧带市的写法。口径确实错，现状影响≈0，降为文档级。
4. ⬇️ **`order_count` 不递减**——当前 0 漂移，改成「潜在」（AR缺10 已按此措辞）。

---

## 7. 利润模块开工前必须拍板（AR1–AR9）

### AR1 经营类型列
加在 `ecommerce_tag`（AN1 已定），两个值（自营 / 仓储代发）。**要问的一句**：tag 上没选类型 = 不进利润，还是按自营兜底？AN7 只回答了「没挂负责人的店」，没回答「挂了负责人但类型空着」。

- **你的答复（待标注）**：

### AR2 利润的「月」到底是哪个月
订单批次归属月 / 子订单自己的 `ship_time` / 账单批次出账月——今天三者完全重合（全库只有 9 月），10 月一导就分家。这决定利润页那一行的键。

- **你的答复（待标注）**：

### AR3 缺账单的 609 行（运费至今不在系统里）
这个月利润按「运费 = 5749.45」出，还是先显式挂一笔「N 票未入账，运费待补」？前者利润偏高。

- **你的答复（待标注）**：

### AR4 2604 行缺成本 / 14.5 万已付
按 0 成本算出虚高利润，还是先报「成本完备率」把当月标成不可用？（§11 本来就挂着「利润页对未确认批次的表现」这一条）

- **你的答复（待标注）**：

### AR5 15 家店 / 17.36 万（55%）整家跳过
这是数据缺口不是代码缺口，但利润页一上线就是这个数。要不要在页顶列出「被跳过的店铺与金额」，把缺口摆在明面上？

- **你的答复（待标注）**：

### AR6 分摊分母 `actual_revenue` 是哪来的数
= 已付 − 退款 吗？代发店排除（AN4）后分母要不要重算？「指定归属成本」落哪张表哪一列？（现在无处落，见 AR缺27/28）

- **你的答复（待标注）**：

### AR7 利润算完存不存
每查每算，还是落一张 `ecommerce_profit`（月 × 店铺 × 负责人）？这决定重算入口，以及批次 remark 里那句「需重算利润」有没有落地（AR缺30/31、AR缺13）。

- **你的答复（待标注）**：

### AR8 刷单
`is_shua_order` 导入硬编码 `False`（[excel_source.py:326](../../backend/app/plugin/module_ecommerce/order/importers/excel_source.py)）、`shua_status` 全库无人写 → §5.4「revenue 剔刷单」现在剔不出任何一行。先按「0 刷单」算，还是先补人工标记入口？

- **你的答复（待标注）**：

### AR9 现在就把 `order-2026-09` 点确认吗
门禁的三条（同月账单批次齐全已确认 + 启用解析规则）实测已经过得去；不确认则利润页永远 0。建议确认，让利润模块一开始就拿真数验。

- **你的答复（待标注）**：

---

## 8. 建议实施顺序

1. 先修 **AR缺1 / AR缺2 / AR缺3**（这三条一旦有数据就是静默改钱），顺手带 AR缺4 / AR缺5 / AR缺6 的闸。
2. 同期把 **AR缺22–AR缺32 的文档冲突**一次性改掉——利润设计要引用 §5.3 / §5.4 / §5.5，带着错口径写下去会把错的东西实现出来。
3. 拍板 **AR1–AR9**，再动 `cost` / `profit` 两个子域的代码。
4. AR缺14 / AR缺15 属框架侧，按约定本轮不动，只把约束写进文档。

**提醒**：第一期全部产出（`CONTEXT.md`、`module_ecommerce/` 前后端、`docs/`、17 个 alembic 版本）**至今仍未提交**，git 那边按约定归业务方。利润模块一旦动模型，回不去的就是这一大片。
