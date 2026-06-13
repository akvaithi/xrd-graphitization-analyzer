import SwiftUI
import XRDCore

struct DetailView: View {
    let file: LoadedFile

    @State private var peakCount = 2
    @State private var subtractBg = false
    @State private var turboCenter = 26.2
    @State private var turboLocked = false
    @State private var result: DGResult?
    @State private var fitError: String?

    // AI assist
    @State private var aiProvider: AISuggester.Provider =
        (ProcessInfo.processInfo.environment["AI_PROVIDER"].flatMap { AISuggester.Provider(rawValue: $0.lowercased()) }) ?? .claude
    @State private var apiKey = ProcessInfo.processInfo.environment["ANTHROPIC_API_KEY"] ?? ""
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
                .font(.system(size: 40, weight: .semibold, design: .rounded))
                .foregroundStyle(.tint)
            Text("\(r.peakCount == 1 ? "single peak" : "area-weighted") · R² \(String(format: "%.4f", r.fitR2))")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 16)
        .background(.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
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
                ("Baseline y0", num(r.y0, 3)),
                ("Wavelength λ", String(format: "%.5f Å", r.wavelength))])
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
        peakCount = 2; subtractBg = false; turboLocked = false
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
        do { result = try GraphitizationAnalyzer(p).run(o); fitError = nil }
        catch { result = nil; fitError = String(describing: error) }
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
                Picker("", selection: $aiProvider) {
                    Text("Claude (cloud)").tag(AISuggester.Provider.claude)
                    Text("Ollama (local)").tag(AISuggester.Provider.ollama)
                }
                .pickerStyle(.segmented).labelsHidden()
                if aiProvider == .claude && apiKey.isEmpty {
                    SecureField("ANTHROPIC_API_KEY", text: $apiKey)
                        .textFieldStyle(.roundedBorder).font(.caption)
                } else if aiProvider == .ollama {
                    TextField("Ollama host", text: $ollamaHost)
                        .textFieldStyle(.roundedBorder).font(.caption)
                }
                HStack {
                    Button { runAI() } label: { Label("Suggest deconvolution", systemImage: "wand.and.stars") }
                        .disabled(aiBusy || file.pattern == nil || (aiProvider == .claude && apiKey.isEmpty))
                    if aiBusy { ProgressView().controlSize(.small) }
                }
                if let note = aiNote {
                    Text(note).font(.caption)
                        .foregroundStyle((aiConfidence ?? 1) < 0.8 ? .orange : .secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(aiProvider == .ollama
                     ? "Runs locally via Ollama — nothing leaves your machine. You confirm the result; DG% is computed locally."
                     : "Sends derived numeric features (not raw data) to the Anthropic API; you confirm the result. DG% is computed locally.")
                    .font(.caption2).foregroundStyle(.tertiary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 4)
        }
    }

    private func runAI() {
        guard let p = file.pattern else { return }
        aiBusy = true; aiNote = nil; aiConfidence = nil
        let provider = aiProvider, key = apiKey, host = ollamaHost
        Task {
            do {
                let (s, feats) = try await AISuggester.suggest(
                    p, provider: provider, apiKey: key, ollamaHost: host)
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
