# 一个仓库一份规则：所有在这个仓库上干活的 agent 受同一套约束

> 状态：已采纳（2026-11-13）

2026-11-13 用户口径：

> 「**以后所有的 agent 都需要有相同的约定和约束，代码仓库就一个，大家一起开发不同的功能。**」

这一句把 `docs/agents/shared-rules.md` 原有的前提（"**两个** agent 共用"）扩大成一般规则：并行方不再固定是"电商这边 + 内务那边"两个检出，而是任意多个 agent 在**同一个远端同一条分支**上各做各的功能。

## 被否掉的旧状态（有案可查，不是假想）

规则各留一份本机副本，正是 2026-11-11 之前几天的样子：`.gitignore` 里那行 `AGENTS.md` 把规则文件挡在库外（Windows 下连 `agents.md` 一起挡，`git check-ignore -v` 实测命中），对面 clone 的 agent 拿到的仓库里一个字都没有，规则只能靠业务方转述。**那次转述失真的后果就是往公开历史里写进了真实姓名与手机号**（`shared-rules.md` §1.4 第 1 条、§3.4 背景）。所以"约定必须住在仓库里"不是整洁偏好，是有代价的教训。

同理被否：`AGENTS.local-backup.md` 这种本机备份当第二份真相——业务方 2026-11-13 已裁「仓库版为准」，那份只作本机运维笔记，不提交、日后由他本人删（§五 D2）。

## 决定

1. **规则只有一份，且必须在仓库里**。入口是 `AGENTS.md`，细则是 `docs/agents/*.md`；任何 agent 在这个 checkout 里干活都受同一套约束，不开本机分支版。
2. **"一起开发不同功能"要防的三种撞，各有机器防**：
   - **号撞** → ADR 号只由 `scripts/adr.mjs new` 发，人不手填（§1.1）。
   - **结构撞**（两人各拉一条迁移链）→ 迁移历史只许一个 head，`scripts/alembic-heads.mjs` 是 CI 第 5 步（`ADR-0040`）。
   - **领土撞** → 业务代码只放 `backend/app/plugin/module_<域>/`，域要先进 `docs/adr/domains.json` 登记才有目录可放；`check` 第 4 条守 `backend/tests/live/<域>/` 与 `module_<域>/`（T6）。
3. **改约定本身是最高风险动作**：机制文件——`AGENTS.md`、`scripts/*.mjs`、`docs/agents/**`、`docs/adr/domains.json`、`.github/workflows/**`——动手前逐条点头（写进 `shared-rules.md` §4.3）。理由：一处改动会被所有 agent 同时采纳，也同时被它们各自复述出去。
4. **闸要每个 agent 自己跑**，不等转述：日常三条 + 动到迁移目录时第四条 + 提交前 `pii.mjs`。工具链从 `b354d0e9` 起在远端，谁 `git pull` 谁就有闸。

## 代价 / 明确不做

- **没有"谁正在改哪个文件"的占用声明**。两个 agent 同时编辑同一份文件，仍然要靠 Git 冲突当场才发现。第一版故意不做：派活的人是业务方，机制化的占位表只会变成又一本没人维护的底账（`legacy-citations.json`、`pii-waivers.json` 两本底账都这么死过，§1.3）。
- **约定变更没有通知机制**，靠对面下次 `pull`。
- **规则只有一份 ≠ 自动生效**：`AGENTS.md` 靠 agent 读，CI 只能管住可机检的那五步。写作规矩（§3.4、`ADR-0041` 那条"不动上游文件"）仍然没有机器兜底。

## 验收钉子

- `git ls-files` 里 agent 规则入口只有 `AGENTS.md` 一份，根上没有 `agents.md` / `agents.zh-CN.md`。
- `shared-rules.md` §4.3 里那条机制文件清单存在，且包含 `AGENTS.md` 自己。
- `.github/workflows/docs-adr.yml` 五步齐全，本地四条命令与远端五步等价（§1.4）。
