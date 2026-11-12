# AGENTS.md

## 项目概览

FastApiAdmin 单体仓库，全栈管理平台：

- 后端：FastAPI + SQLAlchemy + Alembic + Pydantic v2
- 前端：Vue 3 + TypeScript + Vite + Element Plus + Pinia
- 配套文档与部署资源在仓库根目录及 `docker/`

以项目文档为真相来源：[README.md](README.md)、[backend/README.md](backend/README.md)、[frontend/web/README.md](frontend/web/README.md)。`REQUIREMENTS.md`、`CONTRIBUTING.md`、`CHANGELOG.md`、`SKILL.md` 是**上游基线文档，不是我们的需求**（`docs/agents/shared-rules.md` §3.3）；真实需求在 `docs/<域>/` 与 ADR 里。

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
- **本仓是 public fork，一次提交就是一次发布**：暂存、提交、推送由业务方本人操作，agent 只交可粘贴的命令与文件清单。除非明确要求，不强推、不硬 reset、不覆盖他人改动。
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

## ADR：一条全局序列，号由工具发

- **号全仓唯一，平铺在 `docs/adr/`**，当前 `0001–0039`（`0001–0018` 内务、`0019–0039` 电商，旧电商号各加 18）。没有分域目录，也没有 `reserve_upto`。
- **永远不要手敲 ADR 号**：`node scripts/adr.mjs new <标题一句话>` 建文件并打印可粘贴的引用。`renumber` 只用于刚建、还没被任何地方引用过的条目。
- **引用只有一种形式 `ADR-<NNNN>`**，号前不加域名（`电商ADR-…` 这种旧写法 `check` 直接报错）。要说属于哪个域用正文说。
- `docs/adr/domains.json` 是唯一手维护的文件，现在只是**模块登记表**（域名 → 代码目录 → 术语表），与编号无关。
- `docs/adr/README.md` 与 `CONTEXT-MAP.md` 是生成物，手改视为违规，重跑 `index`。
- 判不出某条决策属于哪个域 → 停下来问，不许猜号。已定决议要变更，先商量再动工，不许静默覆盖（`docs/agents/shared-rules.md` §4.1）。

## 闸：日常三条，提交前加一条

**改完 `docs/`、`CONTEXT*.md`、ADR 引用或任何文本文件，立刻跑这三条**（CI 也跑它们）：

```bash
node scripts/adr.mjs check        # 号段 / 引用可解析 / 引用不带域名 / 测试目录已登记 / 相对链接指空
node scripts/adr.mjs index        # 重写 CONTEXT-MAP.md 与 docs/adr/README.md（生成物，手改视为违规）
git diff --exit-code docs/adr/README.md CONTEXT-MAP.md   # 抓生成物被手改
```

**要提交／发布之前，再加一条**（业务方 2026-11-12 定的出场时机：日常不跑，发布前必跑）：

```bash
node scripts/pii.mjs              # 可识别到个人的真实信息；--selftest 验闸自己
```

理由：这仓是 public fork，敏感值一旦提交就从"能改"变成"要重写历史才能撤"——那一秒是这道闸唯一值钱的地方；平时的控制点是 §3.4 那条"agent 不写"。它现在 0 命中，不挡任何路。**主控制仍是写作规矩，不给它加新规则**；命中只有「改」一条路（豁免底账 `scripts/pii-waivers.json` 已删除，`--record` 也随之拆掉）。

`.github/workflows/docs-adr.yml` 跑的就是这四步（CI 那侧第 4 步天然就是发布前的检查点），**但还没上线**：工具链那批文件（`scripts/`、`.github/`、`docs/agents/`、`CONTEXT*.md`、这份 `AGENTS.md`）从未提交，对面 clone 的 agent 因此手里没闸；fork 的 Actions 还要业务方在 GitHub Settings 启用。现状与阻塞清单见 `docs/agents/shared-rules.md` §1.4。

## 敏感信息（一句话口径）

2026-11-11 起：可识别到个人的真实信息不写进代码与文档——真名写主键（`oa_user_id=10`）或代号，手机号／钉钉 userid／登录账号／邮箱／店名／可定位的单号／本机绝对路径一律不写；真实金额不与可识别字段同段；品名用 SKU 编码表达；工号、部门名／公司名／品牌名／考勤组名不属敏感范围。`scripts/pii.mjs` 保持现状**不再加规则**，控制点是 agent 不写。完整硬线、类别清单与刻度表在 `docs/agents/shared-rules.md` §3.4 与 `docs/agents/pii-inventory.md`。

## Agent 技能指针

- **Issue 追踪器**：工单以 GitHub issues 存在于 `cupoy1983/RealSexyAdmin`，用 `gh` CLI 管理 → `docs/agents/issue-tracker.md`。
- **分类标签**：五个标准标签 `needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix` → `docs/agents/triage-labels.md`。
- **领域文档**：多上下文布局，根 `CONTEXT-MAP.md` 指向各域术语表与代码目录 → `docs/agents/domain.md`（读它决定探索代码前先加载哪份 ADR 与术语表）。
- **与 RealSexy-OA 的通用规则**：`docs/agents/shared-rules.md` 是两个 agent 共用的约定（ADR 工具与 CI、编码硬线、文档布局、提问与问题编号规则），外加一张「两边现状差异 + 各自还要改什么」的清单。提出需要对方也遵守的约定前先读它。
