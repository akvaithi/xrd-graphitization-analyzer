import Foundation

public enum XRDError: Error, CustomStringConvertible {
    case noData
    case tooFewPoints(Int)
    case fitFailed(String)

    public var description: String {
        switch self {
        case .noData: return "No numeric (2θ, intensity) data found in input."
        case .tooFewPoints(let n): return "Only \(n) point(s) in the analysis window — too few to fit."
        case .fitFailed(let m): return "fit did not converge — \(m)"
        }
    }
}

/// A two-column XRD pattern with windowing + linear-baseline helpers.
/// Faithful port of `xrd_analyzer.XRDPattern`.
public struct XRDPattern: Sendable {
    public let twoTheta: [Double]
    public let intensity: [Double]

    public init(twoTheta: [Double], intensity: [Double]) {
        self.twoTheta = twoTheta
        self.intensity = intensity
    }

    /// Parse `.xy` text: skip blank/comment/header lines, take the first two
    /// whitespace-separated floats per line.
    public static func parse(_ text: String) throws -> XRDPattern {
        var tt: [Double] = []
        var inten: [Double] = []
        text.enumerateLines { line, _ in
            let s = line.trimmingCharacters(in: .whitespaces)
            guard let first = s.first else { return }
            if first == "#" || first == "!" || first == "'" || first.isLetter { return }
            let parts = s.split(whereSeparator: { $0 == " " || $0 == "\t" })
            guard parts.count >= 2,
                  let a = Double(parts[0]), let b = Double(parts[1]) else { return }
            tt.append(a); inten.append(b)
        }
        if tt.isEmpty { throw XRDError.noData }
        return XRDPattern(twoTheta: tt, intensity: inten)
    }

    public static func parse(contentsOf url: URL) throws -> XRDPattern {
        let data = try Data(contentsOf: url)
        let text = String(decoding: data, as: UTF8.self)
        return try parse(text)
    }

    /// (2θ, intensity) restricted to [low, high].
    public func window(_ low: Double, _ high: Double) -> (x: [Double], y: [Double]) {
        var xs: [Double] = []
        var ys: [Double] = []
        for i in twoTheta.indices where twoTheta[i] >= low && twoTheta[i] <= high {
            xs.append(twoTheta[i]); ys.append(intensity[i])
        }
        return (xs, ys)
    }

    /// Linear background subtraction over [low, high]: a straight line between
    /// the mean of the left/right edges is subtracted and the result clipped ≥ 0.
    public func baselineSubtracted(_ low: Double, _ high: Double) throws -> (x: [Double], y: [Double]) {
        let (x, y) = window(low, high)
        if x.count < 4 { throw XRDError.tooFewPoints(x.count) }
        let nEdge = max(3, x.count / 20)
        let xl = mean(Array(x.prefix(nEdge))), yl = mean(Array(y.prefix(nEdge)))
        let xr = mean(Array(x.suffix(nEdge))), yr = mean(Array(y.suffix(nEdge)))
        let slope = xr != xl ? (yr - yl) / (xr - xl) : 0.0
        var yCorr = [Double](repeating: 0, count: x.count)
        for i in x.indices {
            let baseline = yl + slope * (x[i] - xl)
            yCorr[i] = max(y[i] - baseline, 0.0)
        }
        return (x, yCorr)
    }
}

@inlinable func mean(_ a: [Double]) -> Double {
    a.isEmpty ? 0 : a.reduce(0, +) / Double(a.count)
}
