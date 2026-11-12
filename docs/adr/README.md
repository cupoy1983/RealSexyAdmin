# ADR 索引（由工具生成，别手改）

生成命令：`node scripts/adr.mjs index`。新建 ADR 用 `node scripts/adr.mjs new <标题>`，不要手填编号。

**号全仓唯一，一条连续序列。** 2026-11-12 统一化后 ADR 全部平铺在本目录，引用在任何位置都只写 `ADR-<NNNN>`，号前不加域名。
业务域仍登记在 `domains.json`，但它管的是术语表与代码领土在哪（见根目录 `CONTEXT-MAP.md`），不管编号。

| 号 | 标题 | 状态 | 文件 |
|---|---|---|---|
| ADR-0001 | 外部账号绑定采用统一一对一模型 | （未标） | [0001-外部账号绑定采用统一一对一模型.md](./0001-外部账号绑定采用统一一对一模型.md) |
| ADR-0002 | 考勤结果以钉钉智能月报为准，不再自行计算 | （未标） | [0002-考勤结果以钉钉智能月报为准.md](./0002-考勤结果以钉钉智能月报为准.md) |
| ADR-0003 | 考勤同步走系统节点任务，并保留代码块执行开关 | （未标） | [0003-考勤同步走系统节点任务并保留代码块执行.md](./0003-考勤同步走系统节点任务并保留代码块执行.md) |
| ADR-0004 | 考勤日明细落库，表统一 IAC_ 前缀 | 已接受（2026-10-28） | [0004-考勤日明细落库与报表同款展示.md](./0004-考勤日明细落库与报表同款展示.md) |
| ADR-0005 | 薪资考勤 Tab 化，内务中心模块迁到 plugin | 已接受（2026-10-28） | [0005-薪资考勤Tab化与内务中心迁至plugin.md](./0005-薪资考勤Tab化与内务中心迁至plugin.md) |
| ADR-0006 | 内务中心触碰的框架文件清单与批准记录 | （未标） | [0006-内务中心触碰的框架文件清单.md](./0006-内务中心触碰的框架文件清单.md) |
| ADR-0007 | 删除 OA 用户联动钉钉离职 | （未标） | [0007-删除用户联动钉钉离职.md](./0007-删除用户联动钉钉离职.md) |
| ADR-0008 | 考勤组剔除与考勤同步互斥 | （未标） | [0008-考勤组剔除与考勤同步互斥.md](./0008-考勤组剔除与考勤同步互斥.md) |
| ADR-0009 | 公司休息日重算考勤 | （未标） | [0009-公司休息日重算考勤.md](./0009-公司休息日重算考勤.md) |
| ADR-0010 | 出勤天数不含请假 | （未标） | [0010-出勤天数不含请假.md](./0010-出勤天数不含请假.md) |
| ADR-0011 | 薪资计算与工资条规则 | （未标） | [0011-薪资计算与工资条规则.md](./0011-薪资计算与工资条规则.md) |
| ADR-0012 | 考勤、绩效、工资时点与钉钉通知 | （未标） | [0012-考勤绩效工资时点与钉钉通知.md](./0012-考勤绩效工资时点与钉钉通知.md) |
| ADR-0013 | 系统用户新增与编辑的必填项及角色边界 | 已确认，已实施并验证 | [0013-用户新增编辑必填与角色边界.md](./0013-用户新增编辑必填与角色边界.md) |
| ADR-0014 | 公司休息日考勤组方案（休息 / 补工资） | 已确认，已实施并验证 | [0014-仓库考勤组休息日补贴.md](./0014-仓库考勤组休息日补贴.md) |
| ADR-0015 | （已废止）：关闭公开注册与第三方登录「先绑定、后登录」 | **已废止**；A3 / A5 相关代码已于 2026-11-07 / 2026-11-08 全部回退 | [0015-关闭公开注册与第三方登录先绑定.md](./0015-关闭公开注册与第三方登录先绑定.md) |
| ADR-0016 | 离职结算单 | 设计已拍板，文档先行；2026-11-09 代码与回归测试已落地（实施口径见第九节）。 | [0016-离职结算单.md](./0016-离职结算单.md) |
| ADR-0017 | 考勤月报历史口径与业务日历统一 | 设计已拍板，文档先行；2026-11-09 代码与回归测试已落地。 | [0017-考勤月报历史口径与业务日历.md](./0017-考勤月报历史口径与业务日历.md) |
| ADR-0018 | 考勤确认与工资条生成锁定 | 2026-11-10 杜总拍板并落地（复核报告功能缺陷 3）。 | [0018-考勤确认与工资条生成锁定.md](./0018-考勤确认与工资条生成锁定.md) |
| ADR-0019 | 电商利润模块以插件形式落位 | （未标） | [0019-ecommerce-as-plugin-module.md](./0019-ecommerce-as-plugin-module.md) |
| ADR-0020 | 店铺利润是净利，含间接成本分摊 | （未标） | [0020-store-profit-is-net-with-allocation.md](./0020-store-profit-is-net-with-allocation.md) |
| ADR-0021 | 不做数据隔离，全团队共享 | （未标） | [0021-no-data-isolation-team-shared.md](./0021-no-data-isolation-team-shared.md) |
| ADR-0022 | 商品成本以系统 SKU 表为唯一事实来源 | （未标） | [0022-product-cost-single-source-of-truth.md](./0022-product-cost-single-source-of-truth.md) |
| ADR-0023 | 用 NULL 与 0 区分「缺失成本」和「有意0成本」 | （未标） | [0023-null-vs-zero-cost-semantics.md](./0023-null-vs-zero-cost-semantics.md) |
| ADR-0024 | 店铺从订单数据自动建档，不预配映射表 | （未标） | [0024-store-auto-provision-from-order-data.md](./0024-store-auto-provision-from-order-data.md) |
| ADR-0025 | 运费按子订单发货月归属，迟到账单重算已锁定月 | （未标） | [0025-freight-by-ship-month-recompute-locked.md](./0025-freight-by-ship-month-recompute-locked.md) |
| ADR-0026 | 结算双门禁：时间到16号0点 且 数据齐全 | （未标） | [0026-settlement-double-gate.md](./0026-settlement-double-gate.md) |
| ADR-0027 | 订单数据源可插拔，第一期默认 Excel | （未标） | [0027-pluggable-order-data-source-default-excel.md](./0027-pluggable-order-data-source-default-excel.md) |
| ADR-0028 | 刷单订单以订单与销量权重审批逐笔核对后排除收入 | （未标） | [0028-scam-order-reconciliation.md](./0028-scam-order-reconciliation.md) |
| ADR-0029 | 账单解析规则存独立表，不用字典 | （未标） | [0029-bill-parse-rule-own-table.md](./0029-bill-parse-rule-own-table.md) |
| ADR-0030 | 订单拆主/子两表，所有订单类型一律计入收入 | （未标） | [0030-order-two-table-split-all-types-count.md](./0030-order-two-table-split-all-types-count.md) |
| ADR-0031 | 订单备注上移主订单级，归并时取第一行 | （未标） | [0031-order-remark-on-header-first-row.md](./0031-order-remark-on-header-first-row.md) |
| ADR-0032 | 订单更新走增量 diff + 更新备注日志，导入拒绝已有订单 | （未标） | [0032-order-update-diff-log-import-rejects-existing.md](./0032-order-update-diff-log-import-rejects-existing.md) |
| ADR-0033 | 导入批次是订单与账单共用的确认单位，未确认不进利润 | （未标） | [0033-import-batch-as-confirmation-unit.md](./0033-import-batch-as-confirmation-unit.md) |
| ADR-0034 | 运费回写进子订单，取消独立匹配表；判重只认原件行 | （未标） | [0034-freight-written-onto-suborder-no-match-table.md](./0034-freight-written-onto-suborder-no-match-table.md) |
| ADR-0035 | 第三方对接归到 `app/plugin/infra`，归层判据是「删掉某个插件它还成立吗」 | （未标） | [0035-infra-package-and-third-party-layering.md](./0035-infra-package-and-third-party-layering.md) |
| ADR-0036 | 分销订单：商品成本的第二个来源，与运费侧的彻底摘出 | （未标） | [0036-distribution-order-cost-and-freight-exception.md](./0036-distribution-order-cost-and-freight-exception.md) |
| ADR-0037 | 账单「非账单行」以账单日期能否解析为判定；对账结果只写批次备注，不设门禁 | （未标） | [0037-bill-non-bill-row-and-reconciliation-remark.md](./0037-bill-non-bill-row-and-reconciliation-remark.md) |
| ADR-0038 | Excel 表头行由业务侧解析器自动定位，不改框架的 `ExcelUtil` | （未标） | [0038-excel-header-row-auto-detect.md](./0038-excel-header-row-auto-detect.md) |
| ADR-0039 | 数值格子在「读文件那一步」就量化到它落库列的精度，方向写死 | （未标） | [0039-numeric-cell-quantized-at-parse.md](./0039-numeric-cell-quantized-at-parse.md) |

> 号段来源：电商 0019–0039 由旧电商号 0001–0021 各加 18 而来；内务 0001–0018 从兄弟 clone RealSexy-OA 合并进来，号未改；全局 不属于任何业务域的决策（框架改动、跨域口径）也直接进 docs/adr/，号同样是全仓往下排。2026-11-12 统一化之前的电商号 = 现号减 18，读那之前的老句子时用。
