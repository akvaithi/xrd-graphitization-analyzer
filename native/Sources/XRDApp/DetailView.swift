import SwiftUI
import XRDCore

struct DetailView: View {
    let file: LoadedFile
    @EnvironmentObject private var server: OllamaServer

    @State private var peakCount = 2
    @State private var subtractBg = false
    @State private var turboCenter = 26.2
    @State private var turboLocked = false
    @State private var anchorOn = false
    @State private var anchorTarget = 26.54
    @State private var calStdPhase = ""          // "" off, "auto"/"Fe3C"/"alpha-Fe"/"CaO"
    @State private var calResult: InternalStandard?
    @State private var result: DGResult?
    @State private var fitError: String?
    @State private var quality: ImpurityScan?
    @State private var dgSpan: DGRange?

    // AI assist (local Ollama)
    @State private var ollamaHost = ProcessInfo.processInfo.environment["OLLAMA_HOST"] ?? "http://localhost:11434"
    @State private var aiBusy = false
    @State private var aiNote: String?
    @State private var aiConfidence: Double?

    var body: some View {
        Group {
            if file.pattern == nil {
                ContentUnavailableView("Couldn't read file", systemImage: "exclamationmark.triangle",
                    description: Text(file.parseError ?? "Unknown error"))
            } else {
                HSplitView {
                    controlsAndResults
                        .frame(minWidth: 320, idealWidth: 350, maxWidth: 440)
                    Group {
                        if let r = result {
                            FitChartView(result: r).padding(16)
                        } else {
                            ContentUnavailableView("Fit failed", systemImage: "chart.xyaxis.line",
                                description: Text(fitError ?? "Adjust the deconvolution settings."))
                        }
                    }
                    .frame(minWidth: 440)
                }
            }
        }
        .navigationTitle(file.displayName)
        .navigationSubtitle(file.url.lastPathComponent)
        .onAppear { seedFromAuto(); refit() }
        .onChange(of: file.id) { seedFromAuto(); refit() }
    }

    // MARK: controls + readout

    private var controlsAndResults: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let r = result { dgCallout(r) }
                if let q = quality, !q.hits.isEmpty { qualityCard(q) }

