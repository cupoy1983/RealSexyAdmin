# 通用浏览器采集机制

供业务模块按需调用，不自动启动采集，不注册路由，不导入后台设置、数据库、Redis 或任何业务模块。

| 入口 | 职责 |
|---|---|
| `engine.run_browser_job`、`BrowserJobOptions` | Crawlee＋Playwright 单入口浏览器任务、独立队列／存储、生命周期、准备／处理／失败回调及清理 |
| `browser.EdgeBrowser` | 独立 Edge／CDP 会话及可选导航节奏控制 |
| `traffic.TrafficGate` | 单执行端的主动导航间隔与跨重启冷却 |
| `session.GuestSessionStore` | 作用域绑定、有期限、域名白名单的私有游客 Cookie 存储 |
| `auth_session.AuthSessionStore` | 平台／账号／设备作用域内的私有授权状态保存、加载、过期及删除；由站点适配判定登录成功 |
| `snapshot.SNAPSHOT_SCRIPT`、`safe_url` | 生成禁网、去脚本的离线 HTML 与规整页面地址 |
| `interactive.frame`、`interact` | 当前页面截图及调用方已授权的人工交互原语 |
| `http.retry_after_seconds` | 有限范围的 HTTP Retry-After 解析 |

业务调用方自行决定域名、市场、商品标识、采集字段、权限、任务、冷却原因和人工恢复条件。当前选品是第一个业务调用方；其他模块可以直接导入本包，不需要依赖选品模块，也不会因本次抽取自动启用采集。

同一私有状态目录只能交给一个执行进程，调用方负责进程互斥。Cookie 和节奏状态放私有目录，禁止与公开上传或普通观测备份混用。会话作用域不匹配或过期即丢弃；冷却状态无效时停止。交互原语不提供身份、权限及页面业务判断，必须由调用方完成这些检查。

状态、节奏、会话、HTML 脚本只依赖标准库。CDP 浏览器按需使用 httpx／websockets，交互接收 Playwright page 对象，不在模块导入时启动浏览器。采集镜像只复制所需包；不使用浏览器运行层的后台模块无需安装 Crawlee 或 Chromium。共享回归在 `backend/tests/system/test_crawler_infra.py`。

## 接入条件及依赖

Python 版本沿用仓库的 3.12 及以上要求。更新主分支后，在自己的业务模块中直接导入 `app.plugin.infra.crawler` 下的工具；业务模块负责站点解析、字段转换、任务、权限和调度。共享层不依赖选品代码，主分支本次交付可以单独使用。

| 使用能力 | 需要准备 |
|---|---|
| 状态、节奏、会话、URL 和 HTML 脚本 | Python 标准库 |
| Crawlee＋Playwright 通用运行层 | 在独立采集环境安装本目录 requirements.txt；已验证 Crawlee 1.11.2、Playwright 1.61.0，以及 Chromium |
| `EdgeBrowser` | 仓库已有的 httpx、websockets 16，以及执行设备上已安装的 Edge；也可通过 executable 显式指定兼容 CDP 的浏览器，须自行验证 |
| `frame`、`interact` | 调用方创建的 Playwright page；浏览器安装、依赖锁定及生命周期由调用方管理 |
| 业务任务调度、研究任务、后台接管页面 | 由业务调用方另行接入，不随导入本包自动启用 |

仅使用标准库工具的模块不需要安装浏览器。使用 Playwright 的独立执行端自行管理其依赖，不向整个后台追加采集运行环境。

本目录 requirements.txt 是独立采集运行环境的锁，不是后台依赖清单，不能直接安装到 OA／主后台 venv。采集锁与后台锁可能存在不同的精确版本（例如 greenlet）；应分别维护、构建和验收。engine 模块本身延迟导入采集依赖，后台可以导入 BrowserJobOptions 等纯配置类型；真正执行 run_browser_job 才需要完整采集运行环境。生产触发链路应由后台提交任务给独立 worker，再接收结果，不在 HTTP 请求处理器内直接启动浏览器。具体任务 API、队列或跨进程调用由业务模块设计。

## Crawlee＋Playwright 运行层

