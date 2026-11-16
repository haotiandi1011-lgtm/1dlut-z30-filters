# 交接：iOS 端（下一个对话从这里开始）

> 写于 2026-11-16，对应仓库状态 v0.2.1（main = b95dcb7，tag v0.2.1）。
> 本文件是给「下一个对话里的助手」看的：先读完它，再动手。

## 0. 一句话

把**安卓端**（`android/`，Kotlin + Compose）的能力迁到 iOS（SwiftUI），**页面布局与交互尽量一比一**；
但 iOS 有两个硬前提必须先验证（见 §2），**不要先写代码再发现做不了**。

## 1. 必读顺序

1. `.workbuddy/memory/MEMORY.md` + 最近的日报（`2026-11-10.md`、`2026-11-09.md`）——环境坑、约定、踩过的雷
2. `产品约定.md`——数据目录、升级承诺、**预设 id 不可改不可复用**、删除/回收站语义
3. `产品规划.md`——能力边界（1D 曲线）、命名规范（界面不得出现厂商商标）
4. `真机发现-载荷结构.md`——614 字节载荷逐字段
5. `设计决策-为什么是1D.md`——为什么不做 3D
6. `CHANGELOG.md`——0.2.1 改了什么
7. `交接-安卓端与开源.md`——安卓端结构、协议细节

## 2. 两个硬前提（第一步就要验证并回报，别跳）

### 2.1 iOS 能不能直连相机写入？——大概率**不能**

- iOS 第三方 App **没有 USB Host / 原始 PTP 访问能力**；`ExternalAccessory` 只对 **MFi 认证配件**开放，
  相机不是 MFi 配件；
- 因此安卓端那套「USB Host + PTP（0x90CC 读 / 0x90CD 写）」在 iOS 上**没有对应 API**；
- 需要真机确认的只有一件事：Z30 的 **Wi-Fi/蓝牙（SnapBridge）** 是否暴露可用的 PTP-over-IP 通道给第三方。
  结论很可能是「没有公开 SDK，做不了」，但**要用实测或文档证据说话**，不要凭印象下结论。

**如果确认不能写机身，方案必须改成**（推荐 A）：

- **A. iOS 做「曲线工厂」**：内置 121 套预设、导入参考（照片/`.cube`/`.z30`）、拟合、预览对比、
  导出 `.z30` 配方包；写入交给安卓端或桌面端（两个端都已成熟）。这也是最诚实的产品定位。
- B. 走 MFi 定制配件（成本与周期都不现实，不建议）；
- C. 等厂商开放接口（不可控，不作方案）。

> 这一条必须先跟用户确认口径：**「功能一致」实际是「除写机身外一致」**，
> 否则做完了才发现核心功能缺失，返工代价最大。

### 2.2 构建环境：本机是 Windows，iOS 必须 macOS

- 本机没有 Xcode；可行路径：
  1. **GitHub Actions `macos-latest`** 出**未签名 `.ipa`**（`xcodebuild -sdk iphoneos CODE_SIGNING_ALLOWED=NO` 打包 Payload）
     → 用 Sideloadly / AltStore 侧载（免费 Apple ID 签名 7 天有效）；
  2. 用户有 Mac 时本地 Xcode 打包（最省事，但需要设备与账号）；
  3. TestFlight 需要付费开发者账号（$99/年）——先问用户有没有。
- 交付物按路径 1 设计：CI 出 ipa + 一段侧载说明；不要假设本机能编译。

## 3. 页面与功能对齐清单（以安卓端为准）

安卓端页面（`android/.../ui/WriterScreen.kt`）是单页滚动，顺序如下：

| 模块 | 安卓端行为 | iOS 可行性 |
|---|---|---|
| 设备卡 | 相机连接状态、USB 权限、槽位刷新/断开 | ❌ 无 USB Host，做不了 |
| 目标槽位卡 | C1–C9 九宫格，显示空/名称/仅参数，推荐 C9 | ❌ 依赖读机身 |
| 从照片生成曲线 | 选照片 → 分位数匹配 → 256 点曲线 | ✅ PhotosPicker + vImage |
| 曲线来源卡 | 内置预设 / 导入 .z30 / 照片生成 | ✅ |
| 内置预设库 | 121 套，分类折叠、搜索、隐藏/恢复、**逐条改名** | ✅（同一份资源） |
| 写入内容卡 | 曲线名称（≤19 ASCII）+ 锐化/清晰度/饱和度整数级 | ⚠️ 只能生成载荷/导出 .z30，不能写 |
| 照片对比卡 | 选照片、拖动分割线对比、导出 PNG/JPG | ✅ CoreImage + 手势 |
| 配方列表 | 导入 .z30、删除进回收站、恢复、彻底删除、**改名** | ✅ Files/文档目录 |
| 本机备份 | 写前备份 614 字节原槽位 | ❌ 无写入则无意义 |
| 运行日志 | 协议交互/校验记录 | ✅ 改为「操作日志」 |

**结论**：iOS 端 ≈ 安卓端去掉「设备/槽位/写入/备份」四个卡，保留「预设、拟合、对比、配方、导出」，
并把 `.z30` 导出做成主出口（分享面板 → 传给安卓机或电脑）。

