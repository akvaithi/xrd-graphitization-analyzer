import Foundation
import SwiftUI
import AppKit
import XRDCore

/// One loaded `.xy` file: standardized name, raw pattern (for live re-fitting),
/// parsed run parameters, and the default auto-fit (for the Compare table).
struct LoadedFile: Identifiable {
    let id = UUID()
    let url: URL
    let displayName: String
    let pattern: XRDPattern?
    let parseError: String?
    let info: RunInfo?
    let autoResult: DGResult?

    var dgText: String {
        if pattern == nil { return "—" }
        if let r = autoResult { return String(format: "%.2f%%", r.dgPercent) }
        return "fit failed"
    }
    var failed: Bool { pattern == nil || autoResult == nil }
}

/// Per-file deconvolution choices + last AI run — persisted in AppModel so they
/// survive navigation and tab switches (DetailView is recreated on each visit).
struct DeconvSettings: Equatable, Codable {
    var peakCount = 2
    var subtractBg = false
    var turboLocked = false
    var turboCenter = 26.2
    var anchorOn = false
    var anchorTarget = 26.54
    var calStdPhase = ""          // "" off, "auto"/"Fe3C"/"alpha-Fe"/"CaO"
    var aiNote: String? = nil
    var aiConfidence: Double? = nil
}

@MainActor
final class AppModel: ObservableObject {
    /// Shared instance so the AppDelegate (Finder "open" events) and the SwiftUI
    /// scene drive the same model.
    static let shared = AppModel()

    @Published var files: [LoadedFile] = []
    @Published var selection: LoadedFile.ID?
    @Published var openRequested = false
    @Published var settings: [LoadedFile.ID: DeconvSettings] = [:]
    /// Latest computed fit per file — the single source of truth the sidebar,
    /// Compare tab, and CSV exports read (falls back to the import auto-fit).
    @Published var results: [LoadedFile.ID: DGResult] = [:]
    /// User-set "re-scan this sample" flags (persisted in the sidecar).
    @Published var redoFlags: [LoadedFile.ID: Bool] = [:]

    // Batch-operation progress (AI Suggest all / Export all).
    @Published var batchBusy = false
    @Published var batchProgress = 0.0
    @Published var batchNote: String?
    @Published var requestExportAll = false   // set by the Analyze menu → RootView shows the folder picker

    func requestOpen() { openRequested = true }

    /// Import defaults for a file (turbostratic seed from the auto-fit).
    func defaults(for file: LoadedFile) -> DeconvSettings {
        var s = DeconvSettings()
        if let t = file.autoResult?.turbostratic { s.turboCenter = t.xc }
        return s
    }

    /// The current fit for a file: the refined interactive result if one exists,
    /// else the import auto-fit. Everything user-facing should read through this.
    func currentResult(_ file: LoadedFile) -> DGResult? {
        results[file.id] ?? file.autoResult
    }
    func dgText(for file: LoadedFile) -> String {
        if file.pattern == nil { return "—" }
        if let r = currentResult(file) { return String(format: "%.2f%%", r.dgPercent) }
        return "fit failed"
    }
    func failed(for file: LoadedFile) -> Bool {
        file.pattern == nil || currentResult(file) == nil
    }

    func open(_ urls: [URL]) {
        // Only ingest .xy scans — never the sidecar `.xy.xrda.json` or other files.
        let xyURLs = urls.filter { $0.pathExtension.lowercased() == "xy" }
        var added: [LoadedFile] = []
        for url in xyURLs {
            // Already open (e.g. a duplicate Finder event) → just focus it.
            if let existing = files.first(where: {
                $0.url.standardizedFileURL == url.standardizedFileURL }) {
                selection = existing.id
                continue
            }
            let didScope = url.startAccessingSecurityScopedResource()
            defer { if didScope { url.stopAccessingSecurityScopedResource() } }

            let info = RunParser.parse(fileName: url.lastPathComponent)
            var pattern: XRDPattern? = nil
            var parseError: String? = nil
            var autoResult: DGResult? = nil
            do {
                let p = try XRDPattern.parse(contentsOf: url)
                pattern = p
                autoResult = try? GraphitizationAnalyzer(p).run()
            } catch {
                parseError = String(describing: error)
            }
            let lf = LoadedFile(url: url, displayName: info.displayName, pattern: pattern,
                                parseError: parseError, info: info, autoResult: autoResult)
            added.append(lf)

            // Auto-load a prior analysis (sidecar) and recompute its result now, so
            // the sidebar / Compare reflect the saved deconvolution immediately.
            if let rec = AnalysisStore.load(for: url) {
                settings[lf.id] = rec.settings
                redoFlags[lf.id] = rec.flaggedForRedo
                if let p = pattern { results[lf.id] = FitRunner.run(p, rec.settings).result }
            }
        }
        files.append(contentsOf: added)
        if selection == nil { selection = files.first?.id }
        else { selection = added.first?.id ?? selection }
    }

    func selected() -> LoadedFile? {
        guard let id = selection else { return nil }
        return files.first { $0.id == id }
    }

