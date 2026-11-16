import SwiftUI
import Z30ToneCore

@main
struct Z30ToneApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

/// 骨架页。核心算法已经随包，界面按安卓端 `WriterScreen.kt` 的卡片顺序逐张补齐。
struct RootView: View {
    var body: some View {
        NavigationStack {
            List {
                Section {
                    LabeledContent("版本", value: Bundle.main.displayVersion)
                    LabeledContent("预设库", value: "121 套已随包")
                    LabeledContent("写机身", value: "见下方说明")
                } header: {
                    Text("骨架已就绪")
                } footer: {
                    Text("载荷结构、配方包、拟合算法与金标准测试已全部就位；下面会按安卓端的卡片顺序补齐界面。")
                }

                Section {
                    Text("iPhone / iPad 的公开 API 不允许第三方 App 直接访问相机的 USB/PTP 通道，所以「USB 直连写 C1–C9」在 iOS 上做不了。写出入口有两条：导出 .z30 交给安卓端或电脑写入；或通过相机 Wi-Fi（若机型与固件放行）。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } header: {
                    Text("能力边界")
                }
            }
            .navigationTitle("Z30 影调")
        }
    }
}

private extension Bundle {
    var displayVersion: String {
        let version = infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
        let build = infoDictionary?["CFBundleVersion"] as? String ?? "—"
        return "v\(version) (\(build))"
    }
}
