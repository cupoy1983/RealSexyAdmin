# 语境地图（由工具生成，别手改）

生成命令：`node scripts/adr.mjs index`。域的增删改在 `docs/adr/domains.json`，不要直接编辑本文件。

本仓库有多个业务域，各有自己的术语表与代码领土。**ADR 不分域**：全仓一条连续序列（当前 0001–0039），引用一律写 `ADR-<NNNN>`，号前不加域名。

| 域 | 中文名 | 术语表 | 代码领土 |
|---|---|---|---|
| ecommerce | 电商 | CONTEXT.md | backend/app/plugin/module_ecommerce backend/tests/live/ecommerce frontend/web/src/views/module_ecommerce frontend/web/src/api/module_ecommerce |
| internal | 内务 | （未建） | backend/app/plugin/module_internal backend/tests/module_internal frontend/web/src/views/module_internal frontend/web/src/api/module_internal |
| system | 全局 | （未建） | （不设领土） |

新建 ADR：`node scripts/adr.mjs new <标题>`（下一个号是 0040）。**不要手填编号，也不要在号前加域名。**
