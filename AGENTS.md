# AGENTS.md

## 项目概览

FastApiAdmin 单体仓库，全栈管理平台：

- 后端：FastAPI + SQLAlchemy + Alembic + Pydantic v2
- 前端：Vue 3 + TypeScript + Vite + Element Plus + Pinia
- 配套文档与部署资源在仓库根目录及 `docker/`

以项目文档为真相来源：[README.md](README.md)、[backend/README.md](backend/README.md)、[frontend/web/README.md](frontend/web/README.md)。`REQUIREMENTS.md`、`CONTRIBUTING.md`、`CHANGELOG.md`、`SKILL.md` 是**上游基线文档，不是我们的需求**（`docs/agents/shared-rules.md` §3.3）；真实需求在 `docs/<域>/` 与 ADR 里。

## 这仓是上游框架的 fork

上游是 FastApiAdmin，业务是在它基础上加和改，**以后还要从上游拉更新**（业务方 2026-11-13 定）。由此推出三条：

- **上游自带的文件保持与上游一字不差**：不搬、不改名、不删——尤其那四份基线文档。删掉或挪走不会少一块信息（git 历史里一直都在），却会在每次同步时制造 rename／delete 冲突；`CHANGELOG.md` 停在 `3.0.0` 也不要替它续账，我们的变更史在 git 里。
- **业务代码只放 `backend/app/plugin/module_<域>/`**（前端同理），框架目录零改动是有意为之，不是巧合：上游一动，我们要的是一条干净的 merge。
- **同步由业务方主导**：什么时候拉、怎么合、已动过哪些框架文件（那些改动都经他同意），都由他掌握。agent 不主动发起同步，也不为同步预先做"撞点清单"这类工程。

`REQUIREMENTS.md` 那 3135 行标题就是「SaaS 多租户平台需求文档」——那是上游那个产品的需求，**不许从它推我们的需求，也不许照它改代码**。

## 默认工作流

### 后端

```bash
cd backend
uv sync
uv run main.py run --env=dev
```

数据模型变更走 Alembic（CLI 在 `backend/main.py`）：

```bash
cd backend
uv run main.py revision --env=dev
uv run main.py upgrade --env=dev
```

⚠️ dev 启动会**自动生成并自动应用**迁移，改 model 前先确认这不是你要的产物。

迁移历史是**两个根、一个合并点**：电商链与内务链各自从自己的根长出来，靠一个不含 DDL 的合并点收成单一 `head`，所以 `head` 这个写法可用（`ADR-0040`）。口径是**同一个库装两个域**，只靠表前缀分：库里两域的表已经齐 → 用 `stamp` 只登记、别让迁移再建一遍（`op.create_table` 不带 `IF NOT EXISTS`，会撞 duplicate）；缺哪个域的表 → 正常 `upgrade`，让迁移去建。这条不变量有机器闸：`node scripts/alembic-heads.mjs`（见下面「闸」那一节）。

### 前端

用 pnpm，且写全 `pnpm run <脚本>`（简写在审批时会变卦）：

```bash
cd frontend/web
pnpm install
pnpm run dev
pnpm run type-check
pnpm run lint
pnpm run test
```

环境变量以 `VITE_` 为前缀，改 `.env` 后重启 dev server。

## Git：拉取、提交、推送

以下命令都在仓库根执行。开工前先 `git status` + `git pull --ff-only`；本地未提交改动与远端冲突时，保留本地改动并说明情况。

```bash
git status
git diff
git add <逐个点名的文件路径>
git diff --cached
git commit -m "feat: 简要说明"
git push
```