                GroupBox("Deconvolution") {
                    VStack(alignment: .leading, spacing: 12) {
                        Picker("Peaks", selection: $peakCount) {
                            Text("1 peak").tag(1)
                            Text("2 peaks").tag(2)
                        }
                        .pickerStyle(.segmented)
                        .onChange(of: peakCount) { refit() }

                        if peakCount == 2 {
                            HStack {
                                Toggle("Lock turbostratic 2θ", isOn: $turboLocked)
                                    .onChange(of: turboLocked) { refit() }
                                Spacer()
                                if turboLocked {
                                    Button("Auto") { turboLocked = false; refit() }
                                        .controlSize(.small)
                                }
                            }
                            HStack(spacing: 8) {
                                Text("Turbostratic").foregroundStyle(.secondary).font(.caption)
                                Slider(value: $turboCenter, in: 25.1...26.45) { editing in
                                    if !editing { turboLocked = true; refit() }
                                }
                                Text(String(format: "%.3f°", turboCenter))
                                    .font(.caption).monospacedDigit().frame(width: 56, alignment: .trailing)
                            }
                        }

                        Toggle("Subtract sloped background (24–26.5°)", isOn: $subtractBg)
                            .onChange(of: subtractBg) { refit() }

                        Divider()
                        HStack(spacing: 8) {
                            Text("Internal-std calib").foregroundStyle(.secondary).font(.caption)
                            Picker("", selection: $calStdPhase) {
                                Text("off").tag(""); Text("auto").tag("auto")
                                Text("Fe₃C").tag("Fe3C"); Text("α-Fe").tag("alpha-Fe"); Text("CaO").tag("CaO")
                            }.labelsHidden().frame(width: 90)
                                .onChange(of: calStdPhase) { refit() }
                            Spacer()
                            if !calStdPhase.isEmpty, let c = calResult, let ph = c.phase {
                                Text(c.significant
                                     ? String(format: "%@ %+.3f° ✓", ph, c.offset)
                                     : String(format: "%@ %+.3f° (noise)", ph, c.offset))
                                    .font(.caption2).monospacedDigit()
                                    .foregroundStyle(c.significant ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                            }
                        }
                        Toggle("Anchor (002) to a known angle", isOn: $anchorOn)
                            .onChange(of: anchorOn) { refit() }
                        if anchorOn {
                            HStack(spacing: 8) {
                                Text("Anchor (002) to").foregroundStyle(.secondary).font(.caption)
                                TextField("26.54", value: $anchorTarget, format: .number)
                                    .textFieldStyle(.roundedBorder).frame(width: 70)
                                    .onSubmit { refit() }
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
            Text(r.dgSigma != nil
                 ? String(format: "%.2f ± %.2f %%", r.dgPercent, r.dgSigma!)
                 : String(format: "%.2f %%", r.dgPercent))
                .font(.system(size: 36, weight: .semibold, design: .rounded))
                .foregroundStyle(.tint)
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

    private func seedFromAuto() {
        peakCount = 2; subtractBg = false; turboLocked = false; anchorOn = false
        calStdPhase = ""; calResult = nil
        quality = file.pattern.map { ImpurityScan.scan($0) }
        if let p = file.pattern, let auto = try? GraphitizationAnalyzer(p).run(),
           let t = auto.turbostratic {
            turboCenter = t.xc
        } else {
            turboCenter = 26.2
        }
    }

    private func refit() {
        guard let p = file.pattern else { result = nil; return }
        var o = FitOptions()
        o.peakCount = peakCount
        o.subtractBackground = subtractBg
        o.lockTurbostratic = turboLocked
        o.turbostraticCenter = turboLocked ? turboCenter : nil
        o.anchor002 = (anchorOn && anchorTarget > 0) ? anchorTarget : nil
        // Internal-standard calibration (residual phase) — used when no explicit anchor.
        calResult = nil
        if !calStdPhase.isEmpty, o.anchor002 == nil {
            let cal = InternalStandard.calibrate(p, phase: calStdPhase)
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
                    if let c = aiConfidence {
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
                }
                if let note = aiNote {
                    Text(note).font(.caption)
                        .foregroundStyle((aiConfidence ?? 1) < 0.8 ? .orange : .secondary)
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
        aiBusy = true; aiNote = nil; aiConfidence = nil
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
                    aiConfidence = s.confidence
                    if s.amorphousInvalid {
                        aiNote = "⚠︎ Flagged as too amorphous for this method. " + s.rationale
                        aiBusy = false; return
                    }
                    peakCount = s.peakCount
                    subtractBg = s.subtractBackground
                    if let t = s.turbostraticCenter { turboCenter = t; turboLocked = true }
                    else { turboLocked = false }
                    // Prefer the measured internal-standard offset over the AI guess.
                    if cal.significant {
                        calStdPhase = "auto"; anchorOn = false
                    } else if s.displacementSuspected, s.suggested002Anchor > 0 {
                        anchorOn = true; anchorTarget = s.suggested002Anchor
                    }
                    refit()
                    aiNote = ((s.confidence < 0.8) ? "Review suggested (low confidence). " : "") + s.rationale
                    if let r = result {
                        DecisionLog.append(DecisionLogEntry(
                            file: file.url.lastPathComponent, displayName: file.displayName,
                            features: feats, suggestion: s,
                            finalPeakCount: peakCount,
                            finalTurbostratic2theta: turboLocked ? turboCenter : nil,
                            finalSubtractBackground: subtractBg, dgPercent: r.dgPercent))
                    }
                    aiBusy = false
                }
            } catch {
                await MainActor.run { aiNote = "AI error: \(error)"; aiBusy = false }
            }
        }
    }

    private func deg(_ v: Double) -> String { String(format: "%.4f°", v) }
    private func ang(_ v: Double) -> String { String(format: "%.6f Å", v) }
    private func num(_ v: Double, _ p: Int) -> String { String(format: "%.\(p)f", v) }
}
