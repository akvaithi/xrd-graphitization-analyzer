import Foundation

/// Full-pattern data-quality scan: flag catalyst/carbonate residue peaks.
/// Faithful port of `xrd_analyzer.scan_impurities`. DG is computed only from the
/// (002) window, so this never changes the result — it indicates whether acid
/// washing was complete. Severity is relative to the (002) peak height.
public struct ImpurityScan: Sendable {
    public struct Hit: Sendable, Identifiable {
        public let id = UUID()
        public let twoTheta: Double
        public let phase: String
        public let meaning: String
        public let relPct: Double          // % of the (002) height
        public var level: String {         // trace / minor / significant
            relPct >= 10 ? "significant" : (relPct >= 2 ? "minor" : "trace")
        }
    }

    public let verdict: String
    public let hits: [Hit]
    public let clean: Bool
    public let worstPct: Double

    // Expected graphite reflections (NOT impurities); (002) handled by the fit.
    private static let graphite: [Double: String] = [
        42.4: "graphite (100)", 44.6: "graphite (101)", 50.6: "graphite (102)",
        54.7: "graphite (004)", 77.5: "graphite (110)", 83.6: "graphite (112)",
    ]
    // 2θ (Cu Kα) → (phase, meaning) for catalyst/carbonate residue.
    private static let impurity: [(Double, String, String)] = [
        (29.4, "calcite CaCO3 (104)", "carbonate residue"),
        (30.9, "calcite/dolomite", "carbonate residue"),
        (36.0, "iron oxide (Fe3O4/Fe2O3)", "oxidised catalyst"),
        (37.4, "CaO (200)", "lime from CaCO3 decomposition"),
        (43.8, "Fe3C cementite", "unreacted iron carbide"),
        (45.0, "Fe3C / Fe", "iron carbide / metallic iron"),
        (49.1, "Fe3C cementite", "unreacted iron carbide"),
        (53.9, "CaO (220)", "lime from CaCO3 decomposition"),
        (64.9, "metallic Fe (200)", "unreacted iron catalyst"),
    ]

    public static func scan(_ pattern: XRDPattern, tol: Double = 0.45,
                            traceFrac: Double = 0.012) -> ImpurityScan {
        let n = pattern.twoTheta.count
        if n < 50 {
            return ImpurityScan(verdict: "insufficient range", hits: [], clean: true, worstPct: 0)
        }
        // sort by 2θ
        let order = pattern.twoTheta.indices.sorted { pattern.twoTheta[$0] < pattern.twoTheta[$1] }
        let x = order.map { pattern.twoTheta[$0] }
        let y = order.map { pattern.intensity[$0] }

        // (002) height for relative severity
        var i002 = y.max() ?? 1
        var base002 = y.min() ?? 0
        let winIdx = x.indices.filter { x[$0] >= 24.0 && x[$0] <= 28.5 }
        if winIdx.count > 16 {
            let wy = winIdx.map { y[$0] }
            i002 = wy.max() ?? i002
            let edges = Array(wy.prefix(8)) + Array(wy.suffix(8))
            base002 = median(edges)
        }
        let h002 = max(i002 - base002, 1e-9)

        // local-prominence detection vs a wide rolling minimum
        let half = 30
        var rollmin = [Double](repeating: 0, count: n)
        for i in 0..<n {
            let lo = max(0, i - half), hi = min(n - 1, i + half)
            rollmin[i] = y[lo...hi].min() ?? y[i]
        }
        let edge = Array(y.prefix(20)) + Array(y.suffix(20))
        let noise = stddev(edge)
        let thresh = max(5.0 * noise, traceFrac * h002, 4.0)

        // collect peaks (merge within 0.7°)
        var peaks: [(Double, Double)] = []
        let w = 12
        for i in w..<(n - w) {
            let seg = y[(i - w)...(i + w)]
            let prom = y[i] - rollmin[i]
            if y[i] == seg.max()! && prom >= thresh {
                if peaks.isEmpty || x[i] - peaks[peaks.count - 1].0 > 0.7 {
                    peaks.append((x[i], prom))
                } else if prom > peaks[peaks.count - 1].1 {
                    peaks[peaks.count - 1] = (x[i], prom)
                }
            }
        }

        var hits: [Hit] = []
        for (px, prom) in peaks {
            if abs(px - 26.5) < 1.0 { continue }              // the (002) analyte
            let g = graphite.keys.min { abs($0 - px) < abs($1 - px) }!
            let m = impurity.min { abs($0.0 - px) < abs($1.0 - px) }!
            let dg = abs(g - px), dm = abs(m.0 - px)
            if dg < tol && dg <= dm { continue }              // expected graphite line
            if dm < tol {
                let rel = (100.0 * prom / h002 * 10).rounded() / 10
                hits.append(Hit(twoTheta: (px * 100).rounded() / 100,
                                phase: m.1, meaning: m.2, relPct: rel))
            }
        }
        hits.sort { $0.relPct > $1.relPct }
        let worst = hits.first?.relPct ?? 0

        let verdict: String
        if hits.isEmpty { verdict = "Clean — graphite reflections only." }
        else if worst >= 10 { verdict = "Residual catalyst/carbonate detected — washing likely incomplete." }
        else if worst >= 2 { verdict = "Minor residual catalyst/carbonate present." }
        else { verdict = "Trace impurities only — essentially clean." }

        return ImpurityScan(verdict: verdict, hits: hits, clean: worst < 2, worstPct: worst)
    }

    private static func median(_ a: [Double]) -> Double {
        guard !a.isEmpty else { return 0 }
        let s = a.sorted(); let m = s.count / 2
        return s.count % 2 == 0 ? (s[m - 1] + s[m]) / 2 : s[m]
    }
    private static func stddev(_ a: [Double]) -> Double {
        guard a.count > 1 else { return 0 }
        let mean = a.reduce(0, +) / Double(a.count)
        let v = a.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(a.count)
        return v.squareRoot()
    }
}
