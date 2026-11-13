#!/usr/bin/env node
/**
 * scripts/ —— 仓库根级的开发辅助工具，只放「不随业务模块删走」的脚本。
 *
 * adr.mjs —— ADR 编号的唯一发放者与校验者。人不手填编号，就不可能撞号。
 *
 *   node scripts/adr.mjs new <标题>        发一条新 ADR（取全仓下一个号），打印可粘贴的引用写法
 *   node scripts/adr.mjs check             校验：号不重不断、引用能解析到真实文件、引用没带域名、相对链接不指空
 *   node scripts/adr.mjs index             重新生成 docs/adr/README.md（索引）与 CONTEXT-MAP.md（语境地图）
 *   node scripts/adr.mjs renumber <文件>   给刚新建、还没被任何地方引用的 ADR 重发一个号（并行开发撞号时用）
 *
 * 2026-11-12 统一化（业务方 2026-11-09 拍板，shared-rules §六.3）：
 *   ADR 一律平铺在 docs/adr/，号全仓一条连续序列，引用只有一种写法 `ADR-<NNNN>`。
 *   「按业务域分目录 + 号只在域内唯一 + 没有域的位置必须带域名限定词」这套连同豁免棘轮一起删掉了。
 *   domains.json 仍然登记业务域，但它只用来生成 CONTEXT-MAP.md（术语表与代码领土在哪），
 *   不再参与发号，也不再参与引用判定。
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, statSync, renameSync, realpathSync } from "node:fs";
import { join, dirname, relative, basename, extname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const REGISTRY_PATH = join(ROOT, "docs/adr/domains.json");
const ADR_DIR = join(ROOT, "docs/adr");
const INDEX_PATH = join(ROOT, "docs/adr/README.md");
const CONTEXT_MAP_PATH = join(ROOT, "CONTEXT-MAP.md");

// 引用扫描范围：文档 + 代码，一处不漏
const SCAN_DIRS = ["docs", "backend/app", "backend/tests", "frontend/web/src"];
// 2026-11-13 D5：术语表统一住 docs/<域>/CONTEXT.md，已被上面 docs 那一趟遍历覆盖，不再进根级清单。
// 以前这里是 ["CONTEXT.md", "GLOSSARY.md", ...]，两份词表一名一姓、一处一个，根级清单就是它们的户口本。
const SCAN_FILES = ["AGENTS.md", "README.md", "CONTRIBUTING.md", "REQUIREMENTS.md"];
// 工具自己生成的文件不参与扫描，否则索引里那一堆号会被当成引用
const SKIP_FILES = new Set(["docs/adr/README.md", "docs/adr/domains.json", "CONTEXT-MAP.md"]);
const SKIP_DIR_PREFIXES = ["node_modules", "__pycache__", "dist", ".git", ".backup", ".workbuddy", ".codebuddy", ".agent", ".tmp_", ".plan"];
const TEXT_EXT = new Set([".md", ".py", ".ts", ".vue", ".js", ".mjs", ".json", ".sql", ".yaml", ".yml"]);

const rel = (p) => relative(ROOT, p).replaceAll("\\", "/");
const read = (p) => readFileSync(p, "utf8");
const pad = (n) => String(n).padStart(4, "0");
const registry = () => (existsSync(REGISTRY_PATH) ? JSON.parse(read(REGISTRY_PATH)).domains || {} : {});

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (SKIP_DIR_PREFIXES.some((s) => name.startsWith(s))) continue;
      walk(full, out);
    } else if (TEXT_EXT.has(extname(name)) && !SKIP_FILES.has(rel(full))) {
      out.push(full);
    }
  }
  return out;
}

function scanFiles() {
  const found = [];
  for (const d of SCAN_DIRS) walk(join(ROOT, d), found);
  for (const f of SCAN_FILES) {
    const full = join(ROOT, f);
    if (existsSync(full) && !SKIP_FILES.has(rel(full))) found.push(full);
  }
  // 文件系统大小写不敏感：同名只差大小写的两项指的是同一个物理文件。
  // 不按真实路径去重，同一份文件会被扫两遍、引用计数翻倍。
  const seen = new Set();
  const out = [];
  for (const p of found) {
    const key = realpathSync(p).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(p);
  }
  return out.sort();
}

/** 全仓 ADR 清单：号、文件名、标题、状态 */
function listAdr() {
  if (!existsSync(ADR_DIR)) return [];
  const rows = [];
  for (const name of readdirSync(ADR_DIR)) {
    const m = name.match(/^(\d{4})-(.+)\.md$/);
    if (!m) continue;
    const text = read(join(ADR_DIR, name));
    const title = (text.match(/^#\s+(.+)$/m) || [, "（无标题）"])[1].replace(/^ADR-\d{4}：?\s*/, "");
    const status = (text.match(/^[-—>]\s*状态：\s*(.+)$/m) || [, "（未标）"])[1].trim();
    const idInFile = (text.match(/^#\s*ADR-(\d{4})/m) || [])[1];
    rows.push({ num: m[1], name, slug: m[2], title, status, idInFile, path: `docs/adr/${name}` });
  }
  return rows.sort((a, b) => a.num.localeCompare(b.num));
}

/** 下一个号 = 已用号的最大值 + 1。不用「第一个空号」，避免复用已废止决策留下的洞。
 *  excludePath 给 renumber 用：把正在改号的那条自己排除掉，这样号会往回收而不是继续往外跑。 */
function nextNumber(excludePath = null) {
  const nums = listAdr().filter((r) => r.path !== excludePath).map((r) => Number(r.num));
  return pad((nums.length ? Math.max(...nums) : 0) + 1);
}

/** 引用只有一种合法形状 `ADR-<NNNN>`。域名清单仍从 domains.json 取，
 *  这样以后新登记一个业务域，它自动进「不许写在号前面」的名单。 */
function labelPattern() {
  const labels = [...new Set(Object.values(registry()).map((d) => d.label).filter(Boolean))];
  return labels.length ? new RegExp(`(${labels.join("|")})\\s*[（(【\\[]?\\s*$`) : null;
}

function collectCitations() {
  const labelRe = labelPattern();
  const hits = [];
  for (const full of scanFiles()) {
    const fileRel = rel(full);
    const lines = read(full).split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      for (const m of lines[i].matchAll(/ADR-(\d{4})/g)) {
        const before = lines[i].slice(Math.max(0, m.index - 12), m.index);
        const label = labelRe ? (before.match(labelRe) || [])[1] || null : null;
        hits.push({ file: fileRel, line: i + 1, num: m[1], label, text: lines[i].trim().slice(0, 90) });
      }
    }
  }
  return hits;
}

/** 区间/并写里的裸尾号（`ADR-0015/0016`、`ADR-0007～0012`）：号没带 ADR- 前缀，但同样是引用。
 *  它最容易在重排号段时被漏改，所以 check 单独验它指得到真实文件。 */
function collectRangeTails() {
  const hits = [];
  for (const full of scanFiles()) {
    const text = read(full);
    for (const m of text.matchAll(/ADR-\d{4}((?:\s*[/～~–—-]\s*(?:ADR-)?\d{4})+)/g)) {
      for (const t of m[1].matchAll(/(\d{4})(?!\d)/g)) hits.push({ file: rel(full), num: t[1] });
    }
  }
  return hits;
}

function cmdNew(...titleParts) {
  const title = titleParts.join(" ").trim();
  if (!title) fail("用法：node scripts/adr.mjs new <标题>（标题一句话；号由工具取，不要自己写）");
  const num = nextNumber();
  const slug = title.replaceAll(/[\\/:*?"<>|]/g, "").replaceAll(/\s+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  mkdirSync(ADR_DIR, { recursive: true });
  const file = join(ADR_DIR, `${num}-${slug}.md`);
  if (existsSync(file)) fail(`文件已存在：${rel(file)}`);
  const today = new Date().toISOString().slice(0, 10);
  writeFileSync(file, `# ${title}\n\n> 状态：提议（${today}）\n\n`, "utf8");
  console.log(`已建：${rel(file)}`);
  console.log(`引用写法（全仓任何位置都一样，号前不加域名）：ADR-${num}`);
  cmdIndex();
}

function cmdRenumber(fileRelArg) {
  if (!fileRelArg) fail("用法：node scripts/adr.mjs renumber <ADR 文件路径>");
  const full = join(ROOT, fileRelArg);
  if (!existsSync(full)) fail(`找不到文件：${fileRelArg}`);
  const fileRel = rel(full);
  if (dirname(fileRel) !== "docs/adr") fail(`ADR 一律平铺在 docs/adr/ 下，这个不在：${fileRel}`);
  const m = basename(fileRel).match(/^(\d{4})-(.+)$/);
  if (!m) fail("文件名不是 NNNN-标题.md 格式");
  const num = nextNumber(fileRel);
  const target = join(dirname(full), `${num}-${m[2]}`);
  let text = read(full);
  if (/^#\s*ADR-\d{4}/m.test(text)) text = text.replace(/^(#\s*ADR-)\d{4}/m, `$1${num}`);
  renameSync(full, target);
  writeFileSync(target, text, "utf8");
  console.log(`重发号：${fileRel} → ${rel(target)}`);
  console.log("注意：renumber 只用于刚新建、还没被任何文档或代码引用的 ADR。已有人引用的号不要动。");
  cmdIndex();
}

/** 相对链接是否指得到真实文件：搬目录时靠这条命令兜底，不靠人眼 */
function checkLinks() {
  const bad = [];
  for (const full of scanFiles()) {
    if (extname(full) !== ".md") continue;
    const fileRel = rel(full);
    const text = read(full);
    for (const m of text.matchAll(/\]\(([^)\s]+)\)/g)) {
      const target = m[1].split("#")[0].split(" ")[0];
      if (!target || target.startsWith("http") || target.startsWith("mailto:") || m[1].startsWith("#")) continue;
      if (!target.startsWith("./") && !target.startsWith("../")) continue;
      const resolved = rel(join(dirname(full), target));
      if (!existsSync(join(ROOT, resolved))) bad.push(`${fileRel} → ${target}`);
    }
  }
  return bad;
}

/** 把洞压成区段，否则一个手填的 0099 会刷出 76 个号，没法读 */
function asRanges(nums) {
  const out = [];
  let start = null, prev = null;
  for (const n of [...nums].sort((a, b) => a - b)) {
    if (start === null) { start = n; prev = n; continue; }
    if (n === prev + 1) { prev = n; continue; }
    out.push(start === prev ? start : [start, prev]);
    start = n; prev = n;
  }
  if (start !== null) out.push(start === prev ? start : [start, prev]);
  return out.map((r) => (typeof r === "number" ? pad(r) : `${pad(r[0])}–${pad(r[1])}`));
}

function cmdCheck() {
  const errs = [];
  const rows = listAdr();

  // 1) 号段：不重、不断、文件名与文件内的号一致
  const present = new Set();
  for (const r of rows) {
    if (present.has(r.num)) errs.push(`${r.path} 号重复：ADR-${r.num} 已被占用`);
    present.add(r.num);
    if (r.idInFile && r.idInFile !== r.num) errs.push(`${r.path} 文件内写的是 ADR-${r.idInFile}，与文件名 ${r.num} 不一致`);
  }
  const max = Math.max(0, ...rows.map((r) => Number(r.num)));
  const holes = [];
  for (let n = 1; n <= max; n++) if (!present.has(pad(n))) holes.push(n);
  if (holes.length) {
    const ranges = asRanges(holes);
    errs.push(`号段有洞：${ranges.slice(0, 8).join("、")}${ranges.length > 8 ? `…共 ${holes.length} 个号` : ""} —— 号由工具发放，不要手填；新 ADR 用 node scripts/adr.mjs new <标题>`);
  }

  // 2) 引用可解析：带前缀的号，以及区间里的裸尾号
  const citations = collectCitations();
  for (const c of citations) {
    if (!present.has(c.num)) errs.push(`${c.file}:${c.line} 引用了不存在的一条：ADR-${c.num}（docs/adr/ 里没有这个号）`);
  }
  for (const t of collectRangeTails()) {
    if (!present.has(t.num)) errs.push(`${t.file} 区间/并写里的裸尾号 ${t.num} 指不到真实文件 —— 这类写法重排号段时最容易漏改`);
  }

  // 3) 引用不许带域名：不拦的话，「号只在域内唯一」这套会被新写的句子悄悄退回来
  for (const c of citations) {
    if (c.label) errs.push(`${c.file}:${c.line} 引用还带域名「${c.label}ADR-${c.num}」—— 号全仓唯一，写成 ADR-${c.num} 就行；域名只在正文里说这条属于哪个域`);
  }

  // 4) 测试目录的域名必须在登记表里（shared-rules T6/T7 的牙齿）。
  //    号段统一后「从目录推域」这条没了，若不补一条，「电商的脚本放进 live/internal/」就再没人拦。
  //    只管 tests 这一侧：backend/app/plugin 与 frontend 下还有上游自带的 module_system/module_task 等，
  //    把它们也当成业务域会让闸天天误报——那是「闸响得没道理」，不是安全。
  const domKeys = new Set(Object.keys(registry()));
  for (const full of scanFiles()) {
    const f = rel(full);
    const m = f.match(/^backend\/tests\/live\/([^/]+)\//) || f.match(/^backend\/tests\/module_([^/]+)\//);
    if (m && !domKeys.has(m[1])) {
      errs.push(`${f}：目录名「${m[1]}」不是 docs/adr/domains.json 里登记的业务域 —— 开新域目录先登记，再放文件（T6）`);
    }
  }

  const badLinks = checkLinks();
  let failed = false;
  if (errs.length) {
    failed = true;
    console.error(`引用与号段问题 ${errs.length} 项：`);
    for (const e of errs.slice(0, 60)) console.error("  - " + e);
    if (errs.length > 60) console.error(`  …另有 ${errs.length - 60} 项`);
  }
  if (badLinks.length) {
    failed = true;
    console.error(`断链 ${badLinks.length} 处：`);
    for (const b of badLinks.slice(0, 30)) console.error("  - " + b);
    if (badLinks.length > 30) console.error(`  …另有 ${badLinks.length - 30} 处`);
  }
  if (failed) process.exit(1);

  const doms = registry();
  console.log(`ADR check 通过：${rows.length} 条决策（0001–${pad(max)}，全仓一条序列），${citations.length} 处引用全部可解析且不带域名，相对链接全部指得到真实文件。`);
  const noGlossary = Object.entries(doms).filter(([, d]) => d.context && !existsSync(join(ROOT, d.context))).map(([k]) => k);
  if (noGlossary.length) console.log(`这些域还没建术语表：${noGlossary.join("、")}`);
}

function cmdIndex() {
  const doms = registry();
  const rows = listAdr();
  const lines = [
    "# ADR 索引（由工具生成，别手改）",
    "",
    "生成命令：`node scripts/adr.mjs index`。新建 ADR 用 `node scripts/adr.mjs new <标题>`，不要手填编号。",
    "",
    "**号全仓唯一，一条连续序列。** 2026-11-12 统一化后 ADR 全部平铺在本目录，引用在任何位置都只写 `ADR-<NNNN>`，号前不加域名。",
    "业务域仍登记在 `domains.json`，但它管的是术语表与代码领土在哪（见根目录 `CONTEXT-MAP.md`），不管编号。",
    "",
    "| 号 | 标题 | 状态 | 文件 |",
    "|---|---|---|---|",
  ];
  if (!rows.length) lines.push("| — | （还没有） | — | — |");
  for (const r of rows) lines.push(`| ADR-${r.num} | ${r.title} | ${r.status} | [${r.name}](./${r.name}) |`);
  lines.push("");
  const origins = Object.values(doms).filter((d) => d.origin).map((d) => `${d.label} ${d.origin}`).join("；");
  if (origins) lines.push(`> 号段来源：${origins}。2026-11-12 统一化之前的电商号 = 现号减 18，读那之前的老句子时用。`, "");
  writeFileSync(INDEX_PATH, lines.join("\n"), "utf8");
  console.log(`已生成 ${rel(INDEX_PATH)}`);
  writeContextMap(doms, rows);
}

/** CONTEXT-MAP.md：让通用技能（domain.md 那一套）找到每个域的术语表与代码领土在哪。同样由工具生成，别手改。 */
function writeContextMap(doms, rows) {
  const last = rows.length ? rows[rows.length - 1].num : "0000";
  const out = [
    "# 语境地图（由工具生成，别手改）",
    "",
    "生成命令：`node scripts/adr.mjs index`。域的增删改在 `docs/adr/domains.json`，不要直接编辑本文件。",
    "",
    "本仓库有多个业务域，各有自己的术语表与代码领土。**ADR 不分域**：全仓一条连续序列（当前 0001–" + last + "），引用一律写 `ADR-<NNNN>`，号前不加域名。",
    "",
    "| 域 | 中文名 | 术语表 | 代码领土 |",
    "|---|---|---|---|",
  ];
  for (const [key, dom] of Object.entries(doms)) {
    const ctx = dom.context && existsSync(join(ROOT, dom.context)) ? dom.context : "（未建）";
    const code = (dom.code_dirs || []).length === 0 ? "（不设领土）" : (dom.code_dirs || []).filter((c) => existsSync(join(ROOT, c))).join(" ") || "（还没进本仓库）";
    out.push(`| ${key} | ${dom.label || "—"} | ${ctx} | ${code} |`);
  }
  out.push("", `新建 ADR：\`node scripts/adr.mjs new <标题>\`（下一个号是 ${pad(Number(last) + 1)}）。**不要手填编号，也不要在号前加域名。**`, "");
  writeFileSync(CONTEXT_MAP_PATH, out.join("\n"), "utf8");
  console.log(`已生成 ${rel(CONTEXT_MAP_PATH)}`);
}

function fail(msg) {
  console.error(msg);
  process.exit(1);
}

const [cmd, ...args] = process.argv.slice(2);
if (cmd === "new") cmdNew(...args);
else if (cmd === "check") cmdCheck();
else if (cmd === "index") cmdIndex();
else if (cmd === "renumber") cmdRenumber(args[0]);
else fail("用法：node scripts/adr.mjs new|check|index|renumber [参数]");
