# 电商利润模块以插件形式落位

电商店铺利润的全部业务代码放在 `backend/app/plugin/module_ecommerce/`，作为插件被 `DynamicRouterRegistry` 自动发现注册、被 Alembic 自动发现模型，**不改动任何基础框架、系统依赖或原有业务逻辑**。选插件而非核心模块，是因为框架的硬约束就是"只做业务开发、不碰框架"，插件机制恰好让新增域零侵入接入路由与迁移。

> 2026-10-21 收窄：「全部」不再包括外部系统对接。快麦/钉钉/企微客户端与它们的传输机制移到中立基础包 `backend/app/plugin/infra/integration/`，判据是「删掉某个插件它还成立吗」（ADR-0035）。插件内不保留任何对接代码，`infra` 里也不允许放 `controller.py`——路由发现只扫 `module_*/**/controller.py`。这条约定下，这次搬迁碰到的框架文件只有 `app/common/enums.py` 的两条 Redis 键名（去掉 `ecommerce_` 前缀），已单独批准。