商品中心主动提交商品链接可使用独立 `product_jobs` 服务：异步提交、状态与结果、调用方鉴权和私有持久化执行队列；后台 SDK 为 `product_jobs.client.ProductJobsClient`，不需要在业务后台安装浏览器依赖。部署、接口和商品中心登录／采集连接顺序见 [商品链接采集任务 API](./INTEGRATION.md)。该服务不写商品表、不提供前端业务页面，也不替代选品 worker 协议。

后续需要浏览器渲染或交互的站点适配共用本运行层。Crawlee 管理请求队列、浏览器任务和生命周期，Playwright 提供 context.page 上的页面导航及交互；不同站点各自提供准备、数据处理和失败翻译回调。可使用官方 API 或纯 HTTP 的来源，由业务模块选择更轻的适配方式。

```python
from app.plugin.infra.crawler.engine import BrowserJobOptions, run_browser_job


async def collect_title(url: str):
    async def handle(context):
        return {"title": await context.page.title()}

    return await run_browser_job(url, handle, options=BrowserJobOptions(headless=True))
```

`run_browser_job` 返回 handle 的原始返回值。可传 `prepare(context)`，在入口导航前执行；可传 `on_error(context, error)`，把失败转换为调用方结果，未提供时失败会抛出。回调内的站点限制、市场条件、字段及业务结果验证由调用方负责。

每次调用创建唯一队列、内存存储和独立临时目录，结束或取消时释放队列。每任务只有一个入口、一个页面处理器，初始配置为单并发、零请求重试、零会话轮换、关闭自动处理访问阻挡、不生成浏览器指纹。分页等后续导航由站点处理器在同一 page 中按自身节奏执行。worker 应串行调用，本轮未验收跨调用并发调度。

BrowserJobOptions 支持显式浏览器路径、headless、locale、可选 storage_state、启动参数、入口导航和处理器时限。浏览器显示模式默认有窗口；Linux 对应环境需提供 Xvfb，或明确选择 headless。时限包含 prepare 的执行时间，人工接管需要的等待预算也由业务调用方计入。storage_state 可来自 GuestSessionStore，不从个人浏览器读取。

### 设备配置与受限页面的责任边界（2026-11-25 补充）

站点适配可以显式配置 browser_type（chromium／firefox／webkit）、user_agent、viewport、is_mobile、has_touch、device_scale_factor、extra_http_headers。新增字段为可选，未设置时保留原 Chromium 和原 context 行为；设备字段通过 Playwright context 入口传递，browser_type 用于选择浏览器引擎；不通过启动参数覆盖 UA。Firefox 不接受本接口的 is_mobile 显式设置。UA、视口、触摸及缩放应采用一致的设备配置；这属于设备仿真，不承诺等同真实移动硬件或消除网站验证。不要同时在 extra_http_headers 中配置 User-Agent，统一使用 user_agent。

设备参数在新任务创建 browser context 时生效，不会在运行中自动切换。业务层复用游客状态时，应把设备配置标识加入 GuestSessionStore 的 scope；更换设备配置时使用对应状态，不把不同配置的 Cookie 任意混用。

1688 适配方负责识别最终 URL 中的 `/_____tmd_____/punish`、页面特征及异常响应，保存原因与现场，并按自身策略调用 TrafficGate.cooldown／决定是否弃用游客状态。HTTP 200 不代表已取得有效商品正文；共享引擎不会替业务判断 punish。不得用无上限的外层循环反复创建新任务来重试同一拦截，冷却、重试上限及人工处理出口应在调用方明确。

fingerprint_generator=None 表示不额外生成指纹，不是关闭真实浏览器功能；retry_on_blocked=False、零请求重试和零会话轮换用于让失败明确返回，避免隐藏重试与自动换身份。use_incognito_pages=True 表示页面 context 隔离，可以显式初始化游客状态，不是“关闭反爬”。这些策略保持原默认值，没有运行中自动开启的开关。需要有限的网络重试时，应按网络故障、限流与人机验证分别设计，不把设备配置和重试／会话轮换混为一个总开关。

