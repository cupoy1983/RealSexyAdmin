#!/usr/bin/env node
/**
 * scripts/pii.mjs —— 可识别到个人的真实信息闸（规则正文见 docs/agents/shared-rules.md §3.4）。
 *
 * 扫的文件集合 = `git ls-files` + `git ls-files --others --exclude-standard`，
 * 也就是"一次 git add . 会被带上远端的那些文件"，被 .gitignore 挡住的（含 backend/env/.env.*）不看。
 *
 * 出场时机（业务方 2026-11-12 定）：**只在提交／发布前跑这一次**，日常改文档改代码不要求跑。
 * 理由：这仓是 public fork，命中一旦提交就从"能改"变成"要重写历史才能撤"，那一秒才是闸值钱的地方；
 * 而主控制是 §3.4 那条"agent 不写"。命中只有「改」一条路——豁免底账 `scripts/pii-waivers.json`
 * 已在 2026-11-12 降到 0 条并删除，本文件不再有任何登记／放宽的入口。
 *
 *   node scripts/pii.mjs                # 校验，有命中就 exit 1
 *   node scripts/pii.mjs --selftest     # 验闸自己：正向 6 条必须响、反向 6 条不许响
 */

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const TEXT_EXT = new Set([
  ".py", ".md", ".ts", ".tsx", ".vue", ".js", ".mjs", ".json", ".yaml", ".yml",
  ".sql", ".txt", ".scss", ".css", ".jinja2", ".sh", ".bat", ".ini", ".cfg", ".env", ".example", ".properties",
]);

// 上游自带 / 打包产物：里面的长数字是哈希和版本号，不是业务数据
const SKIP = [
  /^scripts\/pii\./, // 闸自己的探针里有假号码，不能自己抓自己
  /^backend\/static\/swagger\//,
  /^frontend\/docs\//,
  /(^|\/)node_modules\//,
  /(^|\/)dist\//,
  /(^|\/)__pycache__\//,
  /(^|\/)pnpm-lock\.yaml$/,
  /(^|\/)package-lock\.json$/,
  /(^|\/)uv\.lock$/,
  /(^|\/)go\.sum$/,
];

/** 一眼看得出是假的测试数据，不豁免也直接放行 */
const FIXTURE = {
  // 13800138000 / 13800138001 / 13900000001…06 / 13800000001 这类顺序占位号
  mobile: /^1(380013800[0-9]|390000000[0-9]|380000000[0-9])$/,
  name: /^(张三|李四|王五|赵六|测试|员工|示例|超级管理员|管理员|普通用户)$/,
};

