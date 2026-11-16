import SwiftUI

/// 卡片容器（不要叫 `Section`：会和 SwiftUI 自带的 `Section` 撞名）。对齐安卓端 `Section`：圆角 18、1px 描边、内边距 16、元素间距 12，
/// 标题左侧、副标题小字灰色、右侧可放操作按钮。
struct CardSection<Content: View, Trailing: View>: View {
    let title: String
    let subtitle: String?
    @ViewBuilder let trailing: () -> Trailing
    @ViewBuilder let content: () -> Content

    init(
        title: String,
        subtitle: String? = nil,
        @ViewBuilder trailing: @escaping () -> Trailing,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.title = title
        self.subtitle = subtitle
        self.trailing = trailing
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.headline)
                    if let subtitle, !subtitle.isEmpty {
                        Text(subtitle)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
                trailing()
            }
            content()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Color(uiColor: .secondarySystemGroupedBackground))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(Color(uiColor: .separator).opacity(0.45), lineWidth: 1)
        )
    }
}

extension CardSection where Trailing == EmptyView {
    init(
        title: String,
        subtitle: String? = nil,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.init(title: title, subtitle: subtitle, trailing: { EmptyView() }, content: content)
    }
}

/// 小标签，用来显示「贴黑 2.1%」这类指标。
struct MetricPill: View {
    let label: String
    let value: String
    var tint: Color = .secondary

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.footnote.monospacedDigit())
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// 写前建议框：颜色随等级变化，文案原样来自写前判据。
struct AdviceBox: View {
    let level: String
    let text: String

    private var color: Color {
        switch level {
        case "ok": return .green
        case "caution": return .orange
        default: return .red
        }
    }

    private var icon: String {
        switch level {
        case "ok": return "checkmark.circle.fill"
        case "caution": return "exclamationmark.triangle.fill"
        default: return "xmark.octagon.fill"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon)
                .foregroundStyle(color)
            Text(text)
                .font(.footnote)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.10), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}
