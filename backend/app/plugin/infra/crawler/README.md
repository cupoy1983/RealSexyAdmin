# 通用浏览器采集机制

供业务模块按需调用，不自动启动采集，不注册路由，不导入后台设置、数据库、Redis 或任何业务模块。

| 入口 | 职责 |
|---|---|
| `engine.run_browser_job`、`BrowserJobOptions` | Crawlee＋Playwright 单入口浏览器任务、独立队列／存储、生命周期、准备／处理／失败回调及清理 |
| `browser.EdgeBrowser` | 独立 Edge／CDP 会话及可选导航节奏控制 |
| `traffic.TrafficGate` | 单执行端的主动导航间隔与跨重启冷却 |
| `session.GuestSessionStore` | 作用域绑定、有期限、域名白名单的私有游客 Cookie 存储 |
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

## Crawlee＋Playwright 运行层

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

## 输出契约及调用方责任

- `TrafficGate.navigate(interval_seconds=...)` 等待现有冷却及更长的导航间隔，并原子记录本次主动导航时间；不会自动识别网站拒绝，也不会自动重试请求。冷却原因和时间由调用方传入。损坏的节奏状态会抛出错误，不能忽略错误继续领取。
- `retry_after_seconds(value)` 返回 0～86400 的秒数或 None，支持秒数和 HTTP 日期；调用方决定该提示如何与自己的冷却策略组合。
- `SNAPSHOT_SCRIPT` 交给 `page.evaluate`，返回 url、title 和 html。离线 HTML 保留文本结构，去掉脚本、样式、外部资源和输入值，不保证与原网页同样布局；内容限额、存放位置及访问控制由调用方负责。`safe_url` 去掉地址中的账号、查询参数和片段。
- `frame(page)` 返回 frame_id、image（Base64 JPEG）、width、height；只截视口，图片上限 512000 字节，默认遮盖密码和邮箱输入框，可传额外遮盖选择器。
- `interact(page, command)` 仅执行调用方明确传入的 click、text、enter、backspace、tab。resume／stop 等业务动作由调用方解释，不触发浏览器操作。调用方在执行前校验操作权限、有效任务／租约、截图时效、坐标范围及当前站点；text 不接受向密码或邮箱输入框输入。
- 私有 JSON 使用原子替换及私有文件权限，拒绝目标文件符号链接。选择私有且可信的目录、进程互斥和磁盘备份仍由调用方负责。

异常和截图是否代表商品、能否继续任务、是否允许人工操作都由业务层判断。此库提供浏览器机制，不能单凭工具调用成功认定某站点已采集成功。

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