- 按文件添加，不用 `git add .` 带入无关文件；常用类型 `feat` / `fix` / `docs` / `refactor` / `test`。
- 不提交 `.env`、生产密钥、钉钉 Secret、本地产物；被 Git 忽略的文件不擅自 `git add -f`。
- **本仓是 public fork，一次提交就是一次发布**：暂存与提交由 agent 自己做（业务方 2026-11-19 改口径，此前是本人操作），提交前重跑一次 `git status`、只点名加自己这批文件，必过发布前那道闸（`node scripts/pii.mjs` 加日常三条），并把「这批有哪些文件」列出来再动手。**推送仍要业务方一句授权**（一个动词一次授权，2026-11-15 的口径不变）。除非明确要求，不强推、不硬 reset、不覆盖他人改动。
- 兄弟检出 `../RealSexy-OA` 与这里是**同一个 remote 的同一个分支**，各持对方没有的提交。合它只能 `git pull` / merge，**不要复制文件**——复制会让对面下次提交带回旧路径的同名文件，仓库里出现两份 ADR。

## 生产环境重新部署

后端跑在内网 Armbian，前端静态文件由外网 OpenResty 提供；两边可分别部署，生产不用 Vite dev server。

后端代码变更——本机推送后，在**内网 Armbian**拉取并重新构建（只 `restart` 不会把新代码打进镜像）：

```bash
cd /mnt/disk/workspace/RealSexyAdmin
git pull --ff-only
cd docker
docker compose -f docker-compose.prod.yaml up -d --build
docker compose -f docker-compose.prod.yaml ps
```

只改后端环境变量（`backend/env/.env.prod`，不进 Git，所以 `git pull` 更新不到它）：

```bash
cd /mnt/disk/workspace/RealSexyAdmin/docker
docker compose -f docker-compose.prod.yaml restart backend
```

前端代码变更——本机拉取、构建后上传到 OpenResty 站点根：

```bash
git pull --ff-only
cd frontend/web
pnpm run build
scp -r dist/. root@<外网 OpenResty 主机>:/srv/RealSexyAdmin/
```

主机地址与账号进本机运维记录，**不写进仓库**。依赖变化时先 `pnpm install` 再 build；改 `.env.production` 后必须重新构建并上传。含数据库结构变更时按迁移流程执行对应 Alembic 迁移，先读迁移内容再跑。上传后浏览器仍是旧内容，先查浏览器缓存和 OpenResty 实际服务的站点目录，别急着回滚构建。

## 架构约定

### 后端

- 框架代码在 `backend/app/modules/<domain>/`，**业务代码一律 `backend/app/plugin/module_<域>/`**，不散落在路由文件里。
- 插件控制器由 `discover.py` 自动扫描 `module_*/**/controller.py` 注册；不要在 `app/api/v1/routers.py` 手工挂载，重复挂载会中止启动。
- 典型模块结构：`controller.py` / `service.py` / `crud.py` / `model.py` / `schema.py` / `param.py`；到 `controller.py` 的**每一级都要有 `__init__.py`**（少一个中止整个应用）。
- 遵循仓库的 Pydantic v2 与 SQLAlchemy 约定：ORM 写入用原生 Python datetime，返回数据或写 Redis 用 JSON 安全的序列化输出。
- 用共享的响应与校验工具，不自造响应结构。`app/plugin/infra/` 是基础设施，不是模块，不放 controller。

### 前端

- Vue 3 Composition API + `<script setup>`。
- 按领域分离 Pinia store、Router 逻辑与 UI 关注点。
- 动态路由与权限由菜单驱动，不破坏路由名称与菜单到路由的对齐。
- 用现有别名（`@`、`@views`、`@stores`、`@utils`），不引入新路径模式。
- 除非明确指示，不手改 `src/types/import/` 下自动生成的类型文件。

## 质量要求

