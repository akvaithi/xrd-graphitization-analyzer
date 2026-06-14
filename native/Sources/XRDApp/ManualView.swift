import SwiftUI
import XRDCore

/// Manual calc tab — DG from hand-entered Origin peaks (NETL excel sheet).
struct ManualView: View {
    @State private var two = false
    @State private var xc1 = ""
    @State private var a1 = ""
    @State private var xc2 = ""
    @State private var a2 = ""
    @State private var result: ManualResult?
    @State private var error: String?

    var body: some View {
        HSplitView {
            inputs.frame(minWidth: 300, idealWidth: 340, maxWidth: 420)
            output.frame(minWidth: 360)
        }
        .navigationTitle("Manual DG calculator")
    }

    private var inputs: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Enter Origin fit peaks").font(.headline)
                Toggle("Two peaks (graphitic + turbostratic)", isOn: $two)
                peakRow("Graphitic", $xc1, $a1)
                if two { peakRow("Turbostratic", $xc2, $a2) }
                Text("λ fixed at Cu Kα 1.54187 Å. Graphitic = higher 2θ peak. One peak → DG from its d-spacing; two → area-weighted (Maire–Mering).")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Button("Calculate DG%") { calculate() }.keyboardShortcut(.return)
                if let error { Text(error).font(.caption).foregroundStyle(.red) }
            }
            .padding(16)
        }
    }

    private func peakRow(_ label: String, _ xc: Binding<String>, _ area: Binding<String>) -> some View {
        HStack(spacing: 10) {
            Text(label).foregroundStyle(.secondary).frame(width: 88, alignment: .leading).font(.caption)
            TextField("xc (2θ °)", text: xc).textFieldStyle(.roundedBorder).frame(width: 110)
            TextField("area (A)", text: area).textFieldStyle(.roundedBorder).frame(width: 110)
        }
    }

    @ViewBuilder private var output: some View {
        ScrollView {
            if let r = result {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(spacing: 4) {
                        Text("Degree of Graphitization").font(.caption).foregroundStyle(.secondary)
                        Text(String(format: "%.2f %%", r.dgPercent))
                            .font(.system(size: 40, weight: .semibold, design: .rounded)).foregroundStyle(.tint)
                        Text(r.nPeaks == 1 ? "single peak · Maire–Mering" : "area-weighted · Maire–Mering")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 16)
                    .background(.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))

                    rows("Graphitic", [("2θ", deg(r.graphiticXc)), ("area", num(r.graphiticArea, 4)),
                                       ("d-spacing", ang(r.graphiticD))])
                    if let txc = r.turbostraticXc, let ta = r.turbostraticArea, let td = r.turbostraticD {
                        rows("Turbostratic", [("2θ", deg(txc)), ("area", num(ta, 4)), ("d-spacing", ang(td))])
                        rows("Weighted", [
                            ("Xg / Xt", String(format: "%.1f%% / %.1f%%",
                                               (r.areaFractionGraphitic ?? 0) * 100, (r.areaFractionTurbostratic ?? 0) * 100)),
                            ("d′", ang(r.dPrime))])
                    }
                }
                .padding(16)
            } else {
                ContentUnavailableView("Enter peak values", systemImage: "function",
                    description: Text("Type 1 or 2 peaks and calculate."))
            }
        }
    }

    private func rows(_ title: String, _ items: [(String, String)]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased()).font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
            ForEach(items, id: \.0) { item in
                HStack { Text(item.0).foregroundStyle(.secondary); Spacer()
                    Text(item.1).fontWeight(.medium).monospacedDigit() }
                    .font(.system(size: 13)).padding(.vertical, 4)
                Divider()
            }
        }
    }

    private func calculate() {
        error = nil
        var peaks: [ManualPeak] = []
        guard let x1 = Double(xc1), let ar1 = Double(a1) else { error = "Enter numeric xc and area."; return }
        peaks.append(ManualPeak(xc: x1, area: ar1))
        if two {
            guard let x2 = Double(xc2), let ar2 = Double(a2) else { error = "Enter numeric xc and area for both peaks."; return }
            peaks.append(ManualPeak(xc: x2, area: ar2))
        }
        do { result = try dgFromPeaks(peaks) }
        catch { result = nil; self.error = String(describing: error) }
    }

    private func deg(_ v: Double) -> String { String(format: "%.4f°", v) }
    private func ang(_ v: Double) -> String { String(format: "%.6f Å", v) }
    private func num(_ v: Double, _ p: Int) -> String { String(format: "%.\(p)f", v) }
}
