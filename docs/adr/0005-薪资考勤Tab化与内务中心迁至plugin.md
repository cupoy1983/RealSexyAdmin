# ADR-0005：薪资考勤 Tab 化，内务中心模块迁到 plugin

- 状态：已接受（2026-10-28）
- 相关：ADR-0002（考勤结果以钉钉智能月报为准）、ADR-0004（日明细落库）

## 背景

「考勤管理」在菜单里是 type=1 **目录**，靠 redirect 跳到唯一的子页面「考勤统计」。
这个层级只带来一次多余的点击，没有任何实际内容。

同时用户指出：**自建模块应该放在 `app/plugin/` 下，不是 `app/modules/` ** ——
`modules/` 是框架自带的域（system / monitor / task），`plugin/` 才是业务模块的落位。

## 决策

### 1. 「考勤管理」目录降级为「薪资考勤」页面，子菜单改页内 Tab

`type=1` 目录 → `type=2` 页面，redirect 清空，内容由页内两个 Tab 承载：

| Tab | 内容 | 状态 |
|---|---|---|
| 考勤统计 | 钉钉智能考勤月报（原「考勤统计」页面整体） | 已接入 |
| 薪资绩效 | `ElEmpty` 占位 | 预留 |

之所以保留 Tab 而不是直接做成单页：薪资业务已在规划内，现在搭好架子，
将来接入时不用再动菜单结构。占位 Tab 的形态沿用「账号管理 → 快麦账号」的既有做法。

### 2. 后端模块位置：`app/modules/internal/` → `app/plugin/module_internal/`

`app/core/discover.py` 只会 glob `app/plugin/module_*/**/controller.py`，
并把 `module_xxx` 映射成路由前缀 `/xxx`，所以目录名必须是 `module_internal`（带前缀）。
好处是不再需要在 `app/api/v1/routers.py` 里手写 import 和挂载。

> **注意**：新结构下 `DynamicRouterRegistry` 会自动注册 `/internal`。
> 若 `routers.py` 里仍保留 `/internal` 静态挂载，`check_route_conflicts` 会判定
> 路径重复注册并**中止启动**。二者只能有一个。

配合应用 `servers.url = /api/v1`，搬迁后对外 URL 保持不变。

### 3. 接口路径定名（最终方案）

原 `/internal/attendance/monthly-report/*` → **`/internal/attendance/*`**。

路径里的第二段用的是**业务实体名**，不是页面容器名。这一条也经历了两次修正：
先是提过 `/internal/salary-attendance/monthly-report`（把容器名塞进路径），
被 `/ecommerce/sku-cost` 这个反例否掉 —— 商品成本是「订单详情」页面下的功能，
路径却是扁平的 `/ecommerce/sku-cost`，不是 `/ecommerce/order/sku-cost`。
随后又用过 `/internal/monthly-report`，最终定成 `/internal/attendance`，
理由是**日明细不是月报的子功能**：`/internal/monthly-report/daily-list` 语义别扭，
而 `/internal/attendance/daily-list` 才顺。后端目录随之定名 `attendance/`。

依据是 RealSexyAdmin 的既有约定（`views/module_ecommerce/store/index.vue`
+ `api/module_ecommerce/store.ts`）：**Tab 承载的业务各自拥有扁平的接口路径，
不嵌套在页面容器名下面** —— Admin 的「负责人」Tab 没有独立菜单，接口却是
`/ecommerce/tag` 而不是 `/ecommerce/store/tag`。

选它的理由：
- 这套代码最终要合并进 RealSexyAdmin，现在对齐等于提前消掉合并时的一次改名
- 「薪资绩效」将来会拿到 `/internal/salary-performance/*`，与月报平级，不会互相嵌套
- 后端目录同理：`attendance/` 与将来的 `salary_performance/` 是兄弟关系
- 业务实体命名要能覆盖目录下**所有**接口，不能只顾最主要的那个 ——
  这是从 `monthly-report` 改成 `attendance` 的直接原因

### 4. 权限

| 用途 | 权限字符串 | 承载节点 |
|---|---|---|
| 进入薪资考勤页面、调用月报接口 | `module_internal:salary-attendance:query` | 菜单 193 及其「查询」按钮 |
| 薪资绩效 Tab（预留） | `module_internal:salary-performance:query` | 菜单「薪资绩效」按钮 |

旧权限 `module_internal:monthly-report:query` 随菜单节点 243/244 一起软删除。
原持有旧菜单的角色由脚本自动改授到新菜单，不出现「点开一片空白」。

### 5. 前端组织形式照 RealSexyAdmin

```
views/module_internal/salary-attendance/
├── index.vue                        ← Tab 壳（同 store/index.vue）
└── components/
    ├── MonthlyReportTab.vue
    └── SalaryPerformanceTab.vue
api/module_internal/salary-attendance.ts   ← 一个页面一个 ts，内含各 Tab 的 API_PATH
```

## 必须一起改的连带项（漏一个就坏）

1. **定时任务代码块存在数据库里**：`task_cornjob_node` 的 `func` 字段是字符串，
   里面写死了 Service 的 import 路径。改文件救不了它，必须跑脚本 upsert 一次。
   这个任务每月 7 号才执行，失败是静默的，务必核查。
2. **`app/modules/system/user/service.py` 的运行时 import**：新建用户、改手机号时
   联动钉钉，路径失效会直接报错。
3. `app/config/setting.py` 注释里的路径。
4. 两个 `scripts/` 脚本自身的 sys.path 计算（`parents[4]` 在新旧位置都指向 backend，无需改）。

## 后果 / 取舍

- **可接受**：这次改动跨越后端目录、路由、菜单、权限、前端五层，
  因为迁移过程中后端停过一次服务（删旧目录时被 uvicorn reload 抓到半吊子状态，
  新旧两份模型同时被 `ImportUtil.find_models` 扫到，
  报 `Table 'iac_account_binding' is already defined`），属于改动过程中的预期现象，
  迁移完成后不再复现。
- **未做**：薪资绩效业务的任何实现。它不是这次的范围，等有数据源时单独一轮。
