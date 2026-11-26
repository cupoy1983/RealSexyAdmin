# Collector 共享服务接入与部署边界

## 共享能力

本包包含 Crawlee＋Playwright 运行层、访问节奏与冷却、游客 Cookie 与平台授权存储、专用浏览器人工登录、任务与会话 API、后台客户端、异常现场保存、访问异常识别和私有文件原子写入。业务任务关联、用户权限、类目、调度目标与商品入库由各中心负责。备份校验框架未提取；原子写入不等于整批事务或完整掉电恢复。

`state.write_private_bytes` 和 `write_private_json` 使用同目录唯一临时文件、0600 权限、flush／fsync 和 os.replace；失败保留旧文件并清理临时文件。Windows 短暂占用有限重试。异常现场使用 diagnostics 的遮罩、大小限制和 SHA256 清单；不会因留证失败覆盖原业务错误。HTTP 403 识别为 access_denied，不作为登录失效或验证码的充分证据。

## 后台客户端

只安装后台原有 httpx 依赖，不把独立浏览器 requirements.txt 安装进业务后台环境。

```python
from app.plugin.infra.crawler.collector_client import CollectorClient

async with CollectorClient.from_file(private_config_path) as client:
    capabilities = await client.capabilities()
    state = await client.session_status('1688', 'default-1688')
```

私有配置格式为 `{"api_url":"https://collector.example.invalid/collector/v1","api_key":"<service-key>"}`。不要把真实配置、Cookie、入口票据或密钥放进前端、Git 或日志。服务端存调用方标识与密钥摘要；登录进程和任务进程要读取同一登记内容。

共享服务级身份隔离不能替代业务用户权限。各中心 controller 验证本中心权限，service 保存系统用户与 job_id／operation_id 的归属，再代调 Collector。

## 任务接口

前缀 `/collector/v1`，所有请求使用独立 `Authorization: Bearer <service-key>`。

- GET `/capabilities`：查询当前支持的平台与能力。
- POST `/jobs`：请求体 platform、url、session_name，必须有 Idempotency-Key。
- GET `/jobs/{job_id}`：查询执行状态。
- GET `/jobs/{job_id}/result`：成功后获取结构化结果。

首版商品任务仅支持 1688 的指定详情链接。SDK 的 submit、status、result 对应这些操作。状态为 queued、running、succeeded、failed、needs_login、needs_verification。access_denied 可暂停为 needs_verification，但不说明实际出现了验证码。提交响应不确定时复用原幂等键，不换键重复提交。

结果包含 result_version、platform、source_id、url、collected_at、title、images、currency、price_tiers、sku、weight_raw、missing_fields、warnings 和 field_sources。字段缺失明确列出，不用公开页成功代替登录或完整 SKU 验收。服务不写商品业务表。

## 会话与人工入口

- POST `/sessions/status`：platform、session_name，只读查询；saved 不等于 valid。
- POST `/sessions/check`：相同字段，可附 force，返回异步 operation_id。
- POST `/sessions/access`：申请人工登录，返回异步 operation_id。
- GET `/sessions/operations/{id}`：轮询操作。
- POST `/sessions/operations/{id}/entry`：access 完成后领取一次性入口票据。

对应 SDK 方法为 session_status、check_session、login_access、session_operation、login_entry。实际检查缓存六十秒；文件变化、过期或服务重启会失效。任务报告 needs_login 后应 force=true 复查。窗口串行，占用时返回 window_busy；操作记录十分钟，入口票据九十秒且只领取一次。领取响应丢失后重新申请 access，不重试消费同一入口。

用户在专用浏览器完成平台登录，再点击“校验并保存”。不会读取日常浏览器 profile。会话名支持一至四十八位中文汉字、ASCII 字母、数字、下划线与连字符；不接受空格、斜线、点和控制字符。默认名是命名约定，不代表已保存状态。旧会话可能叫 default，不自动迁移为 default-1688。

login_entry 默认采用服务地址 origin 拼接 `/entry`。服务地址与用户可达入口不同，调用 `login_entry(operation_id, browser_origin=trusted_browser_origin)`；origin 必须由后台可信配置提供，不接受浏览器任意输入。允许 HTTPS 或回环 HTTP。

## 当前笔记本测试

当前验证通过已有 SSH 本地转发访问 `http://127.0.0.1:18126/collector/v1`，浏览器 origin 为 `http://127.0.0.1:18126`。这是同一笔记本上的回环入口，不需要提前部署 HTTPS；密钥在本机私有配置文件中，由部署方提供文件位置，不发送到聊天。

另一个 worktree 可复用同一笔记本上的服务地址和获授权的后台凭据。若后台在 Docker，localhost 是容器自身，不能照抄；若在另一台机器或 Armbian，也不能照抄。此时应先部署可达的 HTTPS 网关或在该后台所在环境另建 SSH 回环转发。用户浏览器也必须能访问人工入口；后台通 API 不代表浏览器通入口。

## 生产部署

生产尚需部署方配置真实 HTTPS 主机名、证书与入口路由，不能把测试的 localhost 当作生产地址。API 和页面可共用一个 HTTPS origin；分开时设置 browser_origin。网关转发 `/entry`、页面请求、WebSocket 和 `/collector/v1/`，保留短时票据认证，不能去掉认证换取可达性。

现有 login_deploy/compose.yaml 是登录服务基础部署；compose.shared-api.yaml 只补服务摘要登记，不是完整三服务拓扑。nginx-product-jobs.conf 应包含在登录网关 server 块内，sessions 转登录进程，其他 API 转 product-jobs:8090。执行器使用私有执行队列和结果目录，只读加载授权目录。product_jobs/Dockerfile 基于已准备好的独立浏览器运行镜像，不会替部署方生成带中文输入的原生 Chrome 镜像。

中文原生模式要求 LOGIN_BROWSER_MODE=native，并提供原生 Chrome、虚拟显示、中文字体／输入与配套桌面依赖。native_login.py 仅附加专用 profile 的回环 CDP，不能把基础 Playwright 镜像视为已具备该部署。生产实际镜像与挂载应先验证，再切换，不自动更新现有线上服务。

登录状态与 Cookie 私有保存，不纳入普通观测备份。业务结果与异常现场的备份范围、保留期限和恢复验收由各中心决定。本包提供 SHA256 现场清单，但尚无统一跨中心备份校验框架。

## 验证

共享测试位于 backend/tests/system 的 test_crawler*、test_crawlee_engine、test_native_crawler_login、test_collector*、test_platform_session* 和 test_product_jobs_client。离线浏览器 smoke 见 examples/offline_smoke.py；ARM 部署仍需单独验证。