同一次测试同时更换 PC／移动地址、UA 或其他会话条件，只能证明该组合在当时取得页面，不能据此确认 UA 是唯一原因。站点成功与稳定性需要由适配方分别验收。

### 专用浏览器人工登录

已验收中文环境的原生稳定版 Chrome 可通过 `LOGIN_BROWSER_MODE=native` 启用 `NativeLoginManager`，仅附加连接容器回环 CDP 以校验和导出专用会话。需要包含原生 Chrome 与中文显示／输入依赖的部署镜像，不适用于仅有 Playwright Chromium 的基础镜像。账号 profile 隔离、共享授权目录、正式入口和验收边界见 [中文原生浏览器正式会话](./INTEGRATION.md)。

`login_service` 和 `login_deploy/` 提供显式启动的独立登录应用，不随 import 启动。Ubuntu Server 上使用虚拟显示与 noVNC，让使用者通过受保护的窗口自行登录；不读取个人浏览器 profile。入口须通过 SSH 本地转发，整个页面及 WebSocket 均要求额外 HTTP 认证。站点入口、允许域名及成功标志通过私有配置提供，不能仅凭点击“完成”判定成功。授权状态保存到独立 AuthSessionStore，供同机业务执行端显式加载到 BrowserJobOptions.storage_state；不会自动把游客任务改成账号任务。其他平台可复用机制，仍须分别实现并验收站点适配。

实施范围、登录状态的存放边界和部署步骤见 [Collector 专用浏览器登录](./INTEGRATION.md)。当前远程页面与后台菜单尚未连接，不能将已有 Amazon 验证截图接口直接当作登录入口。

### ARM 与资源验收边界

