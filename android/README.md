# Z30 写入端（Android）

把电脑上拟合好的 `.z30` 配方，写进尼康 Z30 的自定义影调槽位 C1–C9，写完立刻读回逐字节校验。

**手机只做三件事：读配方 → 发 614 字节 → 读回校验。不做任何拟合。**

> **实测环境**：iQOO Neo9 + 尼康 Z30，C-to-C 线直连。写入 C9 → 读回逐字节一致 → 相机成像正常、参数可调。

## 为什么只做写入

拟合要跑矩阵运算和 ΔE 统计，桌面端已经做完了。手机上重做一遍既慢，又容易和桌面算出来的曲线对不上。
`.z30` 配方包就是两端交接的格式：载荷是唯一真值，元数据只是备注。

## 能做什么

| 能力 | 说明 |
|---|---|
| 导入配方 | `.z30` 配方包；也接受裸的 614 字节载荷 `.bin` |
| 读槽位 | 连接后一次读出 C1–C9 的名称与形态（完整配方 / 仅参数 / 空） |
| 写入槽位 | 默认 C9，可改；写前自动把原内容备份到手机 |
| 改名称 | ≤19 字符 ASCII（机身字段限制，中文会被替换成 `?`） |
| 改参数 | 锐化 / 清晰度 / 饱和度，跟着曲线一起进机身 |
| 读回校验 | 写完立刻读回，逐字节比对，不一致直接报错 |
| 恢复 | 从本机备份把某个槽位写回去（仅 614 字节备份可恢复） |

**不做**：拟合曲线、整体色偏、分色相调整、删除槽位（`0x90CE` 是破坏性的，本应用不实现）。

## 构建

### 方案一：GitHub Actions（推荐，不用装 Android SDK）

推到 `main` 自动构建，或在 Actions 页面手动 `Run workflow`。
产物在本次运行的 **Artifacts → `z30tone-writer-apk`**，下载解压即可安装。

需要发布签名时，在仓库 Secrets 里配这四项，工作流会额外产出签名的 release 包：

| Secret | 内容 |
|---|---|
| `Z30_KEYSTORE_BASE64` | keystore 文件的 base64（`base64 -w0 z30.jks`） |
| `Z30_STORE_PASSWORD` | keystore 口令 |
| `Z30_KEY_ALIAS` | 密钥别名 |
| `Z30_KEY_PASSWORD` | 密钥口令 |

打 tag（`v0.1.0`）时会自动把 APK 挂到对应的 GitHub Release。

### 方案二：本地构建

要求：JDK 17、Android SDK Platform 35、Build-Tools 35。

```bash
cd android
echo "sdk.dir=/path/to/Android/Sdk" > local.properties   # Windows 用 D:/tools/android-sdk 这种正斜杠写法
./gradlew :app:assembleDebug          # Windows 命令行用 gradlew.bat :app:assembleDebug
# 产物：app/build/outputs/apk/debug/app-debug.apk
```

首次在 Linux/macOS 上运行如果提示 permission denied，先 `chmod +x gradlew`。
用 Android Studio 直接打开 `android/` 目录也可以。

### 单元测试

```bash
cd android
./gradlew :app:testDebugUnitTest      # Windows：gradlew.bat :app:testDebugUnitTest
```

覆盖 614 字节载荷的构建/解析、参数编码（真机回读的样本值）、名称 ASCII 规则、大端 LUT、`.z30` 包的边界情况。
这些都在 JVM 上跑，不需要相机。

> **Windows 中文路径的已知限制**：如果仓库放在 `D:/GitHub逆向z30滤镜` 这类含中文的目录，Gradle 的测试工作进程会加载不到测试类，
> 报 `ClassNotFoundException: ...Test`（Java 在 Windows 上解类路径的编码问题，与本项目代码无关）。
> APK 编译不受影响；要本地跑测试，把仓库放到纯英文路径即可，或者直接看 GitHub Actions 里的测试结果。

## 使用步骤

1. 电脑端：拟合 → 导出 `.z30`
2. 传给手机（微信/网盘/数据线都行）
3. 相机开机，`USB 模式` 设为 **MTP/PTP**，用数据线接到手机（需要手机支持 USB OTG）
4. 应用会自动拉起；没拉起就点「连接相机」，在系统弹窗里允许访问
5. 「导入」选 `.z30`
6. 选目标槽位（**第一次建议 C9**）
7. 点「写入」→ 确认 → 看结果

写入结果的三种情况：

- **读回一致**：机身已经拿到这份曲线，切到对应 C 槽位即可拍摄
- **有 N 字节不一致**：相机没接受完整数据，界面会列出偏移；原内容可从备份恢复
- **报错**：看界面下方的运行日志，常见原因是相机休眠、USB 被系统占用、线材只供电不传数据

## 相机侧的三个坑

1. **名称只认 ASCII**，最长 19 字符。中文会被替换成 `?`，这是机身限制。
2. **曲线之后还有白平衡增益**（`out = WB_gain × f(in)`）。同一份配方在不同白平衡下效果不同，严肃复现要固定白平衡。
3. **对比度/亮度写不进去**。有自定义曲线时这两个参数被机身禁用，写了也不生效，所以界面里没有它们。

## 安全约定

- 只写 614 字节的完整配方，不碰固件、不刷机
- 写前备份原槽位到手机私有目录，写后立刻读回校验
- 不做「自动重试」「自动清理」这类用户看不见的动作
- `0x90CE` 删槽不实现：它会把配方直接删掉，桌面端已有前人踩坑记录

## 代码地图

| 文件 | 职责 |
|---|---|
| `core/Payload.kt` | 614 字节载荷的解析/构建、参数编码、逐字节比对 |
| `core/Recipe.kt` | `.z30` 配方包读写 |
| `core/Slots.kt` | 槽位常量与显示名 |
| `usb/UsbPtp.kt` | PTP over USB：握手、`0x90CC` 读、`0x90CD` 写 |
| `usb/CameraController.kt` | 备份 → 写 → 读回校验 |
| `usb/UsbHub.kt` | USB 权限与热插拔 |
| `data/Stores.kt` | 配方与备份的本地存储 |
| `ui/` | Compose 界面与状态机 |

## 许可

与仓库一致：MIT。PTP 私有指令与 614 字节载荷结构的来源与鸣谢见根目录 [`开源计划.md`](../开源计划.md)。