- 测试**按所在目录分两类，两类都按业务域分**：A 类 pytest 在全仓唯一那份 `backend/tests/conftest.py`（强制 sqlite + 内存假 Redis）里就能判定，放 `backend/tests/module_<域>/`，框架级/跨域放 `backend/tests/system/`，**CI 跑这些**；B 类轮次脚本连本机 dev MySQL 真实数据、断言 MySQL 专有错误码，放 `backend/tests/live/<域>/`，CI 不跑。两条硬线：不往 `tests/` 或 `tests/live/` 根上平铺，不把 A 域的断言塞进 B 域目录。条文是 `docs/agents/shared-rules.md` §2.5（T1–T8），逐份脚本索引在 `backend/tests/live/README.md`。
- 选符合仓库现有模式的最小聚焦改动；引用现有文档而不是重抄指南。
- 保持 monorepo 边界：后端改动不进前端代码，反之亦然。
- 未经明确允许不修改其他项目或其他工作目录。
- 声称成功前用对应项目命令验证；没跑就明说没验。涉及库的验证脚本必须靠 `close()` 回滚（教训：2026-10-20 污染过 dev 库）。
- 部署与环境敏感的工作以实际生产配置为准，不猜默认值。

## 贡献者须知

- 根 `README.md` 讲完整技术栈、默认认证账号、模块启用方式、部署模型与环境要求。
- `backend/README.md` 讲 API 启动、迁移与 ORM 序列化预期。
- `frontend/web/README.md` 讲环境变量、路由行为与本地开发生命周期。
- **本仓库支持可选模块**：代码生成、任务、聊天、存储这些功能靠后端配置切换启用与否，**不需要把它们从代码库里删掉**；要摘某个业务域时删的只是 `module_<域>` 那一对目录与它的文档。

## ADR：一条全局序列，号由业务方配

- **号全仓唯一，平铺在 `docs/adr/`**：`0001–0018` 内务、`0019–0039` 电商（旧电商号各加 18），**`0040` 起是跨域／仓库级**（迁移链口径、上游同步、多 agent 共用规则）。**序列到几号以生成物 `docs/adr/README.md` 为准，这份文件不抄当前最大值**——抄了就是每加一条 ADR 过期一次。没有分域目录，`domains.json` 里也没有 `reserve_upto`（那个字段 2026-11-12 删了）。
- **不要自己定号，也不要自己发号**：号段由**业务方**报（**起止两个号都由他报**），**`NNNN-占位.md` 空壳由 agent 照那两个号建**（状态写 `占位（日期）`）。**空壳一律不进库**（业务方 2026-11-20 定，写进 `.gitignore` 的 `docs/adr/*-占位.md`）：壳只住在建它的那个本地，填成正式决议那一刻才 `git add`；`index` 生成的两份索引也跳过没填的壳，所以本地和 CI 拿到的是同一份索引。要写一条决议 = **直接改那一份文件**（标题、状态、正文，号不动），**并且把文件名里的「占位」换成 slug**——忽略规则匹配的是路径，一份填好了却还叫 `NNNN-占位.md` 的决议照样进不了库（`git add` 收不进去，本仓不擅自 `-f`）。**建壳前后各复述一次号**：动手前把他报的那两个号原样回一遍（「收到 46–50，建 5 份」），建完**从 `ls docs/adr` 把文件名读回来**逐号对照，再报一张对照表（「46 47 48 49 50 → 5 份，与你报的一致」）——清单要来自文件系统不是我脑子里那份，复述"打算建什么"等于把同一个错误再打一遍字。**这两种错都没有机器判红**：漏建一份时剩下几号还连着，连洞提醒都不响；建出来的号不是他报的那个只打印一句提醒、不拦提交（业务方 2026-11-21 定：「我跟你说的数字，你建好之后好核对，这两个问题都要你保障避免」，细则见 `docs/agents/shared-rules.md` §1.1）。
- **`adr.mjs` 已经不发号了（`ADR-0042` 的 2026-11-20 修订节）**：`new`、`reserve`、`renumber` 三个子命令删掉——前两个只扫本地、不联网，起点还是自己算的 `max+1`，两个没互相 `pull` 过的检出必然从同一个号建起；`renumber` 自己算 `max+1`，会把业务方报的号覆盖成当地的下一个号。现在打这三个词都是**当场拦下**（命令失败，`docs/adr/` 一个文件都不建）。手上没有号段就**停下来向业务方要**，拿到起止之后照他报的号建壳，不自己往前推；他确实把号写错了，也照他报的那个号手工改——**还没进库的壳直接 `mv` 改文件名**（它不在版本控制里，`git mv` 会报 `not under version control`），已经进库的那份才用 `git mv`；两种都要改正文的 `# ADR-NNNN` 那行，再跑 `index`，不靠工具修正。剩下两个命令照旧天天跑：`check`（号不重、引用可解析、不带域名、不许指空壳、相对链接不指空）、`index`（重写两份生成物）。**号段连着配还是口径，但从 2026-11-20 起不再由机器强制**：空壳不进库，本地分不出"那几号还没填好"（合法，而且是常态）和"号建错了／漏建一份"，所以「号段有洞」降成**提醒**（只打印一句，不算错，CI 绿、可以提交）——代价是号建错、漏建一份从此没有机器兜底，还判红的只剩重号、文件名与文内号不一致那几条。CI 也不发号（workflow 是 `contents: read`），真撞了靠 `check` 的「重号即红」事后兜。
- **引用只有一种形式 `ADR-<NNNN>`**，号前不加域名（`电商ADR-…` 这种旧写法 `check` 直接报错）。要说属于哪个域用正文说。**引用前先读正文**（状态、适用范围、后续修订与例外），编号／原标题／链接从文件里复制，不凭记忆写；格式示例一律写 `ADR-NNNN`，不拿真号当样子——`check` 只证明指得到文件，不证明那句话有依据（业务方 2026-11-21 定，细则见 `docs/agents/shared-rules.md` §1.1）。
- `docs/adr/domains.json` 是唯一手维护的文件，现在只是**模块登记表**（域名 → 代码目录 → 术语表），与编号无关。
- `docs/adr/README.md` 与 `CONTEXT-MAP.md` 是生成物，手改视为违规，重跑 `index`。
- 判不出某条决策属于哪个域 → 停下来问，不许猜号。已定决议要变更，先商量再动工，不许静默覆盖（`docs/agents/shared-rules.md` §4.1）。

