import SwiftUI

/// 设备卡：对标安卓端的「设备」。安卓走 USB，iOS 走相机 Wi-Fi，
/// 所以这里是一个地址输入框 + 连接按钮，而不是设备列表。
struct DeviceCard: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        CardSection(
            title: "相机连接",
            subtitle: "手机先加入相机 Wi-Fi，再点连接"
        ) {
            HStack(spacing: 10) {
                TextField(PtpIpClient.defaultHost, text: $model.host)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.decimalPad)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .disabled(model.connected)

                if model.connected {
                    Button("断开") { model.disconnectCamera() }
                        .buttonStyle(.bordered)
                } else {
                    Button("连接") { model.connectCamera() }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.busy)
                }
            }

            if model.connected {
                VStack(alignment: .leading, spacing: 5) {
                    row("相机", model.cameraName)
                    row("型号", "\(model.cameraModel) · 固件 \(model.cameraFirmware)")
                    row("写机身", model.supportsWrite ? "相机声明支持 0x90CD" : "相机未声明支持写入")
                }
            } else {
                Text(
                    "1. 手机「设置 → 无线局域网」连上相机的 Wi-Fi（形如 NIKON_Z_30_xxxxx）\n"
                        + "2. 相机菜单：连接到 PC → 连接到计算机，并调长熄屏时间\n"
                        + "3. 电脑上的 Wireless Transmitter Utility 先退出，它会占住会话"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 44, alignment: .leading)
            Text(value)
                .font(.caption.monospacedDigit())
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}
