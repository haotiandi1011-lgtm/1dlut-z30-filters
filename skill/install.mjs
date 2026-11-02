#!/usr/bin/env node
/**
 * z30tone-setup 的技能安装器。
 *
 *   npx z30tone-setup              # 装到检测到的所有 agent 技能目录
 *   npx z30tone-setup --workbuddy  # 只装 WorkBuddy（~/.workbuddy/skills/）
 *   npx z30tone-setup --claude     # 只装 Claude Code（~/.claude/skills/）
 *   npx z30tone-setup --dir <路径> # 装到指定目录（自定义 agent）
 *   npx z30tone-setup --remove     # 从检测到的目录里移除
 *
 * 零依赖，Node >= 16。
 */

import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SKILL_NAME = "z30tone-setup";
const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, SKILL_NAME);

const KNOWN_TARGETS = [
  { flag: "--workbuddy", label: "WorkBuddy", dir: join(homedir(), ".workbuddy", "skills") },
  { flag: "--claude", label: "Claude Code", dir: join(homedir(), ".claude", "skills") },
];

const args = process.argv.slice(2);

if (args.includes("--help") || args.includes("-h")) {
  console.log(`
z30tone-setup — 把「Z30 影调工具」的安装配置技能装进你的 AI 助手。

用法：
  npx z30tone-setup                自动装到检测到的 agent 技能目录
  npx z30tone-setup --workbuddy    只装 WorkBuddy
  npx z30tone-setup --claude       只装 Claude Code
  npx z30tone-setup --dir <路径>   装到任意目录
  npx z30tone-setup --remove       移除

装好后对助手说：「帮我安装配置 Z30 影调工具」即可开始。
`.trim());
  process.exit(0);
}

const removing = args.includes("--remove");
const explicitDir = (() => {
  const i = args.indexOf("--dir");
  return i >= 0 ? resolve(args[i + 1]) : null;
})();

// 传了具体 flag 就只装指定的；一个都没传才「检测到哪个装哪个」
const flagged = KNOWN_TARGETS.filter((t) => args.includes(t.flag));
const targets = explicitDir
  ? [{ flag: "--dir", label: "自定义目录", dir: explicitDir }]
  : flagged.length > 0
    ? flagged
    : KNOWN_TARGETS.filter((t) => existsSync(t.dir));

if (targets.length === 0) {
  console.error("× 没检测到 agent 技能目录，也没有 --dir。");
  console.error("  可以这样装到任意目录：npx z30tone-setup --dir <技能根目录>");
  process.exit(1);
}

if (!existsSync(join(src, "SKILL.md"))) {
  console.error(`× 包损坏：找不到 ${src}/SKILL.md，请重新安装。`);
  process.exit(1);
}

let failed = false;
for (const t of targets) {
  const dest = join(t.dir, SKILL_NAME);
  try {
    if (removing) {
      if (existsSync(dest)) {
        rmSync(dest, { recursive: true, force: true });
        console.log(`✓ 已从 ${t.label} 移除：${dest}`);
      } else {
        console.log(`- ${t.label} 里本来就没有：${dest}`);
      }
      continue;
    }
    mkdirSync(t.dir, { recursive: true });
    rmSync(dest, { recursive: true, force: true });
    cpSync(src, dest, { recursive: true });
    console.log(`✓ 已装入 ${t.label}：${dest}`);
  } catch (e) {
    failed = true;
    console.error(`× ${t.label} 安装失败：${e.message}`);
  }
}

if (!removing && !failed) {
  console.log(`
装好了。对助手说一句「帮我安装配置 Z30 影调工具」即可开始；
想卸载：npx z30tone-setup --remove`.trim());
}
process.exit(failed ? 1 : 0);
