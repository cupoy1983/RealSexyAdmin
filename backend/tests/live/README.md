# `backend/tests/live/` —— 要连本机 dev 库才跑得动的回归脚本

## 两类测试：位置就是类别

| | `backend/tests/`（根 + 各模块子目录） | `backend/tests/live/`（本目录） |
|---|---|---|
| 形态 | pytest，`def test_*` | 直接执行的脚本，`uv run python tests/live/<域>/xxx.py` |
| 环境 | `conftest.py` 强制 sqlite + 内存假 Redis，干净环境自举 | 连 `backend/env/.env.dev` 里那台**真实 dev MySQL** |
| 数据 | 用例自己造的测试数据 | 库里已有的**真实业务数据**（订单、批次、成本表…） |
| 谁跑 | CI，每次 push | 改了对应业务代码的人，在自己机器上跑 |
| 结论形态 | 通过 / 失败 | 打印 `PASS/FAIL` + 合计 n/m |

**「CI 跑不了」决定它放这儿，不决定它进不进 Git** —— 该不该进 Git 只看一件事：以后还要不要再跑一遍。

## 两边共用的布局（电商 + 内务）

两个业务域共用**同一个测试根** `backend/tests/`，域与域之间靠子目录隔开，不靠文件名：

```
backend/tests/
├── conftest.py  test_main.py  test_migrations.py   # 框架自带，两边都别改
├── system/                                         # A 类：框架/跨域回归（内务那份 test_user_auth_regressions.py 在此）
├── module_ecommerce/                               # A 类：电商的干净环境回归（还没建，将来从 live 搬上来）
├── module_internal/                                # A 类：内务的干净环境回归（10 份，已入库）
└── live/
    ├── README.md                                   ← 本文件，两边的共同索引
    ├── ecommerce/                                  # B 类：电商轮次脚本（现 18 份）
    └── internal/                                   # B 类：内务轮次脚本（现 0 份，目录待建）
```

**2026-11-10 合并后两边的实测现状**：内务 0 份 B 类——他那 11 份 A 类全部跑在 sqlite + 假 Redis 上，逐份核对过没有断言 MySQL 专有错误码、没有依赖本机业务数据，所以 `live/internal/` 暂不建（Git 不跟踪空目录），但目录名已先登记进 `docs/adr/domains.json` 的 internal `code_dirs`，将来有脚本时直接放，不会被 `check` 第 4 条拦下（2026-11-12 统一号段后这条改成"目录名必须在登记表里"，见 `docs/agents/shared-rules.md` §1.3 与 §2.5 T6）。结论与处置记在 `docs/内务中心-测试目录布局与回归验证记录-2026-11-10.md`。

布局的**规范条文**是 `docs/agents/shared-rules.md` §2.5 的 **T1–T8**，两边同等生效（按域分目录 / 不许平铺 / 不许跨域串门 / 全仓一份 `conftest.py` / 目录名对齐代码目录 / 开新域目录先把目录名登记进 `domains.json` / 必须回滚 / 不许写死存量快照）。本文件不承担规范，只写两件事：**怎么跑**，和**每份脚本钉的是哪条口径**。改脚本要同步改下面那份清单，否则清单就成了假账。

## 为什么 B 类进不了 CI

三条依赖 CI 给不了，都实测过：

1. 断言写在真实档案上（成本表、9 月批次的订单）；
2. 有的脚本读 Excel 导出样本，路径走环境变量（`docs/agents/shared-rules.md` §3.4「本机绝对路径」那条：真实路径含 Windows 用户名，不进 Git）——`ECOM_EXPORT_ORDER_FILE`（AN 参照文件）、`ECOM_EXPORT_NEW_FILE` / `ECOM_EXPORT_OLD_FILE`（AC 新旧版式两份）。没设就自动跳过真文件那几项，合成夹具照常跑；
3. 断言里写死了 MySQL 专有错误码，例如 `(1048, "Column 'import_batch_id' cannot be null")`——sqlite 上必挂。

所以 `pyproject.toml` 里 `norecursedirs` 排掉了 `live`（实测把脚本改名成 `test_*.py` 也收不到），CI 只跑 A 类。

## 怎么跑

```bash
cd backend
uv run python tests/live/ecommerce/verify_ae_round.py      # 单跑一份
uv run python tests/live/ecommerce/verify_ab_round.py \
              tests/live/ecommerce/verify_ac_round.py      # 或一次跑多份
```

脚本按自身位置算出 `backend/` 再加进 `sys.path`，不写死盘符；要读仓库源码的脚本（AJ）同理按 `parents[4]` 定位仓库根。换 clone、换机器都能跑，前提是那份 `.env.dev` 指向的库在。

## 硬规矩

- **写完必须回滚**：`session.begin()` 里以异常退出，或显式 `rollback()`。正常退出即提交过一次事故（2026-10-20 污染 dev 库）。
- **不许写死存量快照**：断言"本轮没污染存量"要**开头自取基线、结尾对拍**，不许写 `== 1744` 这类数字。锁死快照的脚本会随数据变化自己变红（AE 轮教训，`order_update_v1v9.py` 就是这么作废的）。
- **有 `DELETE FROM` 不等于删数据**：下面标 ⚠ 的那几个删的是**自己刚插的临时行**，仍在事务内，跑完库里不留东西。

