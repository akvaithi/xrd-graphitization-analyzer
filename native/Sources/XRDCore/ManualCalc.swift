import Foundation

/// Manual peak entry → DG (the NETL "prompt excel sheet"). Port of
/// `xrd_analyzer.dg_from_peaks`.
public struct ManualPeak: Sendable {
    public var xc: Double      // 2θ centre, degrees
    public var area: Double    // OriginLab PsdVoigt1 A
    public init(xc: Double, area: Double) { self.xc = xc; self.area = area }
}

public struct ManualResult: Sendable {
    public let nPeaks: Int
    public let wavelength: Double
    public let graphiticXc: Double
    public let graphiticArea: Double
    public let graphiticD: Double
    public let turbostraticXc: Double?
    public let turbostraticArea: Double?
    public let turbostraticD: Double?
    public let areaFractionGraphitic: Double?
    public let areaFractionTurbostratic: Double?
    public let dPrime: Double
    public let dgPercent: Double
}

public enum ManualError: Error, CustomStringConvertible {
    case count, range(Double), area
    public var description: String {
        switch self {
        case .count: return "Enter 1 or 2 peaks (centre + area each)."
        case .range(let v): return "Peak centre \(v)° is out of range."
        case .area: return "Peak area must be positive."
        }
    }
}

public func dgFromPeaks(_ peaks: [ManualPeak], wavelength: Double = DEFAULT_WAVELENGTH) throws -> ManualResult {
    guard (1...2).contains(peaks.count) else { throw ManualError.count }
    for p in peaks {
        guard p.xc > 1.0 && p.xc < 179.0 else { throw ManualError.range(p.xc) }
        guard p.area > 0 else { throw ManualError.area }
    }
    func bragg(_ tt: Double) -> Double { wavelength / (2.0 * sin((tt / 2.0) * .pi / 180.0)) }
    func mm(_ d: Double) -> Double { (D_TURBOSTRATIC - d) / (D_TURBOSTRATIC - D_GRAPHITE) * 100.0 }

    if peaks.count == 1 {
        let p = peaks[0]; let d = bragg(p.xc)
        return ManualResult(nPeaks: 1, wavelength: wavelength,
                            graphiticXc: p.xc, graphiticArea: p.area, graphiticD: d,
                            turbostraticXc: nil, turbostraticArea: nil, turbostraticD: nil,
                            areaFractionGraphitic: nil, areaFractionTurbostratic: nil,
                            dPrime: d, dgPercent: mm(d))
    }
    // graphitic = higher 2θ
    let sorted = peaks.sorted { $0.xc > $1.xc }
    let g = sorted[0], t = sorted[1]
    let dg = bragg(g.xc), dt = bragg(t.xc)
    let total = g.area + t.area
    let xg = g.area / total, xt = t.area / total
    let dPrime = xg * dg + xt * dt
    return ManualResult(nPeaks: 2, wavelength: wavelength,
                        graphiticXc: g.xc, graphiticArea: g.area, graphiticD: dg,
                        turbostraticXc: t.xc, turbostraticArea: t.area, turbostraticD: dt,
                        areaFractionGraphitic: xg, areaFractionTurbostratic: xt,
                        dPrime: dPrime, dgPercent: mm(dPrime))
}
