import Foundation

/// Carbon / graphite mass-YIELD from weighed run masses — Swift port of
/// `research/yield_calc.py` (`compute_yield`). Reaction-by-reaction mass balance:
///   1. CaCO₃ → CaO + CO₂            2. C + CO₂ → 2CO (Boudouard, C lost)
///   3. CaO + S → CaS                4. remaining C → graphite
/// Yield = carbon surviving the furnace ÷ theoretical graphite. Needs only the
/// **post-furnace (pre-wash)** mass; post-acid is optional (drives the wash QC,
/// but cancels out of the yield). See the Python module for the full rationale.
public struct YieldResult: Sendable {
    public let actualC, actualS: Double
    public let boudouardCLoss, remainingC: Double
    public let caResidues, graphiteTheoretical, targetWash: Double
    public let measuredCAfterFurnace, massYield, carbonLostBeyondBoudouard: Double
    // reconciliation (needs pellet)
    public let unaccountedFurnaceLoss: Double?
    // wash QC (needs post-acid)
    public let trappedMetal: Double?
    public let washEfficiency: Double?          // 0–1
    public let overRemoved: Bool?
    // crystalline (needs a crystallinity index)
    public let crystallineFraction: Double?
    public let crystallineGraphiteYield: Double?    // 0–1
}

public enum YieldCalc {
    static let mC = 12.011, mFe = 55.845, mCaCO3 = 100.086
    static let mCaO = 56.077, mS = 32.06, mCaS = 72.138, mCO2 = 44.009, mCO = 28.01

    /// Placeholder feed compositions (carbon, sulfur mass fractions) by PC grade —
    /// literature-typical values. REPLACE with measured proximate/ultimate analysis
    /// (ideally fixed carbon) before quoting an absolute yield. Kept in sync with
    /// `DEFAULT_COMPOSITION` in research/yield_calc.py.
    public static func defaultComposition(grade: String?) -> (cWt: Double, sWt: Double) {
        switch (grade ?? "").uppercased() {
        case "CPC": return (0.97, 0.02)
        case "LSPC": return (0.95, 0.007)
        default: return (0.88, 0.045)       // GPC / unknown
        }
    }

    public static func compute(gpcMass: Double, cWt: Double, sWt: Double,
                               feMass: Double, caco3Mass: Double,
                               postFurnace: Double, postAcid: Double? = nil,
                               pellet: Double? = nil,
                               crystallineFraction: Double? = nil) -> YieldResult {
        // feed carbon & sulfur
        let actualC = gpcMass * cWt
        let actualS = gpcMass * sWt
        let molsC = actualC / mC
        let molsS = actualS / mS
        // 1. CaCO₃ → CaO + CO₂
        let molsCaCO3 = caco3Mass / mCaCO3
        let molsCaO = molsCaCO3, molsCO2 = molsCaCO3
        // 2. Boudouard: C + CO₂ → 2CO
        let molsCBoud = min(molsCO2, molsC)
        let remainingMolsC = molsC - molsCBoud
        let remainingC = remainingMolsC * mC
        let boudouardCLoss = molsCBoud * mC
        let wasteMolsCO = 2.0 * molsCBoud
        // 3. CaO + S → CaS
        let molsCaS = min(molsS, molsCaO)
        let remainingMolsCaO = molsCaO - molsCaS
        let caSMass = molsCaS * mCaS
        let remainingCaO = remainingMolsCaO * mCaO
        // 4/5. graphite, residues, target wash
        let graphiteTheoretical = remainingC
        let caResidues = caSMass + remainingCaO
        let targetWash = feMass + caResidues
        // yield basis — post-furnace only
        let measuredCAfterFurnace = postFurnace - feMass - caResidues
        let massYield = graphiteTheoretical != 0 ? measuredCAfterFurnace / graphiteTheoretical : 0
        let carbonLost = graphiteTheoretical - measuredCAfterFurnace
        // reconciliation
        let unaccounted: Double? = pellet.map { $0 - (wasteMolsCO * mCO) - postFurnace }
        // wash QC (needs post-acid)
        var trapped: Double?, washEff: Double?, over: Bool?
        if let pa = postAcid {
            let actualWash = postFurnace - pa
            let tm = targetWash - actualWash
            trapped = tm
            washEff = targetWash != 0 ? actualWash / targetWash : 0
            over = tm < 0
        }
        // crystalline-graphite yield
        var cgy: Double?
        if let cf = crystallineFraction { cgy = massYield * cf }

        return YieldResult(
            actualC: actualC, actualS: actualS,
            boudouardCLoss: boudouardCLoss, remainingC: remainingC,
            caResidues: caResidues, graphiteTheoretical: graphiteTheoretical, targetWash: targetWash,
            measuredCAfterFurnace: measuredCAfterFurnace, massYield: massYield,
            carbonLostBeyondBoudouard: carbonLost,
            unaccountedFurnaceLoss: unaccounted,
            trappedMetal: trapped, washEfficiency: washEff, overRemoved: over,
            crystallineFraction: crystallineFraction, crystallineGraphiteYield: cgy)
    }
}
