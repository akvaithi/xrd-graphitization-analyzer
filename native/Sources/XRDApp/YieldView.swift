import SwiftUI
import XRDCore

/// Optional **Yield** tab. Enter a run's weighed masses (persisted in the scan's
/// sidecar) → carbon mass yield, and — using the crystallinity already computed on
/// the Analyze pane — the crystalline-graphite yield. Post-acid unlocks a wash QC.
/// Nothing here is required; fill it in only for runs you want a yield on.
struct YieldView: View {
    @EnvironmentObject var model: AppModel

    @State private var selID: LoadedFile.ID?
    @State private var yi = YieldInputs()
    @State private var loaded = false          // gate: don't persist a programmatic load

    private var selectedFile: LoadedFile? { model.files.first { $0.id == selID } }

    var body: some View {
        Group {
            if model.files.isEmpty {
                ContentUnavailableView("No runs loaded", systemImage: "scalemass",
                    description: Text("Open .xy scans in Analyze, then enter each run's masses here."))
            } else {
                HSplitView {
                    fileList.frame(minWidth: 230, idealWidth: 270, maxWidth: 330)
                    detail.frame(minWidth: 430)
                }
            }
        }
        .navigationTitle("Yield")
        .onAppear {
            if selID == nil { selID = model.selection ?? model.files.first?.id }
            loadInputs()
        }
        .onChange(of: selID) { loadInputs() }
        .onChange(of: yi) {
            guard loaded, let f = selectedFile else { return }
            model.saveYield(f, yi)             // persist only on a real edit
        }
    }

    // MARK: file list (left)

