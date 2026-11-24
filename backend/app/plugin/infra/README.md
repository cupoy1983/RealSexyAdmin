# 插件基础设施

供业务插件按需复用，依赖方向为业务插件调用 infra。基础设施不依赖具体业务模块，业务字段、任务、权限及业务路由由各调用方维护。

| 能力 | 入口 |
|---|---|
| 外部系统请求、通行证缓存与适配器 | [integration/http.py](./integration/http.py) 及同目录的系统客户端 |
| Crawlee＋Playwright 通用运行层、浏览器连接、访问节奏、冷却、游客会话及快照／交互原语 | [crawler 接入与维护说明](./crawler/README.md) |

沿用 [ADR-0035：第三方对接归到 `app/plugin/infra`，归层判据是「删掉某个插件它还成立吗」](../../../../docs/adr/0035-infra-package-and-third-party-layering.md) 中的归层原则：删除某个业务插件后仍成立的机制放在 infra，业务翻译留在插件内。基础设施不放 controller，按需导入也不自动启动后台任务。