[Playwright 官方系统要求](https://playwright.dev/python/docs/intro#system-requirements)列出特定 Debian／Ubuntu 的 x86-64 和 arm64 支持；arm64 不等于所有 ARM，更不等于 32 位 armv7。Armbian 需核对实际 CPU 架构、用户空间位数、发行版、完整依赖安装、Chromium 启动和沙箱能力，不能仅凭系统名称判定通过。本库的完整锁定运行环境目前只在 Linux x86-64 验收，ARM 上须另跑安装和 offline_smoke；官方浏览器支持不替代整套依赖及实际硬件验收。

1536 MiB 容器、1152 MiB Crawlee 内部预算是单任务验证配置，不是最低内存要求，也不是生产可用容量保证。部署方需核对后台及其他容器占用和峰值。选品现有方案把浏览器放在独立 x86 Collector，Armbian 承载后台；其他业务若选择 Armbian 同机采集，应单独做容量验收。主引擎使用 Playwright 管理的 Chromium；EdgeBrowser 是独立 CDP 辅助入口，Windows 路径查找不代表主引擎依赖 Windows。

引擎延迟导入 Crawlee／Playwright；只导入模块不会要求后台安装浏览器依赖。独立采集环境安装方式：

```bash
python -m pip install -r backend/app/plugin/infra/crawler/requirements.txt
python -m playwright install --with-deps chromium
```

requirements.in 记录顶层版本范围，requirements.txt 复用并锁定已验收执行端的解析版本；后续升级须重新解析、回归后同步。已经按该锁定环境构建的采集镜像无需重复安装。当前已验收 Linux x86-64，其他架构由部署方另行验证。容器需明确预留浏览器资源；本轮单任务验证采用 1536 MiB 容器上限和 `CRAWLEE_MEMORY_MBYTES=1152`，该值是验证预算，不是所有站点的容量承诺。

运行环境安装后，可从仓库根目录执行 `python backend/app/plugin/infra/crawler/examples/offline_smoke.py`。脚本仅打包共享代码，在内存路由中提供合成页面，依次验证同入口两次任务和 HTTP 503 失败；不会启动后台或连接业务数据库。在容器中加 `--network none` 可进一步保证运行期间没有外网访问。成功输出 repeated_jobs=2、failure_status=503、business_dependencies=false。

## 最小调用示例

以下函数接收调用方已有的 Playwright page；`state_dir` 使用该执行端专属私有目录。调用方在领取新任务之前先检查冷却，避免占用租约后长时间等待。

```python
from pathlib import Path

from app.plugin.infra.crawler.traffic import TrafficGate


async def fetch_page(page, state_dir: Path, url: str):
    gate = TrafficGate(state_dir / "traffic.json", interval_seconds=5)
    if gate.remaining > 0:
        return {"state": "cooling", "remaining_seconds": gate.remaining}

    await gate.navigate()
    response = await page.goto(url, wait_until="domcontentloaded")
    if response is not None and response.status == 429:
        gate.cooldown(1800, "http_429")
    return {"state": "visited"}
```

实际 worker 应长期复用同一个 gate，并把所有主动导航串行交给它。不同实例即使指向同一文件，也不提供多进程或并发协程锁；多 worker 的全局限流需要由调用方另行实现。采用 `EdgeBrowser(navigation_gate=gate)` 时，由浏览器内部调用 gate，不再在外部重复等待。

```python
from pathlib import Path

from app.plugin.infra.crawler.session import GuestSessionStore


async def open_guest_context(browser, state_dir: Path):
    store = GuestSessionStore(
        state_dir / "guest.json",
        scope="my_module:market:locale:region",
        allowed_domains={"example.invalid", "www.example.invalid"},
        ttl_seconds=21600,
    )
    saved = store.load()
    context = await browser.new_context(**({"storage_state": saved} if saved else {}))
    return context, store
```

调用方使用完 context 后关闭它；确认会话有效时执行 `store.save(await context.storage_state())`，需要弃用时执行 `store.discard()`。

scope 必须包含会影响数据口径的市场／地区等条件。域名采用去掉前导点之后的精确白名单匹配，不自动放行所有子域名；仅存储 Cookie，不保存 localStorage。六小时是初始默认值；同一有效文件反复保存不会延长已有到期时间。Cookie 文件与其他私有状态都不进入公开上传、源码或普通观测备份。

GuestSessionStore 只处理游客 Cookie，不是完整登录态存储。六小时为可配置的本地保留期限，不能保证 Cookie 或服务端会话在此期间始终有效。需要授权登录态的业务，应自行管理账号隔离、跨域状态及有效性验证；必要时使用 Playwright storage_state 保存所需的 Cookie、localStorage 或按需启用的 IndexedDB。不要把登录态直接送进 GuestSessionStore.save，否则非 Cookie 状态会被丢弃。完整授权状态可通过 BrowserJobOptions.storage_state 传入，不需要扩展游客存储的默认职责。

## 输出契约及调用方责任

- `TrafficGate.navigate(interval_seconds=...)` 等待现有冷却及更长的导航间隔，并原子记录本次主动导航时间；不会自动识别网站拒绝，也不会自动重试请求。冷却原因和时间由调用方传入。损坏的节奏状态会抛出错误，不能忽略错误继续领取。
- `retry_after_seconds(value)` 返回 0～86400 的秒数或 None，支持秒数和 HTTP 日期；调用方决定该提示如何与自己的冷却策略组合。
- `SNAPSHOT_SCRIPT` 交给 `page.evaluate`，返回 url、title 和 html。离线 HTML 保留文本结构，去掉脚本、样式、外部资源和输入值，不保证与原网页同样布局；内容限额、存放位置及访问控制由调用方负责。`safe_url` 去掉地址中的账号、查询参数和片段。
- `frame(page)` 返回 frame_id、image（Base64 JPEG）、width、height；只截视口，图片上限 512000 字节，默认遮盖密码和邮箱输入框，可传额外遮盖选择器。
- `interact(page, command)` 仅执行调用方明确传入的 click、text、enter、backspace、tab。resume／stop 等业务动作由调用方解释，不触发浏览器操作。调用方在执行前校验操作权限、有效任务／租约、截图时效、坐标范围及当前站点；text 不接受向密码或邮箱输入框输入。
- 私有 JSON 使用原子替换及私有文件权限，拒绝目标文件符号链接。选择私有且可信的目录、进程互斥和磁盘备份仍由调用方负责。

异常和截图是否代表商品、能否继续任务、是否允许人工操作都由业务层判断。此库提供浏览器机制，不能单凭工具调用成功认定某站点已采集成功。

特别注意：SNAPSHOT_SCRIPT 删除副本中的全部 script，内联 JSON／应用初始化状态也会被删除。部分站点（包括某些 1688 页面）的 offerInfoModel、currentPrices、skuImages、skuWeight 等数据可能保存在其中，因此不能把该离线 HTML 当作完整商品数据或可重放的原始响应。适配方应在原页面有效期间通过 page.evaluate／明确的数据响应读取白名单业务字段，分别保存字段来源、采集条件和时间；快照用于人工审阅及诊断。脚本处理的是克隆副本，不会删除原页面的 script，也不会阻止随后对原页面取数。不要把整个 window／登录状态对象当作商品证据保存。

价格和 SKU 完整性由业务适配验收：档位数量按商品实际返回记录，不预设必有三档；缺少 skuProps／skuMap 或只有一档价格时，应区分商品本身没有这些结构、登录／权限限制、异步数据未加载和解析结构变化。浏览器可执行脚本和加载后续页面数据，但不保证获得未经授权的数据；某个未登录样本的缺失结果不能推广成“浏览器渲染不会改变”，登录也不保证补齐所有字段。

## 独立部署及验证

独立执行端可将本目录及 `infra/__init__.py` 打包到 `/worker/app/plugin/infra/`，把 `/worker` 放入 Python 模块搜索路径。`app`、`plugin` 可作为命名空间；不要为此复制后台应用工厂。调用方同时打包自己的业务适配、锁定运行依赖并准备浏览器及启动命令。

在仓库根目录使用已同步的开发环境执行：

```bash
uv run --project backend python -m pytest backend/tests/system/test_crawler_infra.py -q
uv run --project backend ruff check backend/app/plugin/infra/crawler backend/tests/system/test_crawler_infra.py
```

共享测试不访问外网、不启动浏览器、不连接业务数据库；仓库 conftest 会将框架测试环境隔离到 SQLite 和假 Redis。引擎回归另在 `backend/tests/system/test_crawlee_engine.py`，运行时可将它加入上述 pytest 和 Ruff 命令。独立禁网 Chromium 镜像另验收实际引擎连续批次、队列隔离和失败处理；其他站点仍需调用方做自己的验收。

## 维护、问题反馈与变更

当前共享机制由选品采集开发维护方负责维护。其他模块可以直接调用；共享层缺陷继续交回该维护方修复，站点解析和业务规则由各调用模块维护。

反馈请注明组件名、实际提交号、Python／浏览器版本、运行方式、期望与实际结果，并提供最小复现或脱敏错误堆栈。涉及真实页面时通过私有途径提供必要现场，不把 Cookie、令牌、账号或原始业务数据写入公开 issue。推荐问题标题使用 `[infra/crawler]` 前缀，不要求对方先修改共享实现。

修复流程为：复现 → 在共享测试补回归 → 修复机制 → 检查已有调用方兼容性 → 更新文档及提交。默认保留现有导入与参数语义；确需不兼容变更时，先明确受影响调用方和迁移方法，再实施。使用方可提出修复或 PR，共享实现保持一份，不复制到各业务模块中分别维护。

2026-11-25 首次共享交付：包含 Crawlee＋Playwright 通用运行层、锁定的可选运行依赖、浏览器连接、私有状态、访问节奏与冷却、游客会话、HTML 快照、人工交互原语及回归测试；不包含选品业务、凭据、服务器配置、数据库迁移或自动采集计划。
# 共享 Collector 业务接入

各中心后台通过 `collector_client.CollectorClient` 调用商品任务和平台会话接口，不需要导入浏览器运行依赖，也不依赖选品控制器。完整接口和鉴权约定见 [Collector 共享基础接口](./INTEGRATION.md)。原 `product_jobs.client.ProductJobsClient` 与选品会话桥接继续兼容。

`BrowserJobOptions.navigation_wait_until` 默认 `domcontentloaded`，可显式选择 `commit` 或 `load`。选择 `commit` 的调用方必须在业务回调等待真实内容或验证页面、检查响应状态与目标主机；收到响应本身不能作为采集成功。网络导航时限与人工接管时间分开配置，避免尚未进入处理器时额外空等人工操作预算。