    private var fileList: some View {
        List(selection: $selID) {
            Section {
                ForEach(model.files) { f in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(f.displayName).font(.system(size: 11)).lineLimit(2)
                        Text(yieldTag(f)).font(.system(size: 10)).foregroundStyle(.secondary)
                    }
                    .tag(f.id)
                }
            } header: {
                let done = model.files.filter { model.yieldResult(for: $0) != nil }.count
                Text("\(done)/\(model.files.count) with yield")
            }
        }
    }

    private func yieldTag(_ f: LoadedFile) -> String {
        if let r = model.yieldResult(for: f) {
            let cg = r.crystallineGraphiteYield.map { String(format: " · cryst-g %.0f%%", $0 * 100) } ?? ""
            return String(format: "yield %.1f%%", r.massYield * 100) + cg
        }
        return model.yieldInputs[f.id] != nil ? "incomplete" : "no data"
    }

    // MARK: detail (right)

    private var detail: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let f = selectedFile {
                    Text(f.displayName).font(.headline)
                    if let r = model.yieldResult(for: f) { resultCard(r, f) }
                    else { hint }
                    inputForm
                    recipeInfo(f)
                    summaryTable
                }
            }
            .padding(16)
        }
    }

    private var hint: some View {
        Text("Enter the pellet and post-furnace masses below. The recipe (GPC / Fe / CaCO₃) and feed composition are read from the filename.")
            .font(.callout).foregroundStyle(.secondary)
            .padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }

    private func resultCard(_ r: YieldResult, _ f: LoadedFile) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text("Mass yield").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(String(format: "%.2f %%", r.massYield * 100))
                    .font(.system(size: 26, weight: .semibold, design: .rounded)).foregroundStyle(.tint)
            }
            if let cg = r.crystallineGraphiteYield {
                HStack {
                    Text("Crystalline-graphite yield  (mass × crystallinity \(pct(r.crystallineFraction))")
                        .font(.caption2).foregroundStyle(.secondary)
                    Spacer()
                    Text(String(format: "%.2f %%", cg * 100)).font(.headline).monospacedDigit()
                }
            } else {
                Text("crystalline-graphite yield needs the scan's crystallinity (Analyze this file first)")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            Divider().padding(.vertical, 2)
            row("Theoretical graphite", g(r.graphiteTheoretical))
            row("Carbon lost beyond Boudouard", g(r.carbonLostBeyondBoudouard))
            row("Boudouard C loss (CaCO₃-driven)", g(r.boudouardCLoss))
            if let u = r.unaccountedFurnaceLoss {
                row("Unaccounted furnace loss", g(u))
            }
            if let tm = r.trappedMetal, let we = r.washEfficiency {
                Divider().padding(.vertical, 2)
                row("Wash efficiency", String(format: "%.1f%%", we * 100))
                row("Trapped metal", g(tm) + ((r.overRemoved ?? false) ? "  ⚠︎ over-removed (fines lost?)" : ""))
            }
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(.tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
    }

    private var inputForm: some View {
        GroupBox {
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 8) {
                field("Pellet (g)", $yi.pellet, "REQUIRED — total charged mass (GPC + Fe + CaCO₃)")
                field("Post-furnace (g)", $yi.postFurnace, "REQUIRED — pre-wash mass")
                field("Post-acid (g)", $yi.postAcid, "optional — enables wash QC")
            }
            .padding(.vertical, 4)
        } label: { Label("Weighed masses", systemImage: "scalemass").font(.system(size: 12, weight: .semibold)) }
    }

    /// Read-only recipe/composition derived from the filename (scaled to the pellet).
    @ViewBuilder private func recipeInfo(_ f: LoadedFile) -> some View {
        if let m = model.derivedMasses(for: f) {
            let comp = YieldCalc.defaultComposition(grade: m.grade)
            VStack(alignment: .leading, spacing: 6) {
                Text("FROM FILENAME (scaled to pellet)").font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.secondary)
                row("Grade", m.grade ?? "—")
                row("GPC / Fe / CaCO₃", String(format: "%.4f / %.4f / %.4f g", m.gpc, m.fe, m.caco3))
                row("Assumed composition", String(format: "C %.1f%% · S %.1f%%", comp.cWt * 100, comp.sWt * 100))
                Text("Composition is a per-grade default — replace with proximate/ultimate analysis when available.")
                    .font(.caption2).foregroundStyle(.tertiary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        } else if (model.yieldInputs[f.id]?.pellet ?? 0) > 0 {
            Label("Couldn't read the GPC/Fe recipe from this filename — the yield needs those ratios. Rename the file to the standard pattern, or use the manifest with explicit masses.",
                  systemImage: "exclamationmark.triangle")
                .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
        }
    }

    private func field(_ label: String, _ value: Binding<Double>, _ help: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary).font(.system(size: 12))
            TextField("0", value: value, format: .number).textFieldStyle(.roundedBorder).frame(width: 90)
            Text(help).font(.caption2).foregroundStyle(.tertiary)
        }
    }

    private var summaryTable: some View {
        let rows = model.files.compactMap { f -> (LoadedFile, YieldResult)? in
            model.yieldResult(for: f).map { (f, $0) }
        }
        return Group {
            if rows.count > 1 {
                VStack(alignment: .leading, spacing: 6) {
                    Text("ALL RUNS WITH YIELD").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                    ForEach(rows, id: \.0.id) { f, r in
                        HStack {
                            Text(f.displayName).font(.caption).lineLimit(1)
                            Spacer()
                            Text(String(format: "%.1f%%", r.massYield * 100)).font(.caption).monospacedDigit()
                            if let cg = r.crystallineGraphiteYield {
                                Text(String(format: "· %.1f%% cryst-g", cg * 100))
                                    .font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                            }
                        }
                        Divider()
                    }
                    Text("Trends (yield vs CaCO₃ / Fe / temperature) plot via research/yield_calc.py --plots.")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack { Text(k).foregroundStyle(.secondary); Spacer(); Text(v).fontWeight(.medium).monospacedDigit() }
            .font(.system(size: 12))
    }
    private func g(_ v: Double) -> String { String(format: "%.4f g", v) }
    private func pct(_ v: Double?) -> String { v.map { String(format: "%.0f%%)", $0 * 100) } ?? "—)" }

    private func loadInputs() {
        loaded = false
        yi = selID.flatMap { model.yieldInputs[$0] } ?? YieldInputs()
        loaded = true
    }
}
