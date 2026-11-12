# 订单数据源可插拔，第一期默认 Excel

订单入口抽象为可插拔的 OrderDataSource 接口；第一期默认实现是**快麦 ERP 导出的 Excel**，同时实现快麦 API 客户端（app_secret 存 .env、access_token 自动刷新缓存 Redis）但**不作为默认数据源**。先 Excel 后 API，是因为 API 通路（含钉钉收款审批 app）尚在申请、稳定性未验证；用接口隔离数据源，等 API 就绪可切换而不改动下游成本/利润逻辑。
