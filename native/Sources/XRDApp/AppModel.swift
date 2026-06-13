import Foundation
import XRDCore

/// One loaded `.xy` file: its standardized name, the raw pattern (for live
/// re-fitting in the detail view), and a default auto-fit DG% for the sidebar.
struct LoadedFile: Identifiable {
    let id = UUID()
    let url: URL
    let displayName: String
    let pattern: XRDPattern?
    let parseError: String?
    let autoDG: Double?

    var dgText: String {
        if pattern == nil { return "—" }
        if let dg = autoDG { return String(format: "%.2f%%", dg) }
        return "fit failed"
    }
    var failed: Bool { pattern == nil || autoDG == nil }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var files: [LoadedFile] = []
    @Published var selection: LoadedFile.ID?
    @Published var openRequested = false

    func requestOpen() { openRequested = true }

    func open(_ urls: [URL]) {
        var added: [LoadedFile] = []
        for url in urls {
            let didScope = url.startAccessingSecurityScopedResource()
            defer { if didScope { url.stopAccessingSecurityScopedResource() } }

            let name = RunParser.parse(fileName: url.lastPathComponent).displayName
            var pattern: XRDPattern? = nil
            var parseError: String? = nil
            var autoDG: Double? = nil
            do {
                let p = try XRDPattern.parse(contentsOf: url)
                pattern = p
                autoDG = (try? GraphitizationAnalyzer(p).run())?.dgPercent
            } catch {
                parseError = String(describing: error)
            }
            added.append(LoadedFile(url: url, displayName: name, pattern: pattern,
                                    parseError: parseError, autoDG: autoDG))
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
