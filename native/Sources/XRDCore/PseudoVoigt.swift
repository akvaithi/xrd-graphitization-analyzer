import Foundation

/// OriginLab **PsdVoigt1** profile (area-normalised, no y0 term) — exact port of
/// `xrd_analyzer.pseudo_voigt`.
///
///     A·( μ·(2/π)·(w / (4·dx² + w²))
///         + (1−μ)·(√(4·ln2)/(√π·w))·exp(−(4·ln2/w²)·dx²) )
///
/// `A` = integrated area, `w` = FWHM, `mu` = Lorentzian fraction, `xc` = centre.
@inlinable
public func pseudoVoigt(_ x: Double, _ A: Double, _ xc: Double, _ w: Double, _ mu: Double) -> Double {
    let ln2 = 0.6931471805599453
    let dx = x - xc
    let lorentzian = (2.0 / Double.pi) * (w / (4.0 * dx * dx + w * w))
    let gaussian = (sqrt(4.0 * ln2) / (sqrt(Double.pi) * w))
        * exp(-(4.0 * ln2 / (w * w)) * dx * dx)
    return A * (mu * lorentzian + (1.0 - mu) * gaussian)
}

/// Standard pipeline model: graphitic Pseudo-Voigt (free μ) + a pure-Lorentzian
/// turbostratic peak (μ = 1). `p = [Ag, xcg, wg, mug, At, xct, wt]`.
@inlinable
public func standardModel(_ x: Double, _ p: [Double]) -> Double {
    pseudoVoigt(x, p[0], p[1], p[2], p[3]) + pseudoVoigt(x, p[4], p[5], p[6], 1.0)
}