const RULES = [
  {
    id: "MOBILE",
    why: "手机号。要复现就写 user_id / 主键，号码进本机运维记录",
    re: /(?<!\d)1[3-9]\d{9}(?!\d)/g,
    skip: (m) => FIXTURE.mobile.test(m),
  },
  {
    id: "LONG_ID",
    why: "16–18 位连续数字：订单号 / 钉钉 userid / 身份证形态（版本号 `3.0.0-408…` 不算）",
    re: /(?<![\dxw.:-])\d{16,18}(?![\dxw.:-])/g,
    skip: () => false,
  },
  {
    id: "NAME_PREDICATE",
    why: "姓名列被写成 2-4 个汉字（SQL 谓词或行文），定位请改用主键（oa_user_id / id）",
    re: /(?:user_name|external_name|real_name)\s*[=:]\s*['"`]([\u4e00-\u9fa5]{2,4})['"`]|(?:姓名|真名|用户名)\s*[=:：]\s*['"`]?([\u4e00-\u9fa5]{2,4})['"`]?/g,
    skip: (m, g1) => FIXTURE.name.test(g1),
  },
  {
    id: "NAME_RATIO",
    why: "「人名 + 出勤/金额比值」句式，等于把数字绑到具体人身上",
    re: /(?<=[、：:(（])\s*([\u4e00-\u9fa5]{2,3})\s+\d{1,2}\/\d{1,2}(?![\d/])/g,
    skip: (m, g1) => FIXTURE.name.test(g1),
    group: 1,
  },
  {
    id: "LOCAL_PATH",
    why: "本机绝对路径（含真实用户名）。写 `...` 或环境变量占位即可；容器卷映射 `a:/home/x` 不算",
    re: /(?<![A-Za-z0-9_./\\-])[A-Za-z]:[\\/](?:Users|home|Documents|Desktop)[^\s"'`)`\]]*/g,
    skip: (m) => m.includes("..."),
  },
];

function gitList(args) {
  const r = spawnSync("git", args, { cwd: ROOT, encoding: "utf8" });
  if (r.status !== 0) throw new Error(`git ${args.join(" ")} 失败：${r.stderr}`);
  return r.stdout.split("\n").filter(Boolean);
}

function candidateFiles() {
  const tracked = gitList(["ls-files"]);
  const others = gitList(["ls-files", "--others", "--exclude-standard"]);
  const out = new Set();
  for (const f of [...tracked, ...others]) {
    const rel = f.replaceAll("\\", "/");
    if (!TEXT_EXT.has(rel.slice(rel.lastIndexOf("."))) && rel.lastIndexOf(".") === -1) continue;
    if (!TEXT_EXT.has(rel.slice(rel.lastIndexOf(".")))) continue;
    if (SKIP.some((p) => p.test(rel))) continue;
    out.add(rel);
  }
  return [...out].sort();
}

function scan(files) {
  const hits = [];
  for (const rel of files) {
    let text;
    try {
      text = readFileSync(resolve(ROOT, rel), "utf8");
    } catch {
      continue;
    }
    text.split(/\r?\n/).forEach((line, idx) => {
      for (const rule of RULES) {
        rule.re.lastIndex = 0;
        let m;
        while ((m = rule.re.exec(line)) !== null) {
          const g1 = Array.from(m).slice(1).find((v) => v !== undefined) ?? m[0];
          if (rule.skip(m[0], g1)) continue;
          hits.push({ file: rel, line: idx + 1, rule: rule.id, token: mask(m[0]) });
        }
      }
    });
  }
  return hits;
}

/** 输出里绝不复现原文：只留前 2 后 2，够定位就行 */
function mask(tok) {
  const t = tok.trim();
  if (t.length <= 4) return t[0] + "*".repeat(Math.max(1, t.length - 1));
  return `${t.slice(0, 2)}${"*".repeat(4)}${t.slice(-2)}`;
}

function keyOf(h) {
  return `${h.file}#${h.rule}`;
}

/**
 * 闸自己的探针：正向必须响，反向必须不响。误报一次，两边 agent 就会开始无视这个闸。
 * 探针一律用**编造的**号码/人名/用户名：这个文件会被 SKIP 掉不扫，但它是公开的，
 * 写真实值就等于把泄漏源藏进闸自己看不见的地方——§3.4 的反面教训不许由闸自己犯。
 * 具体到手机号：正向探针必须是 FIXTURE 不认的形状（否则不响），同时又不能是库里取出来的号，
 * 因为本文件被 SKIP，抄进库里真号就再没人查得到（2026-11-12 实测踩过：探针 135 段与开发库同值）。
 */
const SELFTEST = [
  ["13512340000 走完整链路", "MOBILE"],
  ["订单 6000123456789012，实测从第4490行起", "LONG_ID"],
  ["钉钉 `012345678901234567`", "LONG_ID"],
  ["WHERE user_name = '张三丰'", "NAME_PREDICATE"],
  ["同考勤组四人：沈某 23/17、", "NAME_RATIO"],
  ['NEW_FILE = r"C:\\Users\\tester\\Desktop\\12345.xlsx"', "LOCAL_PATH"],
  ['"@dcloudio/uni-app": "3.0.0-4080520251106001",', null],
  ["- ../backend/static/upload:/home/static/upload", null],
  ['row = {"mobile": "13900000001"}', null],
  ['user_name="张三"', null],
  ["散在 `C:/Users/.../Temp/`，不在仓库", null],
  ["comment='姓名快照'", null],
];

function selftest() {
  let bad = 0;
  for (const [line, expect] of SELFTEST) {
    const fired = RULES.filter((rule) => {
      rule.re.lastIndex = 0;
      const m = rule.re.exec(line);
      return m !== null && !rule.skip(m[0], Array.from(m).slice(1).find((v) => v !== undefined) ?? m[0]);
    }).map((r) => r.id);
    const ok = expect === null ? fired.length === 0 : fired.includes(expect);
    if (!ok) {
      bad += 1;
      console.log(`  ✗ 探针不过：${expect ?? "不应命中"} ← "${line.slice(0, 40)}" 实得 [${fired.join(",")}]`);
    }
  }
  console.log(bad ? `\n✗ pii.mjs 自检 ${bad} 项不过` : `✓ pii.mjs 自检 ${SELFTEST.length} 项探针全过（正向 ${SELFTEST.filter((s) => s[1]).length} / 反向 ${SELFTEST.filter((s) => !s[1]).length}）`);
  process.exit(bad ? 1 : 0);
}

function main() {
  if (process.argv.includes("--selftest")) selftest();
  if (process.argv.includes("--record")) {
    console.error("✗ --record 已随豁免底账一起删除（2026-11-12）：命中只有「改」一条路，理由见 docs/agents/shared-rules.md §3.4");
    process.exit(1);
  }
  const files = candidateFiles();
  const hits = scan(files);
  const byKey = {};
  for (const h of hits) (byKey[keyOf(h)] ||= []).push(h);

  console.log(`扫描 ${files.length} 个文件（tracked + 未跟踪且未被 ignore），命中 ${hits.length} 行`);
  for (const rule of RULES) {
    const n = hits.filter((h) => h.rule === rule.id).length;
    console.log(`  ${rule.id.padEnd(15)} ${String(n).padStart(4)} 行   ${rule.why}`);
  }

  if (!hits.length) {
    console.log(`\n✓ 敏感信息闸通过：${files.length} 个文件零命中（没有豁免底账，也没有可登记的地方）`);
    return;
  }

  console.log(`\n✗ 敏感信息闸不过：${hits.length} 行命中，分布在 ${Object.keys(byKey).length} 个「文件 × 规则」上`);
  for (const [k, list] of Object.entries(byKey)) {
    console.log(`  ${k} —— ${RULES.find((r) => r.id === k.split("#")[1]).why}`);
    for (const h of list.slice(0, 6)) console.log(`      ${h.file}:${h.line}  ${h.token}`);
    if (list.length > 6) console.log(`      …另有 ${list.length - 6} 行`);
  }
  console.log("\n修法只有一条：按 §3.4 换成主键／占位／中性描述。");
  process.exit(1);
}

main();
