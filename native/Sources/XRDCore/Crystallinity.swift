import Foundation

/// Amorphous-aware (002) decomposition — Swift port of `research/amorphous.py`
/// (`decompose`, the robust 2-component primary path). It answers the question
/// DG% cannot: **how much** of the carbon is crystalline graphite, vs how *well
/// ordered* the crystalline part is.
///
/// The (002) region is fit with a linear background + ONE broad disordered band
/// (amorphous + turbostratic halo) + ONE sharp crystalline graphitic peak; the
/// crystallinity index is the integrated-area ratio
///
///     crystallineFraction = A_sharp / (A_sharp + A_broad)
///
/// computed *within a single pattern* (not against other samples). It is a
/// model-dependent **index, not a weight percent** — converting to absolute
/// crystalline-graphite wt% needs physical standards (see research/calibration.py).
/// See the Python module for sources (Warren, Franklin, Iwashita, Lu, Ruland).
public struct CrystallinityResult: Sendable {
    public let crystallineFraction: Double   // sharp ÷ (sharp + broad), 0–1
    public let disorderedFraction: Double    // broad ÷ (sharp + broad) = amorphous + turbostratic
    public let sharpArea: Double
    public let broadArea: Double
    public let graphiticCenter: Double
    public let graphiticFWHM: Double
    public let fitR2: Double
    public let windowLow: Double
    public let windowHigh: Double
}

public enum CrystallinityAnalyzer {
    // Decomposition window — mirrors AMORPHOUS_WINDOW in the Python module:
    // low enough to seat the broad disordered band, high enough to clear the
    // (002), short of the (100)/(101) + Fe lines and the low-angle air upturn.
    public static let windowLow = 16.0
    public static let windowHigh = 31.0

    public static func analyze(_ pattern: XRDPattern,
                               low: Double = windowLow, high: Double = windowHigh) throws -> CrystallinityResult {
        // Deterministic edge-baseline subtraction (shared with the Python module's
        // _edge_baseline_subtract) so the peak areas, not a free background, carry
        // the split — this is what keeps the two implementations in lockstep.
        let (x, y) = try pattern.baselineSubtracted(low, high)
        if x.count < 12 { throw XRDError.tooFewPoints(x.count) }
        let ph = y.max() ?? 0

        // p = [A_d, xc_d, w_d, mu_d,  A_g, xc_g, w_g, mu_g]
        //     broad disordered PV  +  sharp graphitic PV  (no free background)
        let model: (Double, [Double]) -> Double = { xx, p in
            pseudoVoigt(xx, p[0], p[1], p[2], p[3]) + pseudoVoigt(xx, p[4], p[5], p[6], p[7])
        }
        let p0 = [ph * 0.4 * 3.0, 25.5, 3.0, 0.5, ph * 0.8 * 0.3, 26.5, 0.3, 0.6]
        let lo = [0, 22, 1.5, 0, 0, 26.2, 0.05, 0]
        let hi = [Double.infinity, 26.3, 12, 1, Double.infinity, 26.9, 0.8, 1]
        let p = levenbergMarquardt(x: x, y: y, model: model, p0: p0, lower: lo, upper: hi).params

        let broadA = p[0], sharpA = p[4]
        let total = (broadA + sharpA) == 0 ? 1.0 : (broadA + sharpA)

        let ymean = mean(y)
        var ssRes = 0.0, ssTot = 0.0
        for i in x.indices {
            let f = model(x[i], p)
            ssRes += (y[i] - f) * (y[i] - f)
            ssTot += (y[i] - ymean) * (y[i] - ymean)
        }
        let r2 = ssTot > 0 ? 1.0 - ssRes / ssTot : 0.0

        return CrystallinityResult(
            crystallineFraction: sharpA / total,
            disorderedFraction: broadA / total,
            sharpArea: sharpA, broadArea: broadA,
            graphiticCenter: p[5], graphiticFWHM: p[6],
            fitR2: r2, windowLow: low, windowHigh: high)
    }
}
