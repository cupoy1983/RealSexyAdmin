#!/usr/bin/env node
/**
 * scripts/ —— 仓库根级的开发辅助工具，只放「不随业务模块删走」的脚本。
 *
 * adr.mjs —— ADR 的校验者与索引生成器。**它不发号**：号由业务方配（2026-11-20 改判，`ADR-0042`）。
 *
 *   node scripts/adr.mjs check             校验：号不重、文件名与文内号一致、引用能解析到真实文件、引用没带域名、占位不许被引用、相对链接不指空；号段有洞只提醒不判红（占位允许不推）
 *   node scripts/adr.mjs index             重新生成 docs/adr/README.md（索引）与 CONTEXT-MAP.md（语境地图）
 *
 * 2026-11-20 改判（业务方定）：`new`、`reserve`、`renumber` 三条已删——号只从一只手出去才真不撞，
 * 而 `renumber` 自己算 `max+1`，那是仓库里最后一条自助定号的路。写一条决议的流程是：
 * 向业务方要号段（**起止都由他报**）→ 他建好 `NNNN-占位.md` 空壳 → agent **直接改那一份文件**的标题、状态、
 * 正文，号不动。占位允许不推（业务方 2026-11-20 定），所以对面看不见我这截空壳是预期行为，
 * `check` 的「号段有洞」从此**只提醒不判红**——代价是「46 敲成 47、漏建一份」不再有机器兜底，
 * 只剩重号、文件名与文内号不一致这两条还判红。号真写错了：按业务方报的那个号手工改
 * （`git mv` 文件名 + 改正文的 `# ADR-NNNN` 那行），再跑 `index`。
 *
 * 2026-11-12 统一化（业务方 2026-11-09 拍板，shared-rules §六.3）：
 *   ADR 一律平铺在 docs/adr/，号全仓一条连续序列，引用只有一种写法 `ADR-<NNNN>`。
 *   「按业务域分目录 + 号只在域内唯一 + 没有域的位置必须带域名限定词」这套连同豁免棘轮一起删掉了。
 *   domains.json 仍然登记业务域，但它只用来生成 CONTEXT-MAP.md（术语表与代码领土在哪），
 *   不再参与发号，也不再参与引用判定。
 */

import { readFileSync, writeFileSync, existsSync, readdirSync, statSync, realpathSync } from "node:fs";
import { join, dirname, relative, extname } from "node:path";
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

/** 占位 = 状态写成「占位（日期）」的那份 ADR：号已占住，内容还没写（`ADR-0042`）。 */
const isPlaceholder = (r) => /^占位/.test(r.status);
const unfilledPlaceholders = () => listAdr().filter(isPlaceholder);

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
  const notices = [];
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
    // 占位允许不推（业务方 2026-11-20 定），而"没推的空壳"和"号敲错了"在本地看不出区别，
    // 所以这一条从判红降成提醒：它只负责让人看见，不再负责拦下对面那一次提交。
    const ranges = asRanges(holes);
    notices.push(`号段有洞：${ranges.slice(0, 8).join("、")}${holes.length > 8 ? `…共 ${holes.length} 个号` : ""} —— 占位可以不推，洞现在只提醒不判红；但这也是说「46 敲成 47、漏建一份」从此没有机器兜底，只剩重号、文件名与文内号不一致这两条还判红`);
  }

  // 2) 引用可解析：带前缀的号，以及区间里的裸尾号
  const citations = collectCitations();
  const byNum = new Map(listAdr().map((r) => [r.num, r]));
  for (const c of citations) {
    if (!present.has(c.num)) errs.push(`${c.file}:${c.line} 引用了不存在的一条：ADR-${c.num}（docs/adr/ 里没有这个号）`);
  }
  for (const t of collectRangeTails()) {
    if (!present.has(t.num)) errs.push(`${t.file} 区间/并写里的裸尾号 ${t.num} 指不到真实文件 —— 这类写法重排号段时最容易漏改`);
  }
  // 2b) 引用不许指向还空着的占位（`ADR-0042` 的占位批次）：号占住了不等于决议存在，
  //     引用一份空壳等于把一个还没定的口径当成已定。占位自己标题里那一句不算引用——
  //     `# ADR-0043：占位（内容待写）` 这一行本来就写着它自己的号，拦它等于让每份空壳一出生就红。
  for (const c of citations) {
    const r = byNum.get(c.num);
    if (r && r.path !== c.file && isPlaceholder(r)) errs.push(`${c.file}:${c.line} 引用了还空着的占位 ADR-${c.num}（docs/adr/${r.name} 状态：${r.status}）—— 先把那份填了再引用，或改写成「待 ADR-${c.num} 落定」`);
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
  for (const n of notices) console.warn(`提醒：${n}`);
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
    "生成命令：`node scripts/adr.mjs index`。**号由业务方配**（`ADR-0042`）：要写决议先向他要号段，他建好 `NNNN-占位.md` 空壳推过来，你直接改那一份文件，不要手填编号。",
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
  const ph = unfilledPlaceholders();
  const phNote = ph.length ? `；未填占位 ${ph.map((r) => `ADR-${r.num}`).join("、")}` : "";
  out.push("", `当前最大号 ADR-${last}${phNote}。写决议**不走工具发号**：向业务方要号段（起止都由他报），他建好空壳推过来，你直接改那一份文件的标题、状态、正文，号不动。引用一律写 \`ADR-<NNNN>\`，号前不加域名。`, "");
  writeFileSync(CONTEXT_MAP_PATH, out.join("\n"), "utf8");
  console.log(`已生成 ${rel(CONTEXT_MAP_PATH)}`);
}

function fail(msg) {
  console.error(msg);
  process.exit(1);
}

const [cmd] = process.argv.slice(2);
if (cmd === "new" || cmd === "reserve" || cmd === "renumber") fail(`\`${cmd}\` 已在 2026-11-20 删掉：号由业务方配，agent 不自行发号，也不自己算 max+1（ADR-0042）。要写一条决议，直接改他建好的那份 docs/adr/NNNN-占位.md 空壳（标题、状态、正文，号不动）；手上没有空壳就先向业务方要号段。`);
else if (cmd === "check") cmdCheck();
else if (cmd === "index") cmdIndex();
else fail("用法：node scripts/adr.mjs check|index（发号的 new／reserve／renumber 已删，号由业务方配）");
