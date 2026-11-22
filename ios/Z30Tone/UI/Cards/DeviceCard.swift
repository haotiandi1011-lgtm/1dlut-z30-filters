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
                    row("身份", model.hostIDText)
                }
                Text("相机只记住第一个连上它的身份。若提示「相机拒绝了这台主机」，在相机上新建一个网络配置文件后再连。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(
                    "1. 相机：连接到 PC → 网络设定 → 创建配置文件 → 直接连接到计算机\n"
                        + "2. 手机「设置 → 无线局域网」连上相机的 Wi-Fi（形如 NIKON_Z_30_xxxxx）\n"
                        + "3. 相机别熄屏；电脑上的 Wireless Transmitter Utility 先退出\n"
                        + "4. 第一次点连接时，iOS 会问是否允许查找本地网络设备，必须点允许"
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