## 4. 技术映射建议

| 安卓 | iOS 对应 |
|---|---|
| Kotlin + Jetpack Compose | SwiftUI（iOS 16+），MVVM |
| `core/Payload.kt` 614 字节 | 结构体 + `withUnsafeBytes`，布局见 §5 |
| `core/Recipe.kt`（`.z30`） | `Data` 读写：8 字节魔数 `Z30PC1\0\0` + 4 字节 metaLen(LE) + JSON + 614B |
| `core/fit/ToneCurve.kt`、`PhotoFit.kt` | Swift 移植（Double 数组 + Accelerate/vImage） |
| `core/fit/PhotoLoader.kt` | `PhotosPicker` + `CGImageSource`（HEIC 原生支持） |
| `data/BuiltinPresets.kt` + `assets/builtin_presets.json` | 直接把同一份 JSON 放进 bundle（121 套，base64 payload），**天然双端一致** |
| `data/Stores.kt`（配方/回收站/隐藏/别名） | `FileManager` 文档目录 + `UserDefaults` 存别名/隐藏 |
| `ui/WriterScreen.kt` | 按 §3 的卡片顺序重排；分割线对比用 `ZStack` + `mask` |

**一致性验收标准**：用 `android/app/src/test/resources/fit-golden.txt` 里的金标准，
在 Swift 里复算同一组输入的 256 点曲线，**逐点相等**（这是安卓/桌面已经做到的，iOS 不能例外）。

## 5. 614 字节载荷（iOS 端必须一模一样）

| 偏移 | 长度 | 内容 |
|---|---|---|
| 0x00 | 4 | 版本标识（0x05 家族） |
| 0x04 | 512 | 256 点 × u16 LE（0–32767），成像真值 |
| 0x204 | 2 | 控制点数量 |
| 0x206 | 2 | 保留 |
| 0x208 | 4×3 | 锐化 / 清晰度 / 饱和度（值 ×4 存储） |
| 0x21C | 20 | 机身名（ASCII，≤19 字符 + 结束符） |
| 0x230 | 其余 | 保留与校验 |

写 `.z30` 时：`metaLen` 小端写在偏移 8；JSON 里 `name/source/tool/created/metrics` 与安卓一致。

## 6. 工程约定与红线（与两端一致，别自己发明）

- **预设 id 不可改、不可复用**：改名只改 `name`；隐藏/回收站/别名都挂在 id 上；
- **公开版命名中性**：界面/资源里不得出现厂商/型号（`Fuji`、`Kodak`、`尼康 Z30` 仅可用于技术说明）；
  个人版靠覆盖层（桌面 `z30fit/personal_names.py`，安卓 `personal_names` 机制）——**覆盖层与个人版构建脚本都不入库**；
- **不动固件、不刷机**；只写 C1–C9，首次建议 C9；
- **版本口径**：产品版本 0.2.x；`versionCode`/iOS `CFBundleVersion` 只增不减（已装用户不能降级）；
- **发布红线**：commit/push/tag/Release/npm 都要**用户明确说「可以」**才做；软著材料（`软著材料/`）不入库；
- 任何「复刻某滤镜」的说法都不要用，统一「近似 + 偏差报告」。

## 7. 环境坑（本机）

- **中文路径**：Gradle 单测在中文路径下会 `ClassNotFoundException` → 测试跑 `D:\Z30Filters\android`（robocopy 同步）；
- **JDK**：`C:/Program Files/Microsoft/jdk-17.0.11.9-hotspot`；Gradle 用 `D:\tools\gradle-8.9\bin\gradle --offline`；
- **Python 3.13**，无 scipy/pytest；桌面测试 `python tests/run_tests.py`（37 项）；
- **重打 exe**：先 `taskkill` 掉运行中的 exe，旧 `dist/Z30影调工具` 先改名再 build（会被批量删除守卫拦）；
- **代理**：`git push`/GitHub API 需要梯子；关着会 `Connection reset`；
- **Android 出包**：`assembleDebug` 在中文路径没问题，只有**测试**要英文副本。

## 8. 交付物（DoD）

1. `ios/` 目录：可编译的 SwiftUI 工程（Xcode 15 / iOS 16+）；
2. 121 套预设随包（同一份 `builtin_presets.json` 转成 iOS 资源）；
3. `.z30` 导出可用：分享/存储到「文件」App；
4. 与金标准逐点一致的曲线测试（XCTest）；
5. CI：`macos-latest` 出未签名 `.ipa`，附侧载说明；
6. `README` 增加 iOS 章节（怎么装、能做什么、**不能做什么**——必须写明不能直连相机写入）。

## 9. 第一个动作

不要直接开写。先做这三件事并把结论回报给用户：

1. 用文档/实测确认 **iOS 无法 USB 直连 Z30 写 PTP**（给出依据），并把「iOS 只做曲线工厂 + `.z30` 导出」作为方案 A 请用户确认；
2. 确认用户的 **Apple 账号情况**（免费 / 付费开发者）与**是否有 Mac**，决定出包路径；
3. 给出 3 步计划与目录结构，等用户点头再开工。
