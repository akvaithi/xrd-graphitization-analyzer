import SwiftUI
import Charts
import XRDCore

/// Native Swift Charts rendering of the (002) fit: windowed raw points + the
/// deconvolved graphitic / turbostratic / total curves (all including y0).
struct FitChartView: View {
    let result: DGResult

    private struct P: Identifiable { let id = UUID(); let x: Double; let y: Double }

    private let cGraph = Color(red: 1.0, green: 0.23, blue: 0.19)   // systemRed
    private let cTurbo = Color(red: 0.19, green: 0.69, blue: 0.78)  // systemTeal
    private let cFit = Color(red: 0.37, green: 0.36, blue: 0.90)    // systemIndigo

    var body: some View {
        let raw = zip(result.pointsX, result.pointsY).map { P(x: $0, y: $1) }
        let (graph, turbo, total) = curves()

        Chart {
            ForEach(raw) { p in
                PointMark(x: .value("2θ", p.x), y: .value("Intensity", p.y))
                    .foregroundStyle(by: .value("Series", "Raw data"))
                    .symbolSize(14)
            }
            ForEach(graph) { p in
                LineMark(x: .value("2θ", p.x), y: .value("Intensity", p.y),
                         series: .value("Series", "Graphitic"))
                    .foregroundStyle(by: .value("Series", "Graphitic"))
                    .lineStyle(StrokeStyle(lineWidth: 2))
            }
            ForEach(turbo) { p in
                LineMark(x: .value("2θ", p.x), y: .value("Intensity", p.y),
                         series: .value("Series", "Turbostratic"))
                    .foregroundStyle(by: .value("Series", "Turbostratic"))
                    .lineStyle(StrokeStyle(lineWidth: 2))
            }
            ForEach(total) { p in
                LineMark(x: .value("2θ", p.x), y: .value("Intensity", p.y),
                         series: .value("Series", "Total fit"))
                    .foregroundStyle(by: .value("Series", "Total fit"))
                    .lineStyle(StrokeStyle(lineWidth: 2.5, dash: [6, 4]))
            }
        }
        .chartForegroundStyleScale([
            "Raw data": Color.secondary, "Graphitic": cGraph,
            "Turbostratic": cTurbo, "Total fit": cFit,
        ])
        .chartXAxisLabel("2θ  (degrees)")
        .chartYAxisLabel("Intensity  (a.u.)")
        .chartLegend(position: .top, alignment: .leading)
    }

    private func curves() -> ([P], [P], [P]) {
        guard let lo = result.pointsX.min(), let hi = result.pointsX.max(), hi > lo else {
            return ([], [], [])
        }
        let g = result.graphitic
        let t = result.turbostratic
        let n = 320
        var graph: [P] = [], turbo: [P] = [], total: [P] = []
        for i in 0...n {
            let x = lo + (hi - lo) * Double(i) / Double(n)
            let gy = result.y0 + pseudoVoigt(x, g.A, g.xc, g.w, g.mu)
            graph.append(P(x: x, y: gy))
            if let t {
                let ty = result.y0 + pseudoVoigt(x, t.A, t.xc, t.w, t.mu)
                turbo.append(P(x: x, y: ty))
                total.append(P(x: x, y: gy + ty - result.y0))   // y0 counted once
            } else {
                total.append(P(x: x, y: gy))
            }
        }
        return (graph, turbo, total)
    }
}
