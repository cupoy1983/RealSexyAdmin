# 语境地图（由工具生成，别手改）

生成命令：`node scripts/adr.mjs index`。域的增删改在 `docs/adr/domains.json`，不要直接编辑本文件。

本仓库有多个业务域，各有自己的术语表与代码领土。**ADR 不分域**：全仓一条连续序列（当前 0001–0042），引用一律写 `ADR-<NNNN>`，号前不加域名。

| 域 | 中文名 | 术语表 | 代码领土 |
|---|---|---|---|
| ecommerce | 电商 | docs/ecommerce/CONTEXT.md | backend/app/plugin/module_ecommerce backend/tests/module_ecommerce backend/tests/live/ecommerce frontend/web/src/views/module_ecommerce frontend/web/src/api/module_ecommerce |
| internal | 内务 | docs/internal/CONTEXT.md | backend/app/plugin/module_internal backend/tests/module_internal frontend/web/src/views/module_internal frontend/web/src/api/module_internal |
| system | 全局 | docs/system/CONTEXT.md | （不设领土） |

当前最大号 ADR-0042（只数已写定的决议，本地未填的空壳不进本文件）。写决议**不走工具发号**：向业务方要号段（起止都由他报），你照这两个号建 `NNNN-占位.md` 空壳、之后直接改那一份文件的标题、状态、正文，号不动。引用一律写 `ADR-<NNNN>`，号前不加域名。
