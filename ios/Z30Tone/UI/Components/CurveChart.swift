import SwiftUI

/// 256 点曲线图。淡橙色是分位数匹配的目标，主色是量化后真正写进机身的那条。
/// 画对角线是为了让「压暗/提亮」一眼看得出来。
struct CurveChart: View {
    let curve: [Double]
    var target: [Double]?
    var chartHeight: CGFloat = 160

    var body: some View {
        Canvas { context, size in
            var grid = Path()
            for i in 0...4 {
                let x = size.width * CGFloat(i) / 4
                grid.move(to: CGPoint(x: x, y: 0))
                grid.addLine(to: CGPoint(x: x, y: size.height))
                let y = size.height * CGFloat(i) / 4
                grid.move(to: CGPoint(x: 0, y: y))
                grid.addLine(to: CGPoint(x: size.width, y: y))
            }
            context.stroke(grid, with: .color(Color.gray.opacity(0.18)), lineWidth: 0.5)

            var diagonal = Path()
            diagonal.move(to: CGPoint(x: 0, y: size.height))
            diagonal.addLine(to: CGPoint(x: size.width, y: 0))
            context.stroke(
                diagonal,
                with: .color(Color.gray.opacity(0.30)),
                style: StrokeStyle(lineWidth: 0.8, dash: [3, 3])
            )

            if let target, target.count > 1 {
                context.stroke(
                    path(for: target, in: size),
                    with: .color(Color.orange.opacity(0.60)),
                    style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round)
                )
            }
            if curve.count > 1 {
                context.stroke(
                    path(for: curve, in: size),
                    with: .color(Color.accentColor),
                    style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round)
                )
            }
        }
        .frame(height: chartHeight)
        .background(
            Color(uiColor: .tertiarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 10, style: .continuous)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(Color(uiColor: .separator).opacity(0.35), lineWidth: 0.5)
        )
        .accessibilityLabel("影调曲线，\(curve.count) 点")
    }

    private func path(for values: [Double], in size: CGSize) -> Path {
        var p = Path()
        let n = values.count
        guard n > 1 else { return p }
        for i in 0..<n {
            let x = size.width * CGFloat(i) / CGFloat(n - 1)
            let y = size.height * (1 - CGFloat(min(max(values[i], 0), 1)))
            if i == 0 {
                p.move(to: CGPoint(x: x, y: y))
            } else {
                p.addLine(to: CGPoint(x: x, y: y))
            }
        }
        return p
    }
}
