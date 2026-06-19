import SwiftUI
import XRDCore

struct DetailView: View {
    let file: LoadedFile
    @EnvironmentObject private var server: OllamaServer
    @EnvironmentObject private var model: AppModel

    // Persisted per-file deconvolution choices (lives in AppModel; survives nav).
    @State private var local = DeconvSettings()

    // Transient — recomputed by refit() from `local`.
    @State private var calResult: InternalStandard?
    @State private var result: DGResult?
    @State private var fitError: String?
    @State private var quality: ImpurityScan?
    @State private var dgSpan: DGRange?

    // AI assist (local Ollama)
    @State private var ollamaHost = ProcessInfo.processInfo.environment["OLLAMA_HOST"] ?? "http://localhost:11434"
    @State private var aiBusy = false

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
        .onAppear { loadSettings(); refit() }
        .onChange(of: file.id) { loadSettings(); refit() }
        .onChange(of: local) { model.settings[file.id] = local; refit() }
    }

    private func loadSettings() {
        quality = file.pattern.map { ImpurityScan.scan($0) }
        local = model.settings[file.id] ?? model.defaults(for: file)
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

                if let r = result { resultRows(r) }
            }
            .padding(16)
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
        guard let p = file.pattern else { result = nil; return }
        var o = FitOptions()
        o.peakCount = local.peakCount
        o.subtractBackground = local.subtractBg
        o.lockTurbostratic = local.turboLocked
        o.turbostraticCenter = local.turboLocked ? local.turboCenter : nil
        o.anchor002 = (local.anchorOn && local.anchorTarget > 0) ? local.anchorTarget : nil
        // Internal-standard calibration (residual phase) — used when no explicit anchor.
        calResult = nil
        if !local.calStdPhase.isEmpty, o.anchor002 == nil {
            let cal = InternalStandard.calibrate(p, phase: local.calStdPhase)
            calResult = cal
            if cal.significant { o.twoThetaOffset = -cal.offset }
        }
        do {
            result = try GraphitizationAnalyzer(p).run(o); fitError = nil
            var span = o; span.peakCount = 2     // range shares calibration/background context
            dgSpan = dgRange(p, base: span)
        }
        catch { result = nil; fitError = String(describing: error); dgSpan = nil }
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
                if server.bundled {
                    HStack(spacing: 6) {
                        if server.host == nil { ProgressView().controlSize(.small) }
                        Text(server.host != nil ? "Local model ready (gemma3:4b)" : "Local model \(server.status)")
                            .font(.caption).foregroundStyle(server.host != nil ? .green : .secondary)
                    }
                } else {
                    TextField("Ollama host", text: $ollamaHost)
                        .textFieldStyle(.roundedBorder).font(.caption)
                }
                HStack {
                    Button { runAI() } label: { Label("Suggest deconvolution", systemImage: "wand.and.stars") }
                        .disabled(aiBusy || file.pattern == nil || effectiveHost == nil)
                    if aiBusy { ProgressView().controlSize(.small) }
                    Spacer()
                    Button {
                        if let r = result { saveChartPNG(FitChartView(result: r), suggestedName: file.displayName + "_002fit") }
                    } label: { Label("Chart", systemImage: "photo") }
                        .disabled(result == nil)
                    Button { saveReport() } label: { Label("Report (CSV)", systemImage: "square.and.arrow.down") }
                        .disabled(result == nil)
                }
                if let note = local.aiNote {
                    Text(note).font(.caption)
                        .foregroundStyle((local.aiConfidence ?? 1) < 0.8 ? .orange : .secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(server.bundled
                     ? "Runs a model bundled in the app (gemma3:4b) — no setup, nothing leaves your machine. You confirm the result; DG% is computed locally."
                     : "Uses a local Ollama model — nothing leaves your machine. You confirm the result; DG% is computed locally.")
                    .font(.caption2).foregroundStyle(.tertiary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 4)
        }
    }

    /// Bundled server when ready, else the manual host (dev / system Ollama).
    private var effectiveHost: String? {
        if let h = server.host { return h }
        return ollamaHost.isEmpty ? nil : ollamaHost
    }

    private func runAI() {
        guard let p = file.pattern else { return }
        aiBusy = true
        let host = effectiveHost ?? AISuggester.defaultHost
        // Measure displacement deterministically from a residual phase first; feed
        // the AI calibrated data (more accurate than it guessing displacement).
        let cal = InternalStandard.calibrate(p, phase: "auto")
        let aiPattern = cal.significant
            ? XRDPattern(twoTheta: p.twoTheta.map { $0 - cal.offset }, intensity: p.intensity) : p
        Task {
            do {
                let (s, feats) = try await AISuggester.suggest(aiPattern, ollamaHost: host)
                await MainActor.run {
                    if s.amorphousInvalid {
                        var v = local; v.aiConfidence = s.confidence
                        v.aiNote = "⚠︎ Flagged as too amorphous for this method. " + s.rationale
                        local = v; aiBusy = false; return
                    }
                    // Build one settings update so persistence/refit fire once.
                    var v = local
                    v.peakCount = s.peakCount
                    v.subtractBg = s.subtractBackground
                    if let t = s.turbostraticCenter { v.turboCenter = t; v.turboLocked = true }
                    else { v.turboLocked = false }
                    if cal.significant {                       // measured offset beats AI guess
                        v.calStdPhase = "auto"; v.anchorOn = false
                    } else if s.displacementSuspected, s.suggested002Anchor > 0 {
                        v.anchorOn = true; v.anchorTarget = s.suggested002Anchor
                    }
                    v.aiConfidence = s.confidence
                    v.aiNote = ((s.confidence < 0.8) ? "Review suggested (low confidence). " : "") + s.rationale
                    local = v                                  // → onChange persists + refits
                    aiBusy = false
                    if let r = result {
                        DecisionLog.append(DecisionLogEntry(
                            file: file.url.lastPathComponent, displayName: file.displayName,
                            features: feats, suggestion: s,
                            finalPeakCount: v.peakCount,
                            finalTurbostratic2theta: v.turboLocked ? v.turboCenter : nil,
                            finalSubtractBackground: v.subtractBg, dgPercent: r.dgPercent))
                    }
                }
            } catch {
                await MainActor.run { var v = local; v.aiNote = "AI error: \(error)"; local = v; aiBusy = false }
            }
        }
    }

    private func saveReport() {
        guard let r = result else { return }
        var rows: [(String, String)] = [
            ("sample", file.displayName), ("file", file.url.lastPathComponent),
            ("method", r.methodName), ("wavelength_angstrom", String(format: "%.5f", r.wavelength)),
            ("DG_percent", String(format: "%.2f", r.dgPercent)),
            ("DG_sigma", r.dgSigma.map { String(format: "%.2f", $0) } ?? ""),
        ]
        if let s = dgSpan { rows += [("DG_range_low", String(format: "%.2f", s.low)),
                                     ("DG_range_high", String(format: "%.2f", s.high))] }
        rows += [
            ("peak_count", "\(r.peakCount)"),
            ("graphitic_2theta", String(format: "%.4f", r.graphitic.xc)),
            ("graphitic_FWHM", String(format: "%.4f", r.graphitic.w)),
            ("graphitic_mu", String(format: "%.4f", r.graphitic.mu)),
            ("graphitic_d_nm", String(format: "%.5f", r.graphitic.dSpacing)),
        ]
        if let t = r.turbostratic {
            rows += [("turbostratic_2theta", String(format: "%.4f", t.xc)),
                     ("turbostratic_FWHM", String(format: "%.4f", t.w)),
                     ("turbostratic_d_nm", String(format: "%.5f", t.dSpacing))]
        }
        rows += [
            ("X_graphitic", String(format: "%.4f", r.areaFractionGraphitic)),
            ("X_turbostratic", String(format: "%.4f", r.areaFractionTurbostratic)),
            ("d_prime_nm", String(format: "%.5f", r.dPrimeWeighted)),
            ("crystallite_Lc_A", String(format: "%.1f", r.crystalliteLc)),
            ("baseline_y0", String(format: "%.3f", r.y0)),
            ("two_theta_offset", String(format: "%+.3f", r.twoThetaOffset)),
            ("fit_R2", String(format: "%.5f", r.fitR2)),
        ]
        if let q = quality { rows.append(("data_quality", q.verdict)) }
        let csv = "key,value\n" + rows.map { "\($0.0),\"\($0.1)\"" }.joined(separator: "\n") + "\n"
        let panel = NSSavePanel()
        panel.nameFieldStringValue = file.displayName.replacingOccurrences(of: " ", with: "_") + "_DG_report.csv"
        panel.allowedContentTypes = [.commaSeparatedText]
        guard panel.runModal() == NSApplication.ModalResponse.OK, let url = panel.url else { return }
        try? csv.write(to: url, atomically: true, encoding: .utf8)
    }

    private func deg(_ v: Double) -> String { String(format: "%.4f°", v) }
    private func ang(_ v: Double) -> String { String(format: "%.6f Å", v) }
    private func num(_ v: Double, _ p: Int) -> String { String(format: "%.\(p)f", v) }
}
