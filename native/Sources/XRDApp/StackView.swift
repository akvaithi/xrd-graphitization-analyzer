import SwiftUI
import Charts
import XRDCore

/// Stack-spectra tab — overlay/waterfall of raw intensities to compare peak
/// heights. Offset 0 = flat overlay; drag up for a waterfall.
struct StackView: View {
    @EnvironmentObject var model: AppModel
    @State private var offset = 0.0
    @State private var zoom = false
    @State private var baseline = false
    @State private var selected: Set<UUID> = []
    @State private var didInit = false

    private struct LP: Identifiable { let id = UUID(); let x: Double; let y: Double; let name: String }
    private var withPattern: [LoadedFile] { model.files.filter { $0.pattern != nil } }

    var body: some View {
        if withPattern.isEmpty {
            ContentUnavailableView("No spectra", systemImage: "square.stack.3d.up",
                description: Text("Open .xy files to overlay them here."))
        } else {
            VStack(spacing: 0) {
                HStack(spacing: 16) {
                    HStack { Text("Offset"); Slider(value: $offset, in: 0...1).frame(width: 160)
                        Text(String(format: "%.2f", offset)).monospacedDigit().foregroundStyle(.secondary) }
                    Toggle("Zoom (002) 24–30°", isOn: $zoom)
                    Toggle("Baseline subtract", isOn: $baseline)
                    Spacer()
                }
                .padding(10)
                Divider()
                HSplitView {
                    chart.frame(minWidth: 440).padding(12)
                    fileList.frame(minWidth: 240, idealWidth: 300, maxWidth: 380)
                }
            }
            .navigationTitle("Stacked spectra")
            .task { if !didInit { selected = Set(withPattern.prefix(8).map(\.id)); didInit = true } }
        }
    }

    private var chart: some View {
        let s = series()
        let many = selected.count > 10
        return Chart(s) { p in
            LineMark(x: .value("2θ", p.x), y: .value("Intensity", p.y), series: .value("File", p.name))
                .foregroundStyle(by: .value("File", p.name))
                .lineStyle(StrokeStyle(lineWidth: 1.2))
        }
        .chartXAxisLabel("2θ  (degrees)")
        .chartYAxisLabel("Intensity  (a.u.)" + (offset > 0 ? "  — offset" : ""))
        .chartLegend(many ? .hidden : .visible)
        .overlay { if s.isEmpty { Text("Check files to stack").foregroundStyle(.secondary) } }
    }

    private var fileList: some View {
        List {
            Section {
                ForEach(withPattern) { f in
                    Toggle(isOn: Binding(
                        get: { selected.contains(f.id) },
                        set: { on in if on { selected.insert(f.id) } else { selected.remove(f.id) } })) {
                        Text(f.displayName).font(.system(size: 11)).lineLimit(2)
                    }
                }
            } header: {
                HStack {
                    Text("\(selected.count) selected"); Spacer()
                    Button("all") { selected = Set(withPattern.map(\.id)) }.buttonStyle(.link).font(.caption)
                    Button("none") { selected.removeAll() }.buttonStyle(.link).font(.caption)
                }
            }
        }
    }

    private func series() -> [LP] {
        let sel = withPattern.filter { selected.contains($0.id) }
        let lo = zoom ? 24.0 : 20.0, hi = zoom ? 30.0 : 60.0
        var prepared: [(name: String, x: [Double], y: [Double])] = []
        var gmax = 0.0
        for f in sel {
            guard let p = f.pattern else { continue }
            var (x, y) = p.window(lo, hi)
            if x.isEmpty { continue }
            if baseline && x.count > 1 {
                let b0 = y.first!, b1 = y.last!
                for j in y.indices {
                    let t = (x[j] - x.first!) / max(x.last! - x.first!, 1e-9)
                    y[j] = max(y[j] - (b0 + (b1 - b0) * t), 0)
                }
            }
            // downsample to ~400 points to keep the chart responsive
            let stride = max(1, x.count / 400)
            if stride > 1 {
                x = Swift.stride(from: 0, to: x.count, by: stride).map { x[$0] }
                let yy = y; y = Swift.stride(from: 0, to: yy.count, by: stride).map { yy[$0] }
            }
            gmax = max(gmax, y.max() ?? 0)
            prepared.append((f.displayName, x, y))
        }
        let step = offset * gmax
        var out: [LP] = []
        for (i, s) in prepared.enumerated() {
            for j in s.x.indices { out.append(LP(x: s.x[j], y: s.y[j] + Double(i) * step, name: s.name)) }
        }
        return out
    }
}
