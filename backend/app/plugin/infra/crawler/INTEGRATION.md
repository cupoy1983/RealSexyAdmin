# Collector 共享服务接入与部署边界

## 共享能力

### 采集异常与会话状态同步（2026-11-27）

1688 任务适配器在返回 needs_login／needs_verification 前，使用 SessionHealth 将 login_required／verification_required（访问拒绝则为 access_denied）写入对应授权版本的共享健康记录。无论引擎返回失败对象还是直接抛出 CollectionFailure，都必须先同步再结束任务。会话 status 优先读取该记录，立即覆盖十二小时正常缓存；网络错误保持 network_error，不解释成掉线。旧授权版本的迟到结果不影响新登录。

商品中心拿到这些任务终态后，仅刷新 session_status(platform, session_name)，按返回的 state／reason 显示人工入口，不自动 force=true。不需要新增 report API，也不要由前端伪造 Collector 的会话事实。force=true 仅供明确的实际复查。

部署必须让登录服务与商品任务服务的 COLLECTOR_SESSION_HEALTH_DIR 指向同一可读写数据卷；仅存在 session_health.py 不代表挂载已接通。可通过 compose.session-health.yaml 或选品部署覆盖文件配置。对接使用本文件当前版本，旧版“六十秒缓存、needs_login 后强制复查”说明已失效。

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

### 结果保留模式（2026-11-27）

POST /v1/jobs 新增 save_mode：overwrite（默认，商品中心）或 history（显式保留历史）。SDK submit 接受同名参数。模式不改变幂等规则，也不禁止重新采集。覆盖范围为同一调用方、平台、规范商品 URL、会话名；不同账号及调用方不能互相覆盖。只有成功结果替换当前结果，失败不覆盖；按提交先后防止旧任务迟到覆盖新任务。

overwrite 成功后立即释放该范围较早覆盖任务的结果正文，并清理对应结果 JSON 和失败诊断文件。任务元数据和幂等键保留七天，旧结果接口返回 result_superseded（410），并给出替代任务标识，不能偷偷返回另一次采集结果。新结果缺失的字段保留旧有效值，并记录各字段的来源采集时间，不把旧字段冒充本次新采集。history 任务不参与覆盖清理，显式长期保留，由所属中心另定历史清理规则；选品既有批次和备份不变。

失败诊断和已被替代的任务记录使用七天兜底期限，执行器空闲轮询时也执行清理，不依赖新任务提交。当前有效结果不因任务超过七天而删除。清理只处理队列登记的结果文件与诊断标准文件，拒绝符号链接和越界路径；失败清理可重试。既有未登记孤立文件不自动推断归属或批量删除。此阶段不提供跨中心数据共享、免采复用查询或人工入库确认协议。

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

对应 SDK 方法为 session_status、check_session、login_access、session_operation、login_entry。成功验证后十二小时内，普通查询与非强制检查直接复用，不启动浏览器；验证时间与授权版本持久保存，服务重启不清除正常结论。授权过期或采集报告登录失效、验证码、访问拒绝或网络异常时失效。普通检查使用 force=false，仅明确强制复查时使用 force=true。十二小时不是平台保证 Cookie 必定有效。窗口串行，占用时返回 window_busy；操作记录十分钟，入口票据九十秒且只领取一次。领取响应丢失后重新申请 access，不重试消费同一入口。

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


## 十二小时会话复用部署

成功保存的授权含 verified_at 与独立 generation；健康记录按授权版本隔离，旧任务的迟到失败不能覆盖新登录。已知验证码不因缓存升级而清除；旧授权文件没有验证时间时不凭空续为正常。

登录服务与商品执行器须同时配置 COLLECTOR_SESSION_HEALTH_DIR，并挂载同一私有可写 session-health 卷；商品执行器的授权目录保持只读。两个服务都定义好后，可叠加 login_deploy/compose.session-health.yaml。采集异常会写该目录，使正常缓存失效。故障记录不包含 Cookie；备份与保留规则仍由部署方决定。

站点配置中的 ttl_seconds 也需设为 43200，否则授权文件可能在正常缓存到期前失效。旧选品桥接的默认 check 同样复用缓存；显式强制检查才打开浏览器。当前已有异常仍需先处理异常，不能把延长缓存当作验证通过。

## 自动检查与部署版本核对

普通用户不需要手动检查会话，也不依赖 AI agent 常驻。商品中心 service 在页面初始化或采集前调用 session_status；正常且 fresh 时直接复用。需要检查且没有 requires_attention 时，调用 check_session(force=False)，仅在返回 operation_id 时轮询。需要人工验证或登录时再提示本人处理。已记录异常时不自动循环打开浏览器；用户明确强制检查才传 force=True。页面刷新仅查状态。

正常缓存为 43200 秒。异常结果的六十秒 fresh 新鲜度不是正常会话有效期；requires_attention=true 时普通检查仍返回已知异常，不因 fresh=false 自动重开浏览器。不得根据旧版六十秒逻辑解释当前部署。

站点配置示例见 login_deploy/sites.example.json，包含已验证的新旧 1688 登录标志和十二小时授权 TTL。它不含任何账号、Cookie 或服务密钥，不能用示例文件覆盖当前私有配置和授权；升级现有部署只合并需要的规则和 TTL。

对已有验收通过的原生浏览器镜像，可使用 login_deploy/Dockerfile.update 覆盖共享代码；分别以当前登录镜像和商品执行器镜像为 CRAWLER_BASE_IMAGE，传入完整 REVISION，使用两个不同的输出标签。该更新镜像保留基底原生 Chrome、中文输入、实际服务 CMD 及现有中文登录页面，不将基础 Playwright 镜像冒充正式原生环境。镜像通过 org.opencontainers.image.revision 标签标识对应提交。

升级时保留当前镜像标签和私有配置回滚副本，确认无正在执行的采集、无正在操作的人工窗口，再更新两个组件。session-health、授权、浏览器 profile 和结果卷必须保留。私有配置中 ttl_seconds 应为 43200；健康目录必须由 UID 10001 可读写，商品执行器授权目录继续只读。更新后核对两个镜像的 revision、共享代码摘要、普通检查是否 cached=true 且 operation_id=null。已有正常授权无需因代码升级再强制访问平台。

必要时实际检查才使用 force=true；验证码和登录失效仍须按平台要求处理。容器重建后如残留 Singleton 锁，应先证明旧容器和对应 Chrome 进程不存在、socket 已失效，再备份并仅清理锁链接，不能删除 profile 或 Cookie，也不能把真实占用当作过期锁清理。
