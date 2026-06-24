import SwiftUI
import XRDCore

struct DetailView: View {
    let file: LoadedFile
    @EnvironmentObject private var server: OllamaServer
    @EnvironmentObject private var model: AppModel

    // Persisted per-file deconvolution choices (lives in AppModel; survives nav).
    @State private var local = DeconvSettings()
    // True once `local` has been loaded for the current file — so programmatic
    // loads (view recreation, file switch) never get persisted as user edits and
    // clobber the saved sidecar with a transient default.
    @State private var settingsLoaded = false

    // Transient — recomputed by refit() from `local`.
    @State private var calResult: InternalStandard?
    @State private var result: DGResult?
    @State private var fitError: String?
    @State private var quality: ImpurityScan?
    @State private var dgSpan: DGRange?

    // AI assist — engine selection now lives in Settings (⌘,).
    @State private var aiBusy = false
    @State private var showExportSheet = false
    /// Resolved engine + host (reads the same prefs the Settings window writes).
    private var ai: AIConfig { .current(server: server) }

    var body: some View {
        Group {
            if file.pattern == nil {
                ContentUnavailableView("Couldn't read file", systemImage: "exclamationmark.triangle",
                    description: Text(file.parseError ?? "Unknown error"))
            } else {
                HSplitView {
                    controlsAndResults
                        .frame(minWidth: 300, idealWidth: 330, maxWidth: 380)
                    Group {
                        if let r = result {
                            FitChartView(result: r).padding(16)
                        } else {
                            ContentUnavailableView("Fit failed", systemImage: "chart.xyaxis.line",
                                description: Text(fitError ?? "Adjust the deconvolution settings."))
                        }
                    }
                    .frame(minWidth: 380, maxWidth: .infinity)
                }
            }
        }
        .navigationTitle(file.displayName)
        .navigationSubtitle(file.url.lastPathComponent)
        // Load once per file. Runs on appear and whenever the selected file
        // changes; cancels/re-runs on `file.id`. The `settingsLoaded` gate stops
        // the assignment below from being treated as a user edit.
        .task(id: file.id) {
            settingsLoaded = false
            quality = file.pattern.map { ImpurityScan.scan($0) }
            local = model.settings[file.id] ?? model.defaults(for: file)
            settingsLoaded = true
            refit()
        }
        .onChange(of: local) {
            guard settingsLoaded else { return }   // ignore programmatic loads
            model.settings[file.id] = local
            refit()
            persist()                              // save sidecar only on real edits
        }
    }

    // MARK: controls + readout

