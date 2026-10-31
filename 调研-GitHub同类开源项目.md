# 给尼康 Z30 增加「七代机滤镜」——GitHub 同类项目调研

> 调研时间：2026-10-30 ｜ 参照对象：[QAQLeonard/a7c2-fl23-research](https://github.com/QAQLeonard/a7c2-fl23-research)（1012★，索尼 A7C II 固件 2.01 一键解锁 FL2/FL3 + 自定义创意外观）
>
> 结论先说：**GitHub 上目前没有「把尼康七代机滤镜移植进 Z30 机身」的现成项目**。但 Z30 这条线上有完整的三块地基，而且比索尼那条线更适合做「无砖机风险」的版本。

---

## 一、参照物到底做了什么

a7c2-fl23-research 的本质不是「写出新算法」，而是三件事：

1. **解密 + 重打包**索尼固件包（`appFw.so`、`cpapp-b.bin`），把旗舰机才有的 FL2/FL3 菜单项与色表写进去；
2. **拟合**：把富士 DCP / 理光 CUBE 离线拟合成机内色表（作者明说「是近似，不是 1:1」）；
3. **工程化**：GUI 一键、写入后逐字节回读、可一键恢复原厂。

所以「同类项目」要在这三个维度上找：谁能解密重打包尼康固件、谁能改机内色表、谁做了一键工具。

---

## 二、尼康阵营：地基已经很齐（同一批人，2026 年 10 月集中产出）

### 1. 最像的同类：[WonderLantern/wonder-lantern-z30](https://github.com/WonderLantern/wonder-lantern-z30) — 208★ / 30 fork

**就是尼康版的 Magic Lantern，而且机型正好是 Z30。** 面向 **Z30 固件 C 1.20**，用原厂 `Z_30_0120.bin` 打补丁，产出独立的 **EG 模块** `EG209020.BIN` + JSON 构建报告。

已实现（10 项，均有实机记录）：自定义快门声、M 档 900 秒长曝光、视频码率档位/慢动作解限、录像方向元数据、六种照片画幅+半透明遮幅、照片实时斑马纹、MTP 加速、Wi-Fi 5GHz 地区解锁、SD 原生模块宿主（ZMOD）。

**关键**: 它没有做优化校准/滤镜。但它证明了本项目最要命的两件事：

- **安装路径通了**：把 `EG209020.BIN` 放 SD 卡根目录，进「设定 → 固件版本」即可安装（issue #14 维护者确认）；
- **不需要攻克 K8 签名**：独立 EG 模块走的是模块级校验，不校验外层 `SHA1(正文||K8)`。这是 Z30 相对 Z8/Z9 的最大优势。

社区需求已经摆在 issue 里，但**没人实现**：

| Issue | 标题 | 状态 |
|---|---|---|
| #13 | 让机内滤镜位置多一倍可以吗（C1–C9 → 更多） | open |
| #15 | 胶片颗粒有可能吗 | open |
| #16 | 云创有没有戏? | open，回复是「除非能塞个 e7 进去」 |

### 2. 离线试验场：[WonderLantern/z30-qemu](https://github.com/WonderLantern/z30-qemu) — 13★

Z30 C 1.20 的 QEMU 整机模拟，能跑出固件自己渲染的菜单。同组织还有 [z7-qemu](https://github.com/WonderLantern/z7-qemu)(30★)、[z9-qemu](https://github.com/WonderLantern/z9-qemu)(26★)、[z63-qemu](https://github.com/WonderLantern/z63-qemu)(17★)、[z6-qemu](https://github.com/WonderLantern/z6-qemu)(9★)、[zr-qemu](https://github.com/WonderLantern/zr-qemu)(9★)。

**意义**：改色表/菜单这种「一改就可能开不了机」的活，可以先在模拟器里看菜单能不能出来，再去真机。a7c2 那条线没有这个条件，只能反复拔电池冷启动。

### 3. 固件格式与密钥：[WonderLantern/nikon-fw-key-tool](https://github.com/WonderLantern/nikon-fw-key-tool) — 57★

- **解密（XOR 表）支持**：Z5 / Z6 / Z6II / Z7 / Z7II / Z8 / Z9 / **Z30** / Z50 / Zfc ← Z30 在列
- **重签名重打包**：只支持 Z8 / Z9（K8 求解器依赖 EXPEED7 的 seed 构造方式；EXPEED6 机型「需要另一种恢复方法」，工具直接拒绝）

README 里把容器格式写得很干净：32 字节明文头 → 模块目录（16 字节名 + BE32 偏移 + BE32 长度）→ 模块体（各自 BE16 CRC）→ 包 CRC，CRC 用 `0x1021`、初值 0。**这是写自己的 patch 工具时最省时间的文档。**

### 4. 模块化运行时：[WonderLantern/Fenestra](https://github.com/WonderLantern/Fenestra) — 9★（10-29 刚开）

从 SD 卡加载独立 `.wlm` 模块的固件扩展运行时，带 SDK 与移植指南。当前 profile 只有 `zr-c110`、`z9-c532`。**如果目标是「加一个滤镜模块」而不是「改死固件」，这是正确方向**，但还没有 Z30 profile。

### 5. 其他可借力的

| 项目 | ★ | 用途 |
|---|---|---|
| [simeonpilgrim/nikon-firmware-tools](https://github.com/simeonpilgrim/nikon-firmware-tools) | 279 | NikonHacker 时代的解码源码（三张 XOR 表的原始出处） |
| [XinmiaoZhao/NikonFirmwarePatch](https://github.com/XinmiaoZhao/NikonFirmwarePatch) | 236 | Z8 ProRes 档位补丁，非官方固件补丁的工程范本 |
| [A2Sumie/NijiN](https://github.com/A2Sumie/NijiN) | 10 | Z8 source-only 工具链（给固件、本地构建、不附固件） |
| [ha1vk/wonder-lantern-z50-firmware](https://github.com/ha1vk/wonder-lantern-z50-firmware) | 3 | Z50 移植先例，看跨机型要改什么 |
| [PuzzleFF/nikon-z-region-unlock](https://github.com/PuzzleFF/nikon-z-region-unlock) | — | **纯 PTP 服务模式**改配置块（`0xF001`/`0xF02D`/`0xF02E`/`0xF030`），Z5/Zfc 实机验证，不碰固件 |

---

## 三、另一条更快的路：不改固件，直接写机内色表（推荐先做这个）

Z30 本来就有 **C1–C9 九个自定义优化校准槽**，官方支持导入 NP3/NCP。所以「七代机滤镜」在应用层就能落地：

### [laoziocean-code/z-cloud-ai](https://github.com/laoziocean-code/z-cloud-ai) — 2★（但技术含量最高）

尼康 Z30 的 Android App，手机 USB-OTG 直连相机，**用 PTP 私有指令把优化校准直接写进 C1–C9**。它把协议逆向得很透：

- `0x90CC` GetPictCtrlData（2 参数 `(slot,0)`）/ `0x90CD` SetPictCtrlData / `0x90CE` DelCstPicCtrl / `0x90CF` GetPicCtrlCapability
- 有效槽位是 **`0x00C9`–`0x00D1`**（不是网上流传的 `0x00CB`–`0x00D3`）
- **线上载荷 614 字节**（不是固件里那个 84 字节 SDS 结构）：
  - `+0x000` 2B 版本 `02 00`
  - `+0x002` 20B ASCII 名称
  - `+0x019` 1B 控制点字节数
  - `+0x02F` 8×4 组 (x,y) 控制点（只给 LCD 画曲线看）
  - `+0x066` **512B = 256 × u16 大端 LUT，值域 0..32767** ← 成像真值
- 握手细节（PTP Class Reset、清端点 HALT、OpenSession 事务号必须为 0、异步优先+同步降级）
- 200 套内置配方（176 套 nikonpc.com 真实 `.NCP`）
- 已知限制：`0x90C5`/`0x90C6`（曲线上传）在 Z30 上 `OperationNotSupported`；`0xD201` 不支持

### 同赛道的其他件

| 项目 | ★ | 用处 |
|---|---|---|
| [yekong-nasa/Z30-Recipes](https://github.com/yekong-nasa/Z30-Recipes) | 6 | iOS 端 NCP/NP3 批量导入 + Wi-Fi 写 C1–C9 |
| [ssssota/nikon-flexible-color-picture-control](https://github.com/ssssota/nikon-flexible-color-picture-control) | 18 | **NP3（灵活颜色）读写**，TS 实现 |
| [horshack-dpreview/NikonPictureControlsDev](https://github.com/horshack-dpreview/NikonPictureControlsDev) | 28 | 优化校准创建/修改的权威工具集 |
| [noobsaibot666/nikon_cps_generator-converter](https://github.com/noobsaibot666/nikon_cps_generator-converter) | 2 | 富士 recipe → 尼康自定义优化校准 |
| [b-z/Nikon-Recipes-to-LR-Profiles](https://github.com/b-z/Nikon-Recipes-to-LR-Profiles) | — | 云创配方 → Lightroom/Camera Raw 配置文件（拟合管线的一环） |
| [Cicakb1661/Nikon-Recipes](https://github.com/Cicakb1661/Nikon-Recipes) | 17 | 186 套配方合集 |

### 为什么这条路成立

- Z30 官方**不支持**尼康云创（Nikon Imaging Cloud），支持的是 Z6III / Zf / Z5II 这批 EXPEED7 机身；
- 但官方留了后门：用**尼康工坊（NX Studio）**把云创配方转成老机身认的自定义优化校准，拷 SD 卡导入即可；
- 也就是说「七代机滤镜」的数据本来就以 NP3 形式存在，Z30 只是没有「云创菜单」这个入口；
- 而 PTP `0x90CD` 能绕过 SD 卡/U 盘，直接把 614 字节塞进槽位 —— **这就是 a7c2 那个「一键」体验在尼康上的等价物，且几乎零砖机风险。**

### 但必须承认的近似

Z5II 那代是 **31 组内置预设 + 云创配方 + 灵活颜色优化校准（Color Blender / 调色）**。Color Blender 的多通道混色在 EXPEED6 上没有对应数据结构，只能塌缩成「曲线 + 256 级 LUT」。所以和 a7c2 的 DCP 拟合一样，**是近似，不是 1:1**——这条要在 README 里写明，不然会重演「刷完感觉差点意思」的评价。

---

## 四、三条路线的取舍

| | A. 应用层写色表 | B. 固件补丁（改 EG 模块） | C. 模块运行时 |
|---|---|---|---|
| 代表项目 | z-cloud-ai / Z30-Recipes | wonder-lantern-z30 | Fenestra |
| 能做到 | 9 个槽位内塞任意滤镜、可回滚 | 加槽位、加「云创」菜单、胶片颗粒 | 从 SD 卡加载可插拔功能模块 |
| 砖机风险 | 极低（读回校验 + 备份） | 中（需要 SD 卡刷 EG 模块） | 中低（可停用模块） |
| 卡点 | 614B LUT 表达力有限 | 要在 EG 里定位 PC 表结构 + ISP 管线入口 + 菜单表 | 没有 Z30 profile，要自己 port |
| 工期量级 | 周 | 月～季 | 月 |

---

## 五、建议的项目路线（给本仓库）

**Phase 0（1–2 周，高确定性）**：做一个桌面版「Z30 滤镜工具箱」，对标 a7c2-fl23-research 的形态
- Python + libusb/PTP，或复用 z-cloud-ai 的 Kotlin 逻辑改写成 CLI
- 输入：云创 NP3 / nikonpc `.NCP` / 任意 sRGB→sRGB LUT
- 拟合：recipe → 曲线 + 256 级 LUT → 614 字节载荷（参考 ssssota 的 NP3 解析 + b-z 的 recipe 解析）
- 写入：`0x90CC` 先读回原槽位备份 → `0x90CD` 写入 → `0x90CC` 读回逐字节核对
- 输出：可一键恢复 + 写 JSON 日志（对齐 WonderLantern 的构建报告风格）

**Phase 1（并行）**：建立「拟合质量」评测
- 同一张 NEF，分别用 NX Studio 套云创配方 vs 我们写进机身的版本，做 ΔE 统计
- 明确公布误差，别学「绝了/封神」那套话术

**Phase 2（可选，真要做固件级）**
- fork wonder-lantern-z30，先在 z30-qemu 里定位：优化校准表的读取路径、C1–C9 槽位数量上限、菜单项表
- 目标功能优先级：① 槽位 9→18（issue #13）② 胶片颗粒（#15）③ 云创菜单入口（#16）
- 安装仍走「SD 根目录 + 固件版本页」，不需要 K8

**别做的事**：直接去啃 K8 签名重打包。Z30 是 EXPEED6，现有求解器不支持，而且用不上（模块级路径已经通了）。

---

## 六、风险清单

- 刷 EG 模块失败 = 相机可能不进系统；`EG209020.BIN` 的恢复只能靠再刷一次原厂 C1.20（前提是还能进固件版本页）
- PTP 写入虽安全，但**先备份 C9 再动**，别一上来写 C1
- 保修：改动固件/写入非官方数据，送修可能被拒
- 云创配方有版权/授权问题：社区合集（144 款打包）来源混杂，二次分发要谨慎
- 固件升级会失效：wonder-lantern-z30 绑定 **C 1.20**，官方一旦推 C 1.21，补丁全废
