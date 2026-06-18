import Foundation
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
struct DeconvSettings: Equatable {
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
    @Published var files: [LoadedFile] = []
    @Published var selection: LoadedFile.ID?
    @Published var openRequested = false
    @Published var settings: [LoadedFile.ID: DeconvSettings] = [:]

    func requestOpen() { openRequested = true }

    /// Import defaults for a file (turbostratic seed from the auto-fit).
    func defaults(for file: LoadedFile) -> DeconvSettings {
        var s = DeconvSettings()
        if let t = file.autoResult?.turbostratic { s.turboCenter = t.xc }
        return s
    }

    func open(_ urls: [URL]) {
        var added: [LoadedFile] = []
        for url in urls {
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
            added.append(LoadedFile(url: url, displayName: info.displayName, pattern: pattern,
                                    parseError: parseError, info: info, autoResult: autoResult))
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
}
