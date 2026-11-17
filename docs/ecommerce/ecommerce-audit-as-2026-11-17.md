# AS 轮复核 · 店铺利润开发前（2026-11-17）

> **编号约定**：本轮前缀 `AS`。`AS缺N` 是 AR 轮没有的新缺陷，`AS-N` 是需要业务方拍板的新问题。
> AR 轮那 32 条缺陷与 AR1–AR9 **不重新编号、不重复描述**，本文只写「复核结果 + 增量」。
> 底盘（AR 轮原文与九问）：[ecommerce-audit-ar-2026-11-08.md](./ecommerce-audit-ar-2026-11-08.md)。设计：[ecommerce-profit-phase1-design.md](./ecommerce-profit-phase1-design.md)。
>
> **用法**：写利润公式的人只需要读本文 §1（能信什么）、§2（会静默改钱的是什么）、§4（分母是多少）。

## 0. 复核口径

- 库里数字：2026-11-17 在 dev 库 `realsexy_admin` 只读探针测得（脚本在 `%TEMP%`，事务外纯 SELECT，不写一行）。
- 代码：AR 轮 32 条逐条重读，结论 **29 条仍在 / 0 条已修 / 3 条不成立**；其中三条最重的我亲自复量（AR缺1 分销 0 行、AR缺3 空编码 538 行、AR缺20 索引 0 个）。
- 另加两路 AR 轮没扫的面：前后端契约（§5）、测试护栏（§3）。

---

## 1. 可以放心用的三件事（今天实测 0 笔不符）

1. **主订单的五个钱袋子 = 子订单逐行求和**。`buyer_paid_amount` / `refund_amount` / `shipped_cost_amount` / `return_cost_amount` / `freight_cost_amount` 与明细求和 **3743 张主单全部相符**（AR §5 当时量的是 4 把尺，本轮补上第 5 把 `freight_cost_amount`，也 0 不符）。→ 利润按主单聚合列取数，不需要自己重扫明细。
2. **明细侧的成本列自洽**。`computed_product_cost` / `computed_return_cost` 与「单价×件数后**见位进分**」的结果 **5524 行 0 笔不符**（导入侧 [order/service.py:457-458](../../backend/app/plugin/module_ecommerce/order/service.py) 与更新侧 [order/service.py:721-722](../../backend/app/plugin/module_ecommerce/order/service.py) 用的是同一个 `_ceil_cent`，两条写路径一把尺）。→ 「成本改了但 computed 没跟着重算」这个担心可以排除。
3. **运费一分不漏、也不重**。账单三项里 `waybill_fee` 合计 3,852.85 + `bill_other_fee` 合计 1,896.60 **正好等于** `actual_freight` 合计 5,749.45（`actual_freight` 的定义就是「面单费+其它费用」，见 [logistics/model.py:77-79](../../backend/app/plugin/module_ecommerce/logistics/model.py)），而摊到主订单上的 `freight_cost_amount` 合计也是 5,749.45。→ **不要**把 `actual_freight` 与另两列相加，那是重复计费。

---

## 2. 会静默改钱的（利润公式必须显式表态）