## 闸：日常三条，另两条看时机

**改完 `docs/`、`CONTEXT*.md`、ADR 引用或任何文本文件，立刻跑这三条**（CI 也跑它们）：

```bash
node scripts/adr.mjs check        # 号不重 / 引用可解析 / 引用不带域名 / 占位不许被引用 / 测试目录已登记 / 相对链接指空（号段有洞只提醒，不判红）
node scripts/adr.mjs index        # 重写 CONTEXT-MAP.md 与 docs/adr/README.md（生成物，手改视为违规）
git diff --exit-code docs/adr/README.md CONTEXT-MAP.md   # 抓生成物被手改
```

**要提交／发布之前，再加一条**（业务方 2026-11-12 定的出场时机：日常不跑，发布前必跑）：

```bash
node scripts/pii.mjs              # 可识别到个人的真实信息；--selftest 验闸自己
```

理由：这仓是 public fork，敏感值一旦提交就从"能改"变成"要重写历史才能撤"——那一秒是这道闸唯一值钱的地方；平时的控制点是 §3.4 那条"agent 不写"。它现在 0 命中，不挡任何路。**主控制仍是写作规矩，不给它加新规则**；命中只有「改」一条路（豁免底账 `scripts/pii-waivers.json` 已删除，`--record` 也随之拆掉）。

**动到 `backend/app/alembic/versions/` 时，再跑第四条**（业务方 2026-11-13 按 AV1–AV4 拍板）：

```bash
node scripts/alembic-heads.mjs    # 迁移历史只许一个 head；--versions-dir 指到夹具可验闸自己
```

