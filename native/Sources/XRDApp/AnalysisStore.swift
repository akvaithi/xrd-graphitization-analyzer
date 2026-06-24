import Foundation
import XRDCore

/// Codable snapshot of the engine `DGResult` — the fields the app needs to
/// persist and to show in Compare / CSV / history without making the whole engine
/// type Codable.
struct DGSnapshot: Codable {
    var dgPercent: Double
    var dgSigma: Double?
    var peakCount: Int
    var graphiticXc: Double
    var graphiticW: Double
    var graphiticMu: Double
    var graphiticD: Double
    var turboXc: Double?
    var turboW: Double?
    var turboD: Double?
    var areaGraphitic: Double
    var areaTurbostratic: Double
    var dPrime: Double
    var lc: Double
    var y0: Double
    var twoThetaOffset: Double
    var fitR2: Double
    var methodName: String
    var wavelength: Double

    init(_ r: DGResult) {
        dgPercent = r.dgPercent; dgSigma = r.dgSigma; peakCount = r.peakCount
        graphiticXc = r.graphitic.xc; graphiticW = r.graphitic.w
        graphiticMu = r.graphitic.mu; graphiticD = r.graphitic.dSpacing
        turboXc = r.turbostratic?.xc; turboW = r.turbostratic?.w; turboD = r.turbostratic?.dSpacing
        areaGraphitic = r.areaFractionGraphitic; areaTurbostratic = r.areaFractionTurbostratic
        dPrime = r.dPrimeWeighted; lc = r.crystalliteLc; y0 = r.y0
        twoThetaOffset = r.twoThetaOffset; fitR2 = r.fitR2
        methodName = r.methodName; wavelength = r.wavelength
    }
}

/// One past analysis, kept so prior deconvolutions aren't lost when settings change.
struct HistoryEntry: Codable {
    var timestamp: Date
    var settings: DeconvSettings
    var dgPercent: Double?
    var note: String?
}

/// The persisted per-file analysis record (the sidecar payload).
struct AnalysisRecord: Codable {
    var schemaVersion = 1
    var sourceFileName: String
    var settings: DeconvSettings
    var result: DGSnapshot?
    var shiftOffset: Double?          // applied 2θ displacement, if any
    var flaggedForRedo = false        // user marked this scan for re-running
    var history: [HistoryEntry] = []
    var updatedAt = Date()
    var appVersion: String?
}

extension AnalysisRecord {
    enum CodingKeys: String, CodingKey {
        case schemaVersion, sourceFileName, settings, result, shiftOffset
        case flaggedForRedo, history, updatedAt, appVersion
    }
    /// Tolerant decoder so sidecars written by older builds still load — missing
    /// keys (e.g. `flaggedForRedo`, added later) fall back to defaults instead of
    /// failing the whole decode (which would silently drop a saved analysis).
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(sourceFileName: (try? c.decode(String.self, forKey: .sourceFileName)) ?? "",
                  settings: (try? c.decode(DeconvSettings.self, forKey: .settings)) ?? DeconvSettings())
        schemaVersion = (try? c.decode(Int.self, forKey: .schemaVersion)) ?? 1
        result = try? c.decode(DGSnapshot.self, forKey: .result)
        shiftOffset = try? c.decode(Double.self, forKey: .shiftOffset)
        flaggedForRedo = (try? c.decode(Bool.self, forKey: .flaggedForRedo)) ?? false
        history = (try? c.decode([HistoryEntry].self, forKey: .history)) ?? []
        updatedAt = (try? c.decode(Date.self, forKey: .updatedAt)) ?? Date()
        appVersion = try? c.decode(String.self, forKey: .appVersion)
    }
}

/// Reads/writes a sidecar JSON next to each scan: `MyScan.xy.xrda.json`. The raw
/// `.xy` is never modified — it is itself the original-data backup. Auto-loaded
/// when a file is opened; saved (coalesced) when the analysis changes. Write
/// failures (e.g. a read-only location) are reported, not fatal.
enum AnalysisStore {
    static let maxHistory = 20

    static func sidecarURL(for url: URL) -> URL {
        URL(fileURLWithPath: url.path + ".xrda.json")
    }

    private static var encoder: JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        e.dateEncodingStrategy = .iso8601
        return e
    }
    private static var decoder: JSONDecoder {
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d
    }

    static func load(for url: URL) -> AnalysisRecord? {
        guard let data = try? Data(contentsOf: sidecarURL(for: url)) else { return nil }
        return try? decoder.decode(AnalysisRecord.self, from: data)
    }

    @discardableResult
    static func save(_ record: AnalysisRecord, for url: URL) -> Bool {
        guard let data = try? encoder.encode(record) else { return false }
        do { try data.write(to: sidecarURL(for: url), options: .atomic); return true }
        catch { return false }
    }

    private static let appVersion =
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String

    /// Build/update the record for a file: set current settings + result snapshot,
    /// append a history entry when the settings differ from the last one (capped),
    /// and persist. Returns whether the write succeeded.
    @discardableResult
    static func update(for url: URL, settings: DeconvSettings, result: DGResult?,
                       flaggedForRedo: Bool? = nil) -> Bool {
        var rec = load(for: url) ?? AnalysisRecord(sourceFileName: url.lastPathComponent, settings: settings)
        // Append history only when the deconvolution settings actually changed.
        if rec.history.last?.settings != settings {
            rec.history.append(HistoryEntry(timestamp: Date(), settings: settings,
                                            dgPercent: result?.dgPercent, note: settings.aiNote))
            if rec.history.count > maxHistory { rec.history.removeFirst(rec.history.count - maxHistory) }
        }
        rec.settings = settings
        rec.result = result.map(DGSnapshot.init)
        rec.shiftOffset = result.flatMap { $0.twoThetaOffset != 0 ? $0.twoThetaOffset : nil }
        if let f = flaggedForRedo { rec.flaggedForRedo = f }   // nil = preserve existing
        rec.updatedAt = Date()
        rec.appVersion = appVersion
        return save(rec, for: url)
    }
}
