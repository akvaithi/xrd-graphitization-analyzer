import SwiftUI
import Charts
import AppKit
import XRDCore

/// Compare tab — comparison chart of metrics vs synthesis parameters, a run
/// table with include toggles, and CSV export.
struct CompareView: View {
    @EnvironmentObject var model: AppModel

    enum XMetric: String, CaseIterable, Identifiable {
        case temperature = "Temperature (°C)", caco3 = "CaCO₃", time = "Dwell (h)", fe = "Fe", carbon = "Carbon"
        var id: String { rawValue }
    }
    enum YMetric: String, CaseIterable, Identifiable {
        case dg = "DG %", lc = "Lc (Å)", dprime = "d′ (Å)", gxc = "Graphitic 2θ (°)"
        var id: String { rawValue }
    }
    enum Grouping: String, CaseIterable, Identifiable {
        case type = "Carbon type", form = "Form", wash = "Wash", none = "(none)"
        var id: String { rawValue }
    }

    @State private var xm: XMetric = .temperature
    @State private var ym: YMetric = .dg
    @State private var grp: Grouping = .type
    @State private var excluded: Set<UUID> = []

    private struct P: Identifiable { let id = UUID(); let x: Double; let y: Double; let group: String }

    private var valid: [LoadedFile] { model.files.filter { $0.autoResult != nil && $0.info != nil } }

    var body: some View {
        if valid.isEmpty {
            ContentUnavailableView("No runs to compare", systemImage: "chart.dots.scatter",
                description: Text("Open .xy files; each is auto-fit, then compare them here."))
        } else {
            VStack(spacing: 0) {
                HStack {
                    Picker("Y", selection: $ym) { ForEach(YMetric.allCases) { Text($0.rawValue).tag($0) } }
                    Picker("X", selection: $xm) { ForEach(XMetric.allCases) { Text($0.rawValue).tag($0) } }
                    Picker("Color", selection: $grp) { ForEach(Grouping.allCases) { Text($0.rawValue).tag($0) } }
                    Spacer()
                    Button { saveChartPNG(chart, suggestedName: "xrd_\(ym.rawValue)_vs_\(xm.rawValue)") }
                        label: { Label("Chart", systemImage: "photo") }
                    Button { exportCSV() } label: { Label("CSV", systemImage: "square.and.arrow.down") }
                }
                .padding(10)
                Divider()
                HSplitView {
                    chart.frame(minWidth: 440).padding(12)
                    runList.frame(minWidth: 240, idealWidth: 300, maxWidth: 380)
                }
            }
            .navigationTitle("Compare runs")
        }
    }

    private var chart: some View {
        let pts = points()
        let xs = pts.map(\.x), ys = pts.map(\.y)
        let xlo = xs.min() ?? 0, xhi = xs.max() ?? 1
        let ylo = ys.min() ?? 0, yhi = ys.max() ?? 1
        let xpad = Swift.max((xhi - xlo) * 0.08, 0.5)
        let ypad = Swift.max((yhi - ylo) * 0.08, 0.5)
        return Chart {
            ForEach(trend(pts)) { p in
                LineMark(x: .value("x", p.x), y: .value("y", p.y), series: .value("g", p.group))
                    .foregroundStyle(by: .value("Group", p.group)).opacity(0.35)
            }
            ForEach(pts) { p in
                PointMark(x: .value(xm.rawValue, p.x), y: .value(ym.rawValue, p.y))
                    .foregroundStyle(by: .value("Group", p.group)).symbolSize(70)
            }
        }
        .chartXScale(domain: (xlo - xpad)...(xhi + xpad))
        .chartYScale(domain: (ylo - ypad)...(yhi + ypad))
        .chartXAxisLabel(xm.rawValue)
        .chartYAxisLabel(ym.rawValue)
        .chartLegend(grp == .none ? .hidden : .visible)
        .overlay {
            if pts.isEmpty {
                Text("No points for this combination").foregroundStyle(.secondary)
            }
        }
    }