两个 head 不是整洁问题：框架四处写死 `"head"`，`init_db()` 又在启动里无条件跑，`upgrade head` 在 alembic 解析阶段就抛 `CommandError` → 应用起不来。双根照旧合法（`ADR-0040` 保留电商链与内务链各自的根），所以闸只数 head，外加一条"每份的 `down_revision` 指得到真实文件"。红了的修法是补一个不含 DDL 的合并点：`cd backend && uv run alembic merge -m "并头" <head1> <head2>`（框架 CLI 只包了 `run`／`revision`／`upgrade`，没有 `merge`），库里表已齐的那一侧随后 `stamp head` 只登记。闸自己 2026-11-13 校准过：真库 32 份迁移绿，7 个 Temp 夹具里 4 条红路（双头／悬空父／读不懂的文件／重号）全响、2 条绿路不误报。

`.github/workflows/docs-adr.yml` 跑的是这五步（第 5 步 `alembic-heads.mjs` 于 2026-11-13 加入，**第一次真在 CI 上跑过是 2026-11-14 由 `f15eae7c` 触发那一次**，逐 step 量到 `success`），**前四步早就上线并跑绿**（实测三次：`b354d0e9`、`f0d1c38e`、`6f7ed8b4`（2026-11-13 术语表统一那一轮）各触发一次 `docs-adr`，conclusion 都是 `success`；那三次跑的是四步时代的 workflow；CI 那侧第 4 步天然就是发布前的检查点）。**这份文件不抄运行总数**——那个数每推一次涨一次，要现状就现查 Actions。工具链（`scripts/`、`.github/`、`docs/agents/`、`CONTEXT-MAP.md`）和这份 `AGENTS.md` 都已进库，两份术语表从 `6f7ed8b4` 起统一住在 `docs/<域>/CONTEXT.md`（格式规矩见 `docs/agents/shared-rules.md` §3.1），对面 clone `git pull` 之后自己就有闸可跑。唯一还没样本的是 `pull_request` 触发路径（三次都由 push 触发）。逐条实测见 `docs/agents/shared-rules.md` §1.4。

## 敏感信息（一句话口径）

2026-11-11 起：可识别到个人的真实信息不写进代码与文档——真名写主键（`oa_user_id=10`）或代号，手机号／钉钉 userid／登录账号／邮箱／店名／可定位的单号／本机绝对路径一律不写；真实金额不与可识别字段同段；品名用 SKU 编码表达；工号、部门名／公司名／品牌名／考勤组名不属敏感范围。`scripts/pii.mjs` 保持现状**不再加规则**，控制点是 agent 不写。完整硬线、类别清单与刻度表在 `docs/agents/shared-rules.md` §3.4 与 `docs/agents/pii-inventory.md`。

## Agent 技能指针

- **Issue 追踪器**：工单以 GitHub issues 存在于 `cupoy1983/RealSexyAdmin`，用 `gh` CLI 管理 → `docs/agents/issue-tracker.md`。
- **分类标签**：五个标准标签 `needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix` → `docs/agents/triage-labels.md`。
- **领域文档**：多上下文布局，根 `CONTEXT-MAP.md` 指向各域术语表与代码目录 → `docs/agents/domain.md`（读它决定探索代码前先加载哪份 ADR 与术语表）。
- **所有 agent 共用的规则**：`docs/agents/shared-rules.md` 是**在这个仓库上干活的任何 agent**都遵守的同一套约定（ADR 工具与 CI、编码硬线、文档布局、提问与问题编号规则），外加一张「各检出现状差异 + 还要改什么」的清单。业务方 2026-11-13 定的口径是「代码仓库就一个，大家一起开发不同的功能，所有 agent 用同一套约束」→ 决议见 `ADR-0042`。**要新增或修改约定时，改的是这个仓库里的这两份文件，不要写进任何本机笔记**；改 `AGENTS.md`、`scripts/*.mjs`、`docs/agents/**`、`docs/adr/domains.json`、`.github/workflows/**` 之前逐条点头（`shared-rules.md` §4.3）。提出需要别人也遵守的约定前先读它。
