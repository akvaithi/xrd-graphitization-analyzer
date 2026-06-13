import Foundation

/// Run parameters parsed from a (messy) `.xy` file name, plus a standardized
/// display name. Port of `run_parser.py`.
public struct RunInfo: Sendable {
    public var carbonType: String?
    public var carbonRatio: Double?
    public var feRatio: Double?
    public var hasFe: Bool = false
    public var caco3Ratio: Double?
    public var temperatureC: Int?
    public var timeH: Double?
    public var form: String?
    public var wash: String?
    public var displayName: String = ""
}

public enum RunParser {

    public static func parse(fileName: String) -> RunInfo {
        let raw = stripSuffix(fileName)
        var info = RunInfo()

        // carbon source: amount before the token ("2CPC") or grams after ("LSPC(2g)")
        if let g = match(#"(\d+(?:\.\d+)?)?\s*(LSPC|GPC|CPC|GCP)(?:\s*\(\s*(\d+(?:\.\d+)?)\s*g?\s*\))?"#, raw) {
            info.carbonType = (g[2] ?? "").uppercased().replacingOccurrences(of: "GCP", with: "GPC")
            info.carbonRatio = num(g[1]) ?? num(g[3])
        }
        // iron: number before 'Fe' or grams after 'Fe(6g)'
        if let g = match(#"(?:(\d+(?:\.\d+)?)\s*Fe(?![A-Za-z])|Fe\s*\(\s*(\d+(?:\.\d+)?)\s*g)"#, raw) {
            info.feRatio = num(g[1]) ?? num(g[2])
        }
        info.hasFe = match(#"(?<![A-Za-z])Fe(?![A-Za-z])"#, raw) != nil
        // CaCO3: number before or grams after
        if let g = match(#"(?:(\d+(?:\.\d+)?)\s*CaCO3|CaCO3\s*\(\s*(\d+(?:\.\d+)?)\s*g)"#, raw) {
            info.caco3Ratio = num(g[1]) ?? num(g[2])
        }
        info.temperatureC = parseTemperature(raw)
        info.timeH = parseTime(raw)
        info.form = match(#"puck"#, raw) != nil ? "puck"
            : (match(#"powder"#, raw) != nil ? "powder" : nil)
        info.wash = parseWash(raw)
        info.displayName = standardName(info, fallback: raw)
        return info
    }

    /// Standardized name, e.g. `2g GPC · 6g Fe · 0.8125g CaCO₃ · 1200°C · 5H · Puck`.
    public static func standardName(_ p: RunInfo, fallback: String) -> String {
        var parts: [String] = []
        if let ct = p.carbonType {
            if let r = p.carbonRatio { parts.append("\(fmt(r))g \(ct)") } else { parts.append(ct) }
        }
        if let fr = p.feRatio { parts.append("\(fmt(fr))g Fe") }
        else if p.hasFe { parts.append("Fe") }
        if let cc = p.caco3Ratio { parts.append("\(fmt(cc))g CaCO₃") }
        if let t = p.temperatureC { parts.append("\(t)°C") }
        if let th = p.timeH { parts.append("\(fmt(th))H") }
        if let f = p.form { parts.append(f.capitalized) }
        if let w = p.wash { parts.append(w.capitalized) }
        return parts.isEmpty ? fallback : parts.joined(separator: " · ")
    }

    // MARK: - helpers

    static func stripSuffix(_ name: String) -> String {
        var base = name.replacingOccurrences(
            of: #"\.(xy|txt|dat)$"#, with: "", options: [.regularExpression, .caseInsensitive])
        base = base.replacingOccurrences(
            of: #"[_\s-]*exported[_\s-]*"#, with: " ", options: [.regularExpression, .caseInsensitive])
        base = base.replacingOccurrences(
            of: #"\(\s*\d+\s*\)"#, with: " ", options: [.regularExpression])
        base = base.replacingOccurrences(
            of: #"\s+"#, with: " ", options: [.regularExpression])
        return base.trimmingCharacters(in: .whitespaces)
    }

    static func parseTemperature(_ raw: String) -> Int? {
        if let all = matches(#"(?<!\d)(\d{3,4})\s*C\b"#, raw) {
            for g in all { if let v = g[1].flatMap({ Int($0) }), (800...1600).contains(v) { return v } }
        }
        if let all = matches(#"(?<!\d)(\d{3,4})(?!\d)"#, raw) {
            for g in all { if let v = g[1].flatMap({ Int($0) }), (800...1600).contains(v) { return v } }
        }
        return nil
    }

    static func parseTime(_ raw: String) -> Double? {
        if let g = match(#"(\d+(?:\.\d+)?)\s*(?:hrs|hr|h)(?![A-Za-z])"#, raw) { return num(g[1]) }
        if let g = match(#"(\d+(?:\.\d+)?)\s*min(?:s|utes?)?(?![A-Za-z])"#, raw) {
            if let m = num(g[1]) { return (m / 60.0 * 1e4).rounded() / 1e4 }
        }
        return nil
    }

    static func parseWash(_ raw: String) -> String? {
        let s = raw.lowercased()
        if match(#"no\s*wash"#, s) != nil { return "no wash" }
        if match(#"(aftr|after)[\s_-]*wash"#, s) != nil { return "after wash" }
        if match(#"before[\s_-]*wash"#, s) != nil { return "before wash" }
        if match(#"wash"#, s) != nil { return "washed" }
        return nil
    }

    static func fmt(_ v: Double) -> String {
        v == v.rounded() ? String(Int(v)) : String(format: "%g", v)
    }
    static func num(_ s: String?) -> Double? { s.flatMap { Double($0) } }

    /// First regex match → captured groups (index 0 = whole match), nil if no match.
    static func match(_ pattern: String, _ s: String) -> [String?]? {
        matches(pattern, s)?.first
    }

    /// All regex matches, each as its capture-group array.
    static func matches(_ pattern: String, _ s: String) -> [[String?]]? {
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return nil }
        let ns = s as NSString
        let found = re.matches(in: s, options: [], range: NSRange(location: 0, length: ns.length))
        if found.isEmpty { return nil }
        return found.map { m in
            (0..<m.numberOfRanges).map { i -> String? in
                let r = m.range(at: i)
                return r.location == NSNotFound ? nil : ns.substring(with: r)
            }
        }
    }
}
