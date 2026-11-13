#!/usr/bin/env node
/**
 * scripts/alembic-heads.mjs —— 迁移历史只许一个 head 的闸（口径见 ADR-0040）。
 *
 * 为什么要这把闸：框架四处写死 revision="head"（`app/scripts/initialize.py` 的 stamp／upgrade／
 * 自动生成后升级，加 `main.py` 的 upgrade 命令），而 `init_db()` 在 lifespan 里无条件跑。
 * 两个 head 不是"以后再说"的整洁问题，是**应用起不来**：alembic 解析 "head" 时直接抛
 * `CommandError: Multiple head revisions are present`。
 *
 * 双根是 ADR-0040 明确保留的（电商链与内务链各自从自己的根长出来），所以这里只数 head，不数 root。
 *
 *   node scripts/alembic-heads.mjs                       校验 backend/app/alembic/versions/
 *   node scripts/alembic-heads.mjs --versions-dir <目录>  指到别处，用来校准闸自己（造个双头夹具它必须响）
 *
 * 只读文本，不连库、不跑 python。解析是刻意的近似：alembic 自己那套才是权威，本闸只守不变量。
 * 读不懂的文件一律报错，不许当成"没有这个迁移"或"它是一个根"跳过——那正是把 2 个 head 说成 1 个的失败模式。
 */

import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const DEFAULT_VERSIONS_DIR = join(ROOT, "backend/app/alembic/versions");

const rel = (p) => relative(ROOT, p).replaceAll("\\", "/");

// revision: str = 'xxx' / revision = "xxx"，行首锚定，避开下面的 down_revision
const REVISION_RE = /^revision[^\n=]*=\s*['"]([^'"]+)['"]/m;
// down_revision 的三种写法：单值、None、跨行的 tuple
const DOWN_RE = /^down_revision[^\n=]*=\s*(?:\(([^)]*)\)|([^\n]+))/m;

function parseMigration(file) {
  const text = readFileSync(file, "utf8");
  const errors = [];

  const rev = text.match(REVISION_RE);
  if (!rev) errors.push(`${rel(file)}：读不出 revision —— 文件名对不上内容，或者写法不是模板那几种`);

  const down = text.match(DOWN_RE);
  // 根也要显式写成 down_revision = None；缺这一行说明文件被改坏了，闸不敢当它是根
  if (!down) errors.push(`${rel(file)}：读不出 down_revision —— 根要显式写 None，缺这一行闸不猜`);

  let parents = [];
  if (down) {
    const raw = down[1] !== undefined ? down[1] : down[2];
    const isNone = down[1] === undefined && /^\s*None\s*$/.test(raw);
    if (!isNone) {
      parents = [...raw.matchAll(/['"]([^'"]+)['"]/g)].map((m) => m[1]);
      if (!parents.length) errors.push(`${rel(file)}：down_revision 既不是 None 也没含任何版本号：${raw.trim()}`);
    }
  }

  return { file, id: rev ? rev[1] : null, parents, errors };
}

function main() {
  const argv = process.argv.slice(2);
  const dirFlag = argv.indexOf("--versions-dir");
  const versionsDir = dirFlag >= 0 ? resolve(ROOT, argv[dirFlag + 1]) : DEFAULT_VERSIONS_DIR;

  if (!existsSync(versionsDir) || !statSync(versionsDir).isDirectory()) {
    console.error(`✗ 迁移目录不存在或不是目录：${versionsDir}`);
    process.exit(1);
  }

  const names = readdirSync(versionsDir).filter((n) => n.endsWith(".py") && !n.startsWith("_"));
  const parsed = names.map((n) => parseMigration(join(versionsDir, n)));
  const errors = parsed.flatMap((p) => p.errors);

  const byId = new Map();
  for (const p of parsed) {
    if (!p.id) continue;
    if (byId.has(p.id)) {
      errors.push(`${rel(p.file)}：版本号 ${p.id} 与 ${rel(byId.get(p.id).file)} 重号 —— alembic 自己也判不出该走哪份`);
      continue;
    }
    byId.set(p.id, p);
  }
  for (const p of parsed) {
    for (const parent of p.parents) {
      if (!byId.has(parent)) {
        errors.push(`${rel(p.file)}：down_revision 指向 ${parent}，这个号在迁移目录里没有 —— 链断在这里`);
      }
    }
  }

  const childIds = new Set(parsed.flatMap((p) => p.parents));
  const heads = [...byId.keys()].filter((id) => !childIds.has(id)).sort();
  const roots = [...byId.keys()].filter((p) => byId.get(p).parents.length === 0).sort();

  if (errors.length) {
    console.error(`✗ 迁移历史闸不过：${names.length} 份迁移文件，问题 ${errors.length} 项`);
    for (const e of errors.slice(0, 40)) console.error("  - " + e);
    if (errors.length > 40) console.error(`  …另有 ${errors.length - 40} 项`);
    process.exit(1);
  }

  if (heads.length !== 1) {
    console.error(`✗ 迁移历史闸不过：${names.length} 份迁移有 ${heads.length} 个 head（${heads.join("、")}），框架要的是唯一一个`);
    console.error("  后果：upgrade head 在 alembic 解析阶段就抛 CommandError，而 init_db() 启动时无条件跑 → 应用起不来。");
    console.error('  修法：补一个不含 DDL 的合并点把两头并起来 —— cd backend && uv run alembic merge -m "并头" <head1> <head2>');
    console.error("        （框架 CLI 只包了 run / revision / upgrade，没有 merge，得走原生 alembic）");
    console.error("        两域的表已经齐的那一侧，合并点之后要 alembic stamp head 只登记，别让迁移再建一遍表（ADR-0040）。");
    process.exit(1);
  }

  console.log(`迁移历史闸通过：${names.length} 份迁移，${roots.length} 个根（${roots.join("、")}）收成单一 head ${heads[0]}，每份的 down_revision 都指得到真实文件。`);
}

main();
