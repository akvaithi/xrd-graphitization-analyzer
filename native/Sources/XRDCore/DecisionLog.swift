import Foundation

/// One audit record: the spectrum, the features, the AI suggestion, and the
/// human's final deconvolution + DG. Appended as JSONL — this file IS the
/// labeled dataset for later prompt tuning / a local model.
public struct DecisionLogEntry: Codable, Sendable {
    public var timestamp: String
    public var file: String
    public var displayName: String
    public var features: DeconvolutionFeatures
    public var suggestion: Suggestion
    public var finalPeakCount: Int
    public var finalTurbostratic2theta: Double?
    public var finalSubtractBackground: Bool
    public var dgPercent: Double

    public init(file: String, displayName: String, features: DeconvolutionFeatures,
                suggestion: Suggestion, finalPeakCount: Int,
                finalTurbostratic2theta: Double?, finalSubtractBackground: Bool,
                dgPercent: Double) {
        self.timestamp = ISO8601DateFormatter().string(from: Date())
        self.file = file
        self.displayName = displayName
        self.features = features
        self.suggestion = suggestion
        self.finalPeakCount = finalPeakCount
        self.finalTurbostratic2theta = finalTurbostratic2theta
        self.finalSubtractBackground = finalSubtractBackground
        self.dgPercent = dgPercent
    }
}

public enum DecisionLog {
    /// ~/Library/Application Support/XRD Graphitization Analyzer/decisions.jsonl
    public static var fileURL: URL {
        let base = (try? FileManager.default.url(for: .applicationSupportDirectory,
                                                 in: .userDomainMask, appropriateFor: nil, create: true))
            ?? FileManager.default.temporaryDirectory
        let dir = base.appendingPathComponent("XRD Graphitization Analyzer", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("decisions.jsonl")
    }

    public static func append(_ entry: DecisionLogEntry) {
        guard var line = try? JSONEncoder().encode(entry) else { return }
        line.append(0x0A)  // newline
        let url = fileURL
        if let handle = try? FileHandle(forWritingTo: url) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: line)
        } else {
            try? line.write(to: url)
        }
    }
}