    private var runList: some View {
        List {
            Section {
                ForEach(valid) { f in
                    Toggle(isOn: Binding(
                        get: { !excluded.contains(f.id) },
                        set: { on in if on { excluded.remove(f.id) } else { excluded.insert(f.id) } })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(f.displayName).font(.system(size: 11)).lineLimit(2)
                            Text(f.dgText).font(.system(size: 10)).foregroundStyle(.secondary)
                        }
                    }
                }
            } header: {
                HStack {
                    Text("\(valid.count - excluded.count)/\(valid.count) shown")
                    Spacer()
                    Button("all") { excluded.removeAll() }.buttonStyle(.link).font(.caption)
                    Button("none") { excluded = Set(valid.map(\.id)) }.buttonStyle(.link).font(.caption)
                }
            }
        }
    }

    // MARK: data

    private func points() -> [P] {
        valid.compactMap { f in
            guard !excluded.contains(f.id), let xx = xval(f), let yy = yval(f) else { return nil }
            return P(x: xx, y: yy, group: gval(f))
        }
    }
    private func trend(_ pts: [P]) -> [P] {
        var out: [P] = []
        for (g, gp) in Dictionary(grouping: pts, by: { $0.group }) {
            let byX = Dictionary(grouping: gp, by: { $0.x })
            for (xx, ps) in byX.sorted(by: { $0.key < $1.key }) {
                out.append(P(x: xx, y: ps.map(\.y).reduce(0, +) / Double(ps.count), group: g))
            }
        }
        return out
    }
    private func xval(_ f: LoadedFile) -> Double? {
        guard let i = f.info else { return nil }
        switch xm {
        case .temperature: return i.temperatureC.map(Double.init)
        case .caco3: return i.caco3Ratio
        case .time: return i.timeH
        case .fe: return i.feRatio
        case .carbon: return i.carbonRatio
        }
    }
    private func yval(_ f: LoadedFile) -> Double? {
        guard let r = f.autoResult else { return nil }
        switch ym {
        case .dg: return r.dgPercent
        case .lc: return r.crystalliteLc
        case .dprime: return r.dPrimeWeighted
        case .gxc: return r.graphitic.xc
        }
    }
    private func gval(_ f: LoadedFile) -> String {
        guard grp != .none, let i = f.info else { return "all" }
        switch grp {
        case .type: return i.carbonType ?? "—"
        case .form: return i.form?.capitalized ?? "—"
        case .wash: return i.wash?.capitalized ?? "—"
        case .none: return "all"
        }
    }

    private func exportCSV() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "xrd_runs.csv"
        panel.allowedContentTypes = [.commaSeparatedText]
        guard panel.runModal() == NSApplication.ModalResponse.OK, let url = panel.url else { return }
        func n(_ v: Double?) -> String { v.map { String(format: "%g", $0) } ?? "" }
        func q(_ s: String) -> String { s.contains(",") ? "\"\(s)\"" : s }
        var out = "file,carbon_type,carbon_ratio,fe_ratio,caco3_ratio,temperature_C,time_h,form,wash,DG,Lc,d_prime,graphitic_xc\n"
        for f in valid {
            let i = f.info, r = f.autoResult
            out += [q(f.url.lastPathComponent), i?.carbonType ?? "", n(i?.carbonRatio), n(i?.feRatio),
                    n(i?.caco3Ratio), i?.temperatureC.map(String.init) ?? "", n(i?.timeH),
                    i?.form ?? "", i?.wash ?? "",
                    r.map { String(format: "%.2f", $0.dgPercent) } ?? "",
                    r.map { String(format: "%.1f", $0.crystalliteLc) } ?? "",
                    r.map { String(format: "%.5f", $0.dPrimeWeighted) } ?? "",
                    r.map { String(format: "%.4f", $0.graphitic.xc) } ?? ""].joined(separator: ",") + "\n"
        }
        try? out.write(to: url, atomically: true, encoding: .utf8)
    }
}