| # | 事实 | 对利润的方向性后果 |
|---|---|---|
| AR缺1 | 子订单从非分销刷成分销时不动已摊好的 `freight_cost`，主单求和却按全部子订单算 | 今天 **0 行分销**，一分钱没脏；分销一进来就是必炸 |
| AR缺2 | `tracking_no` / `is_erp_shipped` / `main_sku_code` / `sku_code` 四格「更新订单」永不刷新，成本却按文件里的新编码回查 | 落库的 `cost_price` 与库里的编码对不上，两轮数互相解释不了 |
| AR缺3 | 主编码被刷成空 → `cost_price` NULL、发货成本 0.00、`cost_missing` 0 | 「没编码就白拿」，且不进待补充清单。今天实测：`cost_price` NULL 3142 行里，`cost_missing` 没标的 538 行 **全部**是空主编码（`有编码却没标 = 0 行`，即存量口径没错、只有增量会漏） |
| 本轮新增 | 我们的发货成本与快麦报的 `erp_shipped_cost` **1187 行不一致**，其中我们比快麦**低 1125 行**、只高 62 行 | 我们的单价来自成本主表当期值、快麦是发货时点值。**利润读哪一个必须先写死**，否则同一份月报两个数 |
| 本轮新增 | `ship_qty=0` 却有实付的子订单 **1011 行**（占 18%） | 收入算不算这 1011 行，决定当月收入盘子差多少 |
| 本轮新增 | `return_qty > ship_qty` 的子订单 **20 行** | 退货成本加回超过发货成本，这类行必然出负利润 |
| AR缺20 | 9 个电商模型的 `__table_args__` 写成 dict，把基类 [core/base_model.py:71-77](../../backend/app/core/base_model.py) 的两个复合索引顶掉了 | 今天 0 个复合索引（96 条索引里唯一的多列索引是 `ecommerce_store_tag` 的 PRIMARY）。当前数据量不痛，量级上来列表页全表扫 |

---

## 3. AS 轮新缺陷

### AS缺1 电商域一行 A 类测试都没有，CI 对本域零覆盖
`backend/tests/module_ecommerce/` **目录不存在**（内务有 `backend/tests/module_internal/` 12 份）。本域只有 18 份 B 类轮次脚本 `backend/tests/live/ecommerce/`，CI 不跑。`backend/tests/live/README.md:23` 自己写着「还没建，将来从 live 搬上来」。

- 与 `AGENTS.md`「质量要求」和 `docs/agents/shared-rules.md` §2.5（T1–T8）的口径直接冲突：A 类应住 `backend/tests/module_<域>/` 且 CI 跑这些。
- 现状影响：`order/service.py::_ceil_cent`、`_derive_refund_status`、`_recompute_order_aggregates`、整个 `scam_reconciliation.py`、`logistics` 的 `_apportion` / `_judge_rows` / `_reconcile_bill` 都**只被导入/确认流程间接路过**，没有任何一份 `pytest` 直接钉住它们。利润公式是新写的第 1 个「纯计算」代码，落在一个没有任何回归护栏的域里。
- **处置（待标注）**：

### AS缺2 两条后端路由前端没有任何页面调用
- `POST /ecommerce/order/scam-reconciliation/run`（[order/controller.py:118](../../backend/app/plugin/module_ecommerce/order/controller.py)）——wrapper 在 [api/module_ecommerce/order.ts:164](../../frontend/web/src/api/module_ecommerce/order.ts)，**无 view 调用**。
- `GET /ecommerce/batch/detail/{id}`（[common/import_batch/controller.py:31](../../backend/app/plugin/module_ecommerce/common/import_batch/controller.py)）——wrapper 在 [api/module_ecommerce/batch.ts:46](../../frontend/web/src/api/module_ecommerce/batch.ts)，**无 view 调用**。

- 与 AR8 是同一条链：`is_shua_order` 导入硬编码 `False`、`shua_status` 全库没人写，核对接口又点不到 → §5.4「revenue 剔刷单」现在剔不出任何一行，且**补不进人工标记**。
- **处置（待标注）**：

---

## 4. 底盘数字（2026-11-17，与 AR §5 逐项对照）

**一句话：11-08 到 11-17 这九天，业务数据一行业务性地没动过。** 下面每个钱数都与 AR §5 完全相同。

