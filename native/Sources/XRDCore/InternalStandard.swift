import Foundation

/// Internal-standard 2θ calibration. Residual catalyst/carbonate phases (Fe₃C,
/// α-Fe, CaO) have lattice-fixed d-spacings, so their reflections are a built-in
/// 2θ reference. We index a phase, match its lines to observed peaks, and if they
/// agree on one offset (tight spread) above the lattice-uncertainty floor, use it
/// to correct specimen-displacement / zero error. Port of
/// `xrd_analyzer.calibrate_internal_standard`.
public struct InternalStandard: Sendable {
    public struct Match: Sendable { public let line, observed, delta: Double }

    public let phase: String?
    public let phaseLabel: String?
    public let offset: Double           // observed − reference (median over lines)
    public let spread: Double?
    public let nLines: Int
    public let matches: [Match]
    public let reliable: Bool           // lines agree (tight spread)
    public let significant: Bool        // reliable AND above the ~0.05° noise floor

    // Lattice constants are room-temperature reference values (Å). Sources:
    //   Fe3C     orthorhombic Pnma  — ICDD PDF 00-035-0772, after Fasiska & Jeffrey,
    //            Acta Cryst. 19 (1965) 463–471 (a,b,c here are the Pnma cell).
    //   alpha-Fe cubic Im-3m (bcc)  — ICDD PDF 00-006-0696, a=2.8664 Å at 20 °C.
    //   CaO      cubic Fm-3m (lime) — ICDD PDF 00-037-1497, a=4.8105 Å.
    private static let phases: [(key: String, label: String, system: String, abc: (Double, Double, Double))] = [
        ("Fe3C", "cementite Fe₃C", "ortho", (5.0896, 6.7443, 4.5248)),
        ("alpha-Fe", "metallic α-Fe", "cubic", (2.8664, 0, 0)),
        ("CaO", "lime CaO", "cubic", (4.8105, 0, 0)),
    ]
    private static let graphiteLines = [26.55, 42.4, 44.6, 50.6, 54.7, 77.5, 83.6]

    private static func lines(_ system: String, _ abc: (Double, Double, Double),
                              _ lam: Double, _ lo: Double = 32, _ hi: Double = 90) -> [Double] {
        var out: [Double] = []
        for h in 0..<4 { for k in 0..<4 { for l in 0..<4 {
            if h == 0 && k == 0 && l == 0 { continue }
            let inv: Double
            if system == "cubic" { let a = abc.0; inv = Double(h*h + k*k + l*l) / (a*a) }
            else { let (a, b, c) = abc; inv = Double(h*h)/(a*a) + Double(k*k)/(b*b) + Double(l*l)/(c*c) }
            let d = 1.0 / inv.squareRoot(); let s = lam / (2.0 * d)
            if s > 1 { continue }
            let t = 2.0 * asin(s) * 180.0 / .pi
            if t >= lo && t <= hi { out.append(t) }
        }}}
        out.sort()
        var dd: [Double] = []
        for t in out where dd.isEmpty || t - dd.last! > 0.15 { dd.append(t) }
        return dd
    }

    // Half-width (deg) of the intensity-weighted centroid window — matches Python.
    private static let centroidHalfDeg = 0.12

    private static func localPeaks(_ p: XRDPattern, _ lo: Double, _ hi: Double,
                                   _ promFrac: Double = 0.02) -> [(Double, Double)] {
        let order = p.twoTheta.indices.sorted { p.twoTheta[$0] < p.twoTheta[$1] }
        let x = order.map { p.twoTheta[$0] }, y = order.map { p.intensity[$0] }
        let n = x.count, half = 40
        if n < 80 { return [] }
        var rollmin = [Double](repeating: 0, count: n)
        for i in 0..<n { let a = max(0, i - half), b = min(n - 1, i + half); rollmin[i] = y[a...b].min() ?? y[i] }
        let ymax = y.max() ?? 1
        var out: [(Double, Double)] = []
        for i in 6..<(n - 6) {
            guard x[i] >= lo && x[i] <= hi else { continue }
            let prom = y[i] - rollmin[i]
            if y[i] == y[(i-6)...(i+6)].max()! && prom > promFrac * ymax {
                var num = 0.0, den = 0.0
                for j in 0..<n where x[j] >= x[i] - centroidHalfDeg && x[j] <= x[i] + centroidHalfDeg {
                    let w = max(y[j] - rollmin[j], 0.0); num += x[j] * w; den += w
                }
                let cen = den > 0 ? num / den : x[i]
                if out.isEmpty || cen - out.last!.0 > 0.4 { out.append((cen, prom)) }
            }
        }
        return out
    }

    public static func calibrate(_ pattern: XRDPattern, phase: String = "auto",
                                 wavelength: Double = 1.54187, tol: Double = 0.40,
                                 minLines: Int = 3, maxSpread: Double = 0.06,
                                 maxOffset: Double = 0.8, minSignificant: Double = 0.05) -> InternalStandard {
        let peaks = localPeaks(pattern, 32, 90)
        let cands = phases.filter { phase == "auto" || $0.key == phase }
        var best: InternalStandard?
        for ph in cands {
            var matches: [Match] = []
            for L in lines(ph.system, ph.abc, wavelength) {
                if graphiteLines.contains(where: { abs(L - $0) < 0.5 }) { continue }
                if let pk = peaks.filter({ abs($0.0 - L) < tol }).min(by: { abs($0.0 - L) < abs($1.0 - L) }) {
                    matches.append(Match(line: L, observed: pk.0, delta: pk.0 - L))
                }
            }
            if matches.count < minLines { continue }
            var off = median(matches.map { $0.delta })
            let keep = matches.filter { abs($0.delta - off) <= 0.10 }
            if keep.count < minLines { continue }
            let dd = keep.map { $0.delta }; off = median(dd); let spread = stddev(dd)
            let reliable = spread <= maxSpread && abs(off) <= maxOffset
            let significant = reliable && abs(off) >= minSignificant
            let res = InternalStandard(phase: ph.key, phaseLabel: ph.label, offset: off,
                                       spread: spread, nLines: keep.count, matches: keep,
                                       reliable: reliable, significant: significant)
            let rank = (significant ? 1 : 0, keep.count, -spread)
            let bestRank = best.map { ($0.significant ? 1 : 0, $0.nLines, -($0.spread ?? 9)) } ?? (-1, -1, -9.0)
            if rank > bestRank { best = res }
        }
        return best ?? InternalStandard(phase: nil, phaseLabel: nil, offset: 0, spread: nil,
                                        nLines: 0, matches: [], reliable: false, significant: false)
    }

    // small numeric helpers (file-private to avoid clashes)
    private static func median(_ a: [Double]) -> Double {
        guard !a.isEmpty else { return 0 }
        let s = a.sorted(); let m = s.count / 2
        return s.count % 2 == 0 ? (s[m-1] + s[m]) / 2 : s[m]
    }
    private static func stddev(_ a: [Double]) -> Double {
        guard a.count > 1 else { return 0 }
        let mu = a.reduce(0, +) / Double(a.count)
        return (a.reduce(0) { $0 + ($1 - mu) * ($1 - mu) } / Double(a.count)).squareRoot()
    }
}
