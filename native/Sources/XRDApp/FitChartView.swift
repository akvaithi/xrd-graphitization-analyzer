import SwiftUI
import Charts
import XRDCore

/// A line that can appear in the on-chart parameters box.
enum AnnotationField: String, CaseIterable, Identifiable {
    case dg = "DG %"
    case graphitic = "Graphitic 2θ"
    case fwhm = "Graphitic FWHM"
    case lc = "Lc"
    case r2 = "Fit R²"
    case dprime = "Weighted d′"
    case turbo = "Turbostratic 2θ"
    var id: String { rawValue }
}

/// Per-export controls for what the fit chart draws and how big the text is.
struct ChartOptions: Equatable {
    var showComponents = true   // graphitic + turbostratic component curves
    var showAnnotation = true   // parameters box
    var fields: Set<AnnotationField> = [.dg, .graphitic, .fwhm, .lc, .r2]
    var textScale: Double = 1.0
}

/// Native Swift Charts rendering of the (002) fit: windowed raw points + the
/// deconvolved graphitic / turbostratic / total curves (all including y0).
struct FitChartView: View {
    let result: DGResult
    var options = ChartOptions()

    private struct P: Identifiable { let id = UUID(); let x: Double; let y: Double }

    private let cGraph = Color(red: 1.0, green: 0.23, blue: 0.19)   // systemRed
    private let cTurbo = Color(red: 0.19, green: 0.69, blue: 0.78)  // systemTeal
    private let cFit = Color(red: 0.37, green: 0.36, blue: 0.90)    // systemIndigo

    var body: some View {
        let raw = zip(result.pointsX, result.pointsY).map { P(x: $0, y: $1) }
        let (graph, turbo, total) = curves()

        // Explicit domains so the axes match the data (Charts auto-scaling can drift).
        let xs = result.pointsX
        let xlo = xs.min() ?? 24, xhi = xs.max() ?? 28.5
        let allY = result.pointsY + total.map(\.y)
        let ymax = allY.max() ?? 1
        let ymin = Swift.min(result.y0, allY.min() ?? 0)
        let pad = Swift.max((ymax - ymin) * 0.06, 1e-6)

        return Chart {
            ForEach(raw) { p in
                PointMark(x: .value("2θ", p.x), y: .value("Intensity", p.y))
                    .foregroundStyle(by: .value("Series", "Raw data"))
                    .symbolSize(14)
            }
            if options.showComponents {
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
        .chartXScale(domain: xlo...xhi)
        .chartYScale(domain: (ymin - pad)...(ymax + pad))
        .chartXAxisLabel("2θ  (degrees)")
        .chartYAxisLabel("Intensity  (a.u.)")
        .chartLegend(position: .top, alignment: .leading)
        .font(.system(size: 11 * options.textScale))   // scales axis tick labels
        .overlay(alignment: .topTrailing) {
            if options.showAnnotation, !lines.isEmpty { annotationBox.padding(8) }
        }
    }

    /// The text for each enabled annotation field, in display order.
    private var lines: [(field: AnnotationField, text: String)] {
        let g = result.graphitic
        return AnnotationField.allCases.compactMap { f in
            guard options.fields.contains(f) else { return nil }
            let s: String?
            switch f {
            case .dg:        s = String(format: "DG  %.2f%%", result.dgPercent)
            case .graphitic: s = String(format: "graphitic 2θ  %.3f°", g.xc)
            case .fwhm:      s = String(format: "FWHM  %.3f°", g.w)
            case .lc:        s = String(format: "Lc  %.0f Å", result.crystalliteLc)
            case .r2:        s = String(format: "R²  %.4f", result.fitR2)
            case .dprime:    s = String(format: "d′  %.5f nm", result.dPrimeWeighted)
            case .turbo:     s = result.turbostratic.map { String(format: "turbo 2θ  %.3f°", $0.xc) }
            }
            return s.map { (f, $0) }
        }
    }

    /// Compact parameters box drawn on the chart for exports/readers.
    private var annotationBox: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(lines, id: \.field) { item in
                Text(item.text).fontWeight(item.field == .dg ? .semibold : .regular)
            }
        }
        .font(.system(size: 10 * options.textScale, design: .monospaced))
        .padding(6 * options.textScale)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
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