| 项 | 2026-11-17 | AR §5（11-08） |
|---|---|---|
| 子订单 / 主订单 | 5524 / 3743 | 5524 / 3743 |
| 买家已付 | 315,375.64 | 315,375.64 |
| 退款 | 3,349.08（none 3641 / full 60 / partial 42） | 3,349.08 |
| 发货成本 / 退货成本 | 109,382.82 / 570.40 | 109,382.82 / — |
| 运费成本 | 5,749.45（=账单 `actual_freight` 合计） | 5,749.45 |
| 运费收入 `freight_income` | 3,983.62（明细侧**不落列**，只存主单 → 不可按行重算） | — |
| 待补充成本 `cost_missing=1` | 2604 行 | 2604 行 |
| `cost_price` NULL 全量 | 3142 行（其中 538 行空主编码，不算缺失） | — |
| 缺运费 `freight_cost` NULL | 1815 行；NULL 或 0 合计 1909 行 | 1815 行 |
| 重量 NULL / 运单号 NULL | 586 / 460 | — |
| 账单 | 2302 票，只有 2026-09 一个月，软删 0、重复运单号 0、`store_id` NULL 2 | 2302 票 / 6 个已确认批次 |
| 成本主表 | 1744 行，成本价 NULL 290 | 1744 / 290 |
| 店铺 / 负责人 | 31 家、30 家有单、**15 家无负责人** | 31 / 30 / 15 |
| 分销行 | **0**（ADR-0036 整条例外链路空转） | 0 |
| 刷单 | **0**（`is_shua_order` 全 0、`shua_status` 全 `not_shua`） | 0 |
| 数据来源 | 全 `excel`，快麦 API 源 0 行（`kuaimai_api_source.py` 从未被真实数据走过） | — |
| 软删行 | 主单 0、明细 0、成本主表 0、账单 0 → `is_deleted` 过滤今天不改任何数 | — |
| 月份口径 | `ship_time` 全部 2026-09（NULL 0）；`pay_time` 跨 2026-04～2026-10 | 发货只落 9 月 |

**利润的月份键（AR2）今天还不能从数据里看出来**：全库只有 9 月发货，但支付月铺了 5 个月——一导 10 月，`批次归属月 / ship_time / pay_time` 三个键立刻分家。

---

## 5. 前后端契约（本轮新扫，AR 轮未覆盖）

结论：**0 处路径错、0 处方法错、0 个前端调用没有后端路由**。前缀链核对为 `VITE_APP_BASE_API=/api/v1` + `ROOT_PATH=/api/v1` + `discover.py` 把 `module_ecommerce` 映射成 `/ecommerce`，nginx 不 rewrite。

四处「前端多发、后端 schema 不声明、被 Pydantic 丢掉」的参数，今天全部良性（对应输入框在编辑态是 disabled），但都是给利润页埋的坑：

1. [logistics/components/BillParseRuleTab.vue:331](../../frontend/web/src/views/module_ecommerce/logistics/components/BillParseRuleTab.vue) PUT 带 `bill_type` → `BillParseRuleUpdateSchema` 无此字段。
2. [order/components/SkuCostTab.vue:301](../../frontend/web/src/views/module_ecommerce/order/components/SkuCostTab.vue) PUT 带 `main_sku_code` / `sku_code` → `SkuCostUpdateSchema` 无此字段。
3. `OrderPageQuery extends PageQuery`（[api/module_ecommerce/order.ts:47](../../frontend/web/src/api/module_ecommerce/order.ts)）声称能按 `created_time` / `updated_time` 筛，而 `OrderQueryParam` **故意**不继承 `BaseQueryParam`（[order/schema.py:102](../../backend/app/plugin/module_ecommerce/order/schema.py)）→ 界面一加时间筛就静默失效（同 AR缺15 那一类）。
4. `missingCostList(query: PageQuery)`（同文件 `:154`）同理，而 [order/controller.py:106-110](../../backend/app/plugin/module_ecommerce/order/controller.py) 只收 `PaginationQueryParam`。

---

## 6. AS 轮新增拍板项

### AS1 收入读 `paid_amount` 全量，还是只读已发货行
`ship_qty=0` 但有实付 1011 行、`return_qty>ship_qty` 20 行、`buyer_paid_amount=0` 却背着成本的有 98 单（有发货成本）+ 204 单（有运费成本）。这三类决定利润表会不会出现负数行、以及当月收入盘子。AR6 只问了分母公式，没问行的准入。

- **你的答复（待标注）**：

### AS2 成本读我们的还是快麦的
1187 行两数不一致（我们低 1125）。利润减项只能有一个来源；另一个留着做差异告警。

- **你的答复（待标注）**：
