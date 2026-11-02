# z30tone-setup · Z30 影调工具的安装配置技能

一个**通用 Agent Skill**：把「Z30 影调工具」的完整安装配置流程教给你的 AI 助手，
装上之后对助手说一句「**帮我安装配置 Z30 影调工具**」，它会一步步带你走完
下载 → 启动 → 驱动 → 首次写入，出问题时还能照着排错表帮你定位。

WorkBuddy、Claude Code 等支持 SKILL.md 标准的助手都能用。

## 一条命令安装

```bash
npx z30tone-setup
```

安装器会自动检测本机的 agent 技能目录（WorkBuddy 的 `~/.workbuddy/skills/`、
Claude Code 的 `~/.claude/skills/`），把技能装进去；也可以指定：

```bash
npx z30tone-setup --workbuddy     # 只装 WorkBuddy
npx z30tone-setup --claude        # 只装 Claude Code
npx z30tone-setup --dir <路径>    # 装到任意平台的技能根目录
npx z30tone-setup --remove        # 卸载
```

## 安装前提（两条，先看）

1. **需要本机有 Node.js 18+**（`npx` 依赖它）。没有 Node 的用户别装 Node，走下面的「手动安装」。
2. **目标平台得支持 SKILL.md 技能机制**。支持：WorkBuddy、Claude Code，以及任何按
   SKILL.md 约定读取技能目录的平台（用 `--dir` 指到它的技能根目录）。
   **不支持**：豆包、DeepSeek 网页版这类没有「技能目录」概念的纯聊天产品——装了也没有东西去读它。
   判断方法：去那个平台的文档里搜 "skills" 或 "SKILL.md"。

## 手动安装（没有 Node / 平台不在自动检测范围）

技能本体就是一个文件夹，安装器只是搬运工。所以手动装等价：

1. 从仓库把 `skill/z30tone-setup/` 整个文件夹下载下来
   （`https://github.com/haotiandi1011-lgtm/1dlut-z30-filters/tree/main/skill`，
   或下载仓库 zip 后只取这一个文件夹）
2. 把它拷进目标平台的技能根目录，保证路径形如
   `<技能根目录>/z30tone-setup/SKILL.md`
3. 重启/刷新该平台，让它重新扫描技能

## 这个技能会帮用户做什么

- **桌面端（Windows）**：下载 exe → 启动 → Zadig 换 WinUSB 驱动（含副作用与还原方法）→ 首次写入 C9
- **安卓端**：APK 安装 → OTG 直连相机 → 选预设/照片拟合 → 写入与读回校验
- **排错**：错误码 -1、读超时、接口占用、一加/ColorOS 的 OTG 自动关闭、杀软误报……
- **说清边界**：不改固件、只写 C1–C9、只有一条 1D 曲线（做不了色偏/分离色调）

## 这个工具是什么

[Z30 影调工具](https://github.com/haotiandi1011-lgtm/1dlut-z30-filters)（MIT）：
把任意影调参考拟合成 256 点曲线，写进尼康 Z30 的自定义优化校准槽 C1–C9，
写入前给还原度报告。不改固件、不刷机。

## 维护者须知

- **npm 发布节奏与主项目一致**：`skill/` 随仓库开源后才能 `npm publish --access public`，
  在那之前不发布（主仓库的开源红线）。
- 本地自测时，**从英文路径跑安装器**：`cp -r skill /tmp/zskill && cd /tmp/zskill && node install.mjs`。
  仓库路径含中文时 node 会静默失败（Windows 下非 ASCII 路径的已知问题，与 npm 安装的用户无关）。
- 改 `SKILL.md` 后直接重跑安装器即可覆盖更新（幂等）。

## 许可

MIT。技能内容随主项目一起维护；发布节奏与主项目 Release 一致。