    private var controlsAndResults: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let r = result { dgCallout(r) }
                if let q = quality, !q.hits.isEmpty { qualityCard(q) }

                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Deconvolution").font(.system(size: 13, weight: .semibold))
                            Spacer()
                            Button { local = model.defaults(for: file) } label: {
                                Label("Reset", systemImage: "arrow.uturn.backward")
                            }
                            .controlSize(.small)
                            .disabled(local == model.defaults(for: file))
                            .help("Restore the import defaults")
                        }
                        Picker("Peaks", selection: $local.peakCount) {
                            Text("1 peak").tag(1)
                            Text("2 peaks").tag(2)
                        }
                        .pickerStyle(.segmented)

                        if local.peakCount == 2 {
                            Toggle("Lock turbostratic 2θ", isOn: $local.turboLocked)
                            if local.turboLocked {
                                HStack(spacing: 8) {
                                    Text("Turbostratic 2θ").foregroundStyle(.secondary).font(.caption)
                                    TextField("26.20", value: $local.turboCenter, format: .number)
                                        .textFieldStyle(.roundedBorder).frame(width: 70)
                                    Text("°").foregroundStyle(.secondary)
                                    Spacer()
                                }
                            }
                        }

                        Toggle("Subtract sloped background (24–26.5°)", isOn: $local.subtractBg)

                        Divider()
                        HStack(spacing: 8) {
                            Text("Internal-std calib").foregroundStyle(.secondary).font(.caption)
                            Picker("", selection: $local.calStdPhase) {
                                Text("off").tag(""); Text("auto").tag("auto")
                                Text("Fe₃C").tag("Fe3C"); Text("α-Fe").tag("alpha-Fe"); Text("CaO").tag("CaO")
                            }.labelsHidden().frame(width: 90)
                            Spacer()
                            if !local.calStdPhase.isEmpty, let c = calResult, let ph = c.phase {
                                Text(c.significant
                                     ? String(format: "%@ %+.3f° ✓", ph, c.offset)
                                     : String(format: "%@ %+.3f° (noise)", ph, c.offset))
                                    .font(.caption2).monospacedDigit()
                                    .foregroundStyle(c.significant ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                            }
                        }
                        Toggle("Anchor (002) to a known angle", isOn: $local.anchorOn)
                        if local.anchorOn {
                            HStack(spacing: 8) {
                                Text("Anchor (002) to").foregroundStyle(.secondary).font(.caption)
                                TextField("26.54", value: $local.anchorTarget, format: .number)
                                    .textFieldStyle(.roundedBorder).frame(width: 70)
                                Text("°").foregroundStyle(.secondary)
                                Spacer()
                                if let r = result, r.twoThetaOffset != 0 {
                                    Text(String(format: "Δ2θ %+.3f°", r.twoThetaOffset))
                                        .font(.caption).monospacedDigit().foregroundStyle(.tint)
                                }
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }

                aiAssist
                exportBox

                if let r = result { resultRows(r) }
            }
            .padding(16)
        }
        .sheet(isPresented: $showExportSheet) {
            if let r = result {
                ExportPreviewView(result: r, displayName: file.displayName)
            }
        }
    }

    private func dgCallout(_ r: DGResult) -> some View {
        VStack(spacing: 4) {
            Text("Degree of Graphitization").font(.caption).foregroundStyle(.secondary)
            Text(String(format: "%.2f %%", r.dgPercent))
                .font(.system(size: 38, weight: .semibold, design: .rounded))
                .foregroundStyle(.tint)
            if let sg = r.dgSigma {
                Text(String(format: "± %.2f%%", sg))
                    .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
            }
            Text("\(r.peakCount == 1 ? "single peak" : "area-weighted") · R² \(String(format: "%.4f", r.fitR2))")
                .font(.caption2).foregroundStyle(.secondary)
            if let s = dgSpan, s.high - s.low > 0.5 {
                Text(String(format: "range %.1f–%.1f%% across deconvolution choices", s.low, s.high))
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity).padding(.vertical, 16)
        .background(.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }

    private func qualityCard(_ q: ImpurityScan) -> some View {
        let warn = !q.clean
        return VStack(alignment: .leading, spacing: 6) {
            Label(q.verdict, systemImage: warn ? "exclamationmark.triangle.fill" : "checkmark.seal.fill")
                .font(.caption).fontWeight(.semibold)
                .foregroundStyle(warn ? .orange : .green)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(q.hits) { h in
                HStack(spacing: 6) {
                    Text(String(format: "%.2f°", h.twoTheta)).monospacedDigit().foregroundStyle(.secondary)
                    Text(h.phase)
                    Spacer()
                    Text(String(format: "%.1f%%", h.relPct)).monospacedDigit()
                    Text(h.level).font(.caption2).foregroundStyle(.tertiary)
                }
                .font(.caption)
            }
            Text("Impurities lie outside the (002) window — DG is unaffected; this flags wash completeness.")
                .font(.caption2).foregroundStyle(.tertiary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background((warn ? Color.orange : Color.green).opacity(0.12),
                    in: RoundedRectangle(cornerRadius: 10))
    }

    private func resultRows(_ r: DGResult) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            section("Graphitic peak", [
                ("2θ centre", deg(r.graphitic.xc)), ("FWHM", deg(r.graphitic.w)),
                ("μ", num(r.graphitic.mu, 4)), ("Area", num(r.graphitic.A, 2)),
                ("d-spacing", ang(r.graphitic.dSpacing))])
            if let t = r.turbostratic {
                section("Turbostratic peak (Lorentzian)", [
                    ("2θ centre", deg(t.xc)), ("FWHM", deg(t.w)),
                    ("Area", num(t.A, 2)), ("d-spacing", ang(t.dSpacing))])
            }
            section("Result", [
                ("Xg / Xt", String(format: "%.1f%% / %.1f%%",
                                    r.areaFractionGraphitic * 100, r.areaFractionTurbostratic * 100)),
                ("d′ weighted", ang(r.dPrimeWeighted)),
                ("Crystallite Lc", String(format: "%.1f Å (apparent)", r.crystalliteLc)),
                ("Baseline y0", num(r.y0, 3))]
                + (r.twoThetaOffset != 0
                   ? [("2θ displacement corr.", String(format: "%+.3f°", r.twoThetaOffset))] : [])
                + [("Wavelength λ", String(format: "%.5f Å", r.wavelength))])
        }
    }

    private func section(_ title: String, _ items: [(String, String)]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased()).font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
            ForEach(items, id: \.0) { item in
                HStack {
                    Text(item.0).foregroundStyle(.secondary)
                    Spacer()
                    Text(item.1).fontWeight(.medium).monospacedDigit()
                }
                .font(.system(size: 13)).padding(.vertical, 4)
                Divider()
            }
        }
    }

    // MARK: fit

    private func refit() {
        guard let p = file.pattern else { result = nil; model.results[file.id] = nil; return }
        let out = FitRunner.run(p, local)
        calResult = out.calibration
        result = out.result
        fitError = out.error
        model.results[file.id] = out.result          // publish → sidebar / Compare update
        if out.result != nil {
            var span = out.options; span.peakCount = 2   // range shares calibration/background
            dgSpan = dgRange(p, base: span)
        } else {
            dgSpan = nil
        }
    }

    /// Persist the sidecar — called only from a genuine user edit (never on load),
    /// so a transient default during view recreation can't clobber saved settings.
    private func persist() {
        AnalysisStore.update(for: file.url, settings: local, result: result)
    }

    // MARK: AI assist (optional)

    @ViewBuilder private var aiAssist: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label("AI assist", systemImage: "sparkles").font(.system(size: 12, weight: .semibold))
                    Spacer()
                    if let c = local.aiConfidence {
                        Text(String(format: "conf %.0f%%", c * 100))
                            .font(.caption2).padding(.horizontal, 7).padding(.vertical, 2)
                            .background((c < 0.8 ? Color.orange : Color.green).opacity(0.22), in: Capsule())
                    }
                }
                HStack {
                    Button { runAI() } label: { Label("Suggest deconvolution", systemImage: "wand.and.stars") }
                        .disabled(aiBusy || file.pattern == nil || !ai.canSuggest)
                    if aiBusy { ProgressView().controlSize(.small) }
                    Spacer()
                    SettingsLink { Text("Settings…").font(.caption) }
                }
                if let note = local.aiNote {
                    Text(note).font(.caption)
                        .foregroundStyle((local.aiConfidence ?? 1) < 0.8 ? .orange : .secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.vertical, 4)
        }
    }

    @ViewBuilder private var exportBox: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                Label("Export", systemImage: "square.and.arrow.up").font(.system(size: 12, weight: .semibold))
                HStack {
                    Button { showExportSheet = true } label: { Label("Chart…", systemImage: "photo") }
                        .disabled(result == nil)
                    Button { saveReport() } label: { Label("Report (CSV)", systemImage: "tablecells") }
                        .disabled(result == nil)
                    Button { exportShiftedXY() } label: { Label("Shifted .xy", systemImage: "arrow.left.and.right") }
                        .disabled((result?.twoThetaOffset ?? 0) == 0)
                        .help("Export a copy with the 2θ shift applied — the original file is left untouched")
                }
            }
            .padding(.vertical, 4)
        }
    }

    private func runAI() {
        guard let p = file.pattern else { return }
        aiBusy = true
        let cfg = ai
        Task {
            do {
                let out = try await AISuggestionService.suggest(
                    pattern: p, active: cfg.active, host: cfg.host, base: local)
                await MainActor.run {
                    local = out.settings                       // → onChange persists + refits
                    aiBusy = false
                    if !out.suggestion.amorphousInvalid, let r = result {
                        DecisionLog.append(DecisionLogEntry(
                            file: file.url.lastPathComponent, displayName: file.displayName,
                            features: out.features, suggestion: out.suggestion,
                            finalPeakCount: local.peakCount,
                            finalTurbostratic2theta: local.turboLocked ? local.turboCenter : nil,
                            finalSubtractBackground: local.subtractBg, dgPercent: r.dgPercent))
                    }
                }
            } catch {
                await MainActor.run { var v = local; v.aiNote = "AI error: \(error)"; local = v; aiBusy = false }
            }
        }
    }

    private func saveReport() {
        guard let r = result else { return }
        let csv = ReportBuilder.csv(displayName: file.displayName, fileName: file.url.lastPathComponent,
                                    result: r, span: dgSpan, quality: quality)
        let panel = NSSavePanel()
        panel.nameFieldStringValue = file.displayName.fileSafe + " — DG report.csv"
        panel.allowedContentTypes = [.commaSeparatedText]
        guard panel.runModal() == NSApplication.ModalResponse.OK, let url = panel.url else { return }
        try? csv.write(to: url, atomically: true, encoding: .utf8)
    }

    /// Export a copy of the scan with the applied 2θ displacement baked in. The
    /// original `.xy` is never modified (it remains the backup); the sidecar
    /// records the offset. Header comment documents the shift + source.
    private func exportShiftedXY() {
        guard let p = file.pattern, let r = result, r.twoThetaOffset != 0 else { return }
        let off = r.twoThetaOffset
        var text = String(format: "# shifted %+.4f° from %@ on %@\n", off, file.url.lastPathComponent,
                          Date().formatted(date: .abbreviated, time: .shortened))
        text += "# 2theta\tintensity\n"
        for (x, y) in zip(p.twoTheta, p.intensity) {
            text += String(format: "%.5f\t%g\n", x + off, y)
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = file.displayName.fileSafe + " — shifted.xy"
        guard panel.runModal() == NSApplication.ModalResponse.OK, let url = panel.url else { return }
        try? text.write(to: url, atomically: true, encoding: .utf8)
    }

    private func deg(_ v: Double) -> String { String(format: "%.4f°", v) }
    private func ang(_ v: Double) -> String { String(format: "%.6f Å", v) }
    private func num(_ v: Double, _ p: Int) -> String { String(format: "%.\(p)f", v) }
}