## 清单：`ecommerce/`（18 份，按口径分组）

**① 数值尺子（ADR-0039「读文件即量化」，方向写死四舍五入）**

| 脚本 | 钉住的口径 | 前置 |
|---|---|---|
| `verify_ag_round.py` | 幂等不变式：同一份文件先导入再更新必须「无变化」 | 自带夹具 |
| `verify_af_round.py` | 缺失标记无条件求值、读不出来带真实行号报错 | 自带夹具 |
| `verify_an_round.py` | 三道闸（必填闸 / `0` 闸 / 白名单剥括号 / 空值过尺） | 读 `ECOM_EXPORT_ORDER_FILE` 指向的 xlsx，**没设或文件不在就跳过那四项** |
| `verify_al_round.py` | 面单费必填 + 上限 100 + 两位精度拒收；存量快照前后对拍 | 自带夹具 + 前后自取基线 |

**② 导入与批次（AE / AK / AO）**

| 脚本 | 钉住的口径 | 前置 |
|---|---|---|
| `verify_ae_round.py` | 批次号 `NOT NULL` 真落在数据库上；孤儿行照筛得到 → 界面必须红标 | 自带夹具；含 MySQL 错误码断言 |
| `verify_ae_search_mechanics.py` | 「月份 → 批次下拉」靠现有分页接口就够，不许为此新增接口 | 只读 |
| `verify_ak_round.py` | 「只看缺账单」入口在主列表 EXISTS；**展开区那条旧路径不许复活** | 自带夹具 |
| `verify_ao_rowcount.py` | 批次 `row_count` 增量必须等于真实子订单行增量（整单新建也算） | ⚓ 锚本机批次 `order-2026-09`，批次不存在就不跑 |

**③ 成本与 SKU 编码语义**

| 脚本 | 钉住的口径 | 前置 |
|---|---|---|
| `verify_code_semantics.py` | 规则 A 同码置空 / B 混挂禁死 / C 编码大写 | 自带夹具 |
| `verify_mixban.py` | 混挂双向禁死，且删掉规格行后可再建商品级（禁死不是永久锁） | ⚠ 有 `DELETE FROM`（删自建行）；基线自取 |
| `verify_uppercase.py` | 三个写入口的小写编码都刷成大写；名称列不动；跨大小写查重仍拦 | ⚠ 有 `DELETE FROM`（删自建行）；基线自取 |
| `verify_hard_delete_and_clear.py` | 成本价显式清空 = NULL；商品成本是硬删（软删挡不住唯一索引） | 全程回滚，名字吓人而已 |

**④ 认领与账单订正（AI / AJ）**

| 脚本 | 钉住的口径 | 前置 |
|---|---|---|
| `verify_ai_round.py` | 认领 = 把运单号订正回子订单行；删到空连带批次 | 自带夹具（`cvtestai` 等） |
| `verify_aj_round.py` | 「重新订正」整条已删；**「已恢复」是终点**，再带单号直接拒 | 自带夹具 + 读前端源码文本 |
| `verify_confirm_path.py` | 确认批次这条「钱路」没被 `_require_status_in` / 认领改造打破 | 自带夹具 |

**⑤ 解析与店铺基础**

| 脚本 | 钉住的口径 | 前置 |
|---|---|---|
| `verify_ac_round.py` | 分销列判据（只认「已生成采购单」）、带标题行的表头定位、缺账单计数改指主列表 | 读 `ECOM_EXPORT_NEW_FILE` / `ECOM_EXPORT_OLD_FILE` 两份 xlsx，**没设自动 skip 并改用合成表** |
| `verify_ab_round.py` | 店铺建档 / 账单解析一批口径 | AB09 是**取证项**：只报 `tag_name` 唯一索引现状，不判对错 |

**⑥ 出库单编码与成本取价（AP / ADR-0023 + ADR-0024 修订块）**

| 脚本 | 钉住的口径 | 前置 |
|---|---|---|
| `verify_ap_round.py` | 编码 `0` 是快麦给「出库单」留的固定值；非 0 走词表；查无此行与 `0.00` / `NULL` 三种成本态分别处理 | 自带夹具，结尾另开会话证明真档案没动 |

## 2026-11-10 盘点结论

- **删掉 1 份**：`verify_sku_cost.py` —— 一次性对账（比桌面 Excel 与库内 1744 行），不写库、无 docstring、口径已完整落在设计文档 §14 与 `docs/ecommerce/ecommerce-audit-ar-2026-11-08.md`。它依赖的本机文件是硬失败，没有 skip 兜底。
- **改掉 2 份**：`verify_mixban.py` U9 与 `verify_uppercase.py` X10 原先写死 `== 1744`，改成**本轮开头自取基线、结尾对拍**，从此不随成本表行数变化而假红。
- **其余 16 份保留**：静态核对 198 条 `app.*` 导入，全部指得到当前源码里的真实模块与符号；AC / AI 的 docstring 里能看到 AK 轮、AJ 轮改动后同步过来的痕迹，没有"功能已删、断言还在"的僵尸。
- **仍未跑过**：以上全是静态结论。真实红绿只有连上 dev 库跑一遍才知道，谁跑谁负责（脚本一律回滚，库里不留东西）。