    func openLaunchArguments() {
        guard files.isEmpty else { return }
        let fm = FileManager.default
        let urls = CommandLine.arguments.dropFirst()
            .filter { fm.fileExists(atPath: $0) }
            .map { URL(fileURLWithPath: $0) }
        if !urls.isEmpty { open(urls) }
    }

    // MARK: - File management

    /// Remove a file from the session (the .xy and its sidecar are left on disk).
    func remove(_ id: LoadedFile.ID) {
        files.removeAll { $0.id == id }
        settings[id] = nil; results[id] = nil; redoFlags[id] = nil
        if selection == id { selection = files.first?.id }
    }

    func revealInFinder(_ file: LoadedFile) {
        NSWorkspace.shared.activateFileViewerSelecting([file.url])
    }

    // MARK: - Scan quality / re-scan flags

    func isFlaggedForRedo(_ file: LoadedFile) -> Bool { redoFlags[file.id] ?? false }

    func toggleRedo(_ file: LoadedFile) {
        let new = !(redoFlags[file.id] ?? false)
        redoFlags[file.id] = new
        AnalysisStore.update(for: file.url, settings: settings[file.id] ?? defaults(for: file),
                             result: currentResult(file), flaggedForRedo: new)
    }

    /// A reason to re-scan, or nil — when the graphitic (002) centre falls outside
    /// the acceptable band set in Settings (default 26.50–26.60°).
    func redoRecommended(_ file: LoadedFile) -> String? {
        guard let xc = currentResult(file)?.graphitic.xc else { return nil }
        let d = UserDefaults.standard
        let lo = d.object(forKey: "redoMin") as? Double ?? 26.50
        let hi = d.object(forKey: "redoMax") as? Double ?? 26.60
        if xc < lo { return String(format: "graphitic 2θ %.3f° < %.2f° — consider re-scanning", xc, lo) }
        if xc > hi { return String(format: "graphitic 2θ %.3f° > %.2f° — consider re-scanning", xc, hi) }
        return nil
    }

    // MARK: - Batch operations

    /// Run the AI suggester over every loaded file, applying each result.
    func suggestAllAI(_ config: AIConfig) async {
        guard !batchBusy, config.canSuggest else { return }
        batchBusy = true; batchProgress = 0; batchNote = nil
        let targets = files.filter { $0.pattern != nil }
        var failures = 0
        for (i, f) in targets.enumerated() {
            batchNote = "Suggesting \(i + 1)/\(targets.count): \(f.displayName)"
            if let p = f.pattern {
                do {
                    let base = settings[f.id] ?? defaults(for: f)
                    let out = try await AISuggestionService.suggest(
                        pattern: p, active: config.active, host: config.host, base: base)
                    settings[f.id] = out.settings
                    let r = FitRunner.run(p, out.settings).result
                    results[f.id] = r
                    AnalysisStore.update(for: f.url, settings: out.settings, result: r)
                } catch { failures += 1 }
            }
            batchProgress = Double(i + 1) / Double(max(targets.count, 1))
        }
        batchNote = failures == 0 ? "Suggested \(targets.count) file(s)."
                                  : "Suggested \(targets.count - failures)/\(targets.count) (\(failures) failed)."
        batchBusy = false
    }

    /// Export a report CSV + fit PNG per file, plus one consolidated runs CSV.
    func exportAll(to folder: URL, options: ChartOptions, includeTitle: Bool) async {
        guard !batchBusy else { return }
        batchBusy = true; batchProgress = 0; batchNote = nil
        let targets = files.filter { currentResult($0) != nil }
        var consolidated = ReportBuilder.consolidatedHeader()
        var written = 0
        for (i, f) in targets.enumerated() {
            batchNote = "Exporting \(i + 1)/\(targets.count): \(f.displayName)"
            consolidated += ReportBuilder.consolidatedRow(fileName: f.url.lastPathComponent,
                                                          info: f.info, result: currentResult(f))
            if let r = currentResult(f), let p = f.pattern {
                let stem = f.displayName.fileSafe
                // per-file report CSV
                var base = FitRunner.options(p, settings[f.id] ?? defaults(for: f)).0
                base.peakCount = 2
                let span = dgRange(p, base: base)
                let quality = ImpurityScan.scan(p)
                let csv = ReportBuilder.csv(displayName: f.displayName, fileName: f.url.lastPathComponent,
                                            result: r, span: span, quality: quality)
                try? csv.write(to: folder.appendingPathComponent("\(stem) — DG report.csv"),
                               atomically: true, encoding: .utf8)
                // fit PNG
                renderExportChart(
                    ExportChart(result: r,
                                title: includeTitle ? f.displayName : nil,
                                subtitle: includeTitle ? String(format: "DG %.2f%% · (002) fit", r.dgPercent) : nil,
                                options: options),
                    to: folder.appendingPathComponent("\(stem) — 002 fit.png"))
                written += 1
            }
            batchProgress = Double(i + 1) / Double(max(targets.count, 1))
        }
        try? consolidated.write(to: folder.appendingPathComponent("XRD runs.csv"),
                                atomically: true, encoding: .utf8)
        batchNote = "Exported \(written) file(s) + XRD runs.csv to \(folder.lastPathComponent)."
        batchBusy = false
    }
}
