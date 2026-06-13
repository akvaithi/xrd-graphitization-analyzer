import Foundation

/// Numeric features describing the (002) shape, used to drive the AI
/// deconvolution suggestion. Keys match the validated Python harness exactly.
public struct DeconvolutionFeatures: Codable, Sendable {
    public let singlePeakR2: Double
    public let twoPeakR2: Double
    public let dR2: Double
    public let singlePeakCenter: Double
    public let singlePeakFWHM: Double
    public let lowAngleResidual2theta: Double
    public let lowAngleResidualFraction: Double
    public let automaticTwoPeakTurbostratic2theta: Double?
    public let snr: Double

    enum CodingKeys: String, CodingKey {
        case singlePeakR2 = "single_peak_R2"
        case twoPeakR2 = "two_peak_R2"
        case dR2
        case singlePeakCenter = "single_peak_center"
        case singlePeakFWHM = "single_peak_FWHM"
        case lowAngleResidual2theta = "low_angle_residual_2theta"
        case lowAngleResidualFraction = "low_angle_residual_fraction"
        case automaticTwoPeakTurbostratic2theta = "automatic_two_peak_turbostratic_2theta"
        case snr = "SNR"
    }
}

private func roundTo(_ v: Double, _ places: Int) -> Double {
    let f = pow(10.0, Double(places))
    return (v * f).rounded() / f
}

/// Compute the deconvolution features by reusing the deterministic engine:
/// a single-peak fit (R², centre, residual shoulder) and an automatic
/// two-peak fit (turbostratic position, ΔR²).
public func computeFeatures(_ pattern: XRDPattern) throws -> DeconvolutionFeatures {
    let analyzer = GraphitizationAnalyzer(pattern)

    var oneOpt = FitOptions(); oneOpt.peakCount = 1
    let r1 = try analyzer.run(oneOpt)

    var twoOpt = FitOptions(); twoOpt.peakCount = 2
    let r2 = try? analyzer.run(twoOpt)

    // Residual of the single-peak fit; shoulder = largest positive low-angle residual.
    let x = r1.pointsX, y = r1.pointsY
    let g = r1.graphitic
    var maxResid = -Double.greatestFiniteMagnitude
    var shoulderX = g.xc
    var peakH = 0.0
    for i in x.indices {
        let f = r1.y0 + pseudoVoigt(x[i], g.A, g.xc, g.w, g.mu)
        let resid = y[i] - f
        if x[i] < g.xc && resid > maxResid { maxResid = resid; shoulderX = x[i] }
        peakH = max(peakH, y[i])
    }
    let baseline = mean(y)
    let frac = peakH > baseline ? maxResid / (peakH - baseline) : 0.0

    // SNR from the window edges.
    let edge = max(3, min(8, x.count / 4))
    var edges = Array(y.prefix(edge)); edges.append(contentsOf: y.suffix(edge))
    let em = mean(edges)
    let sd = sqrt(edges.map { ($0 - em) * ($0 - em) }.reduce(0, +) / Double(max(edges.count, 1)))
    let snr = (peakH - em) / max(sd, 1e-9)

    return DeconvolutionFeatures(
        singlePeakR2: roundTo(r1.fitR2, 5),
        twoPeakR2: roundTo(r2?.fitR2 ?? r1.fitR2, 5),
        dR2: roundTo((r2?.fitR2 ?? r1.fitR2) - r1.fitR2, 5),
        singlePeakCenter: roundTo(g.xc, 3),
        singlePeakFWHM: roundTo(g.w, 3),
        lowAngleResidual2theta: roundTo(shoulderX, 3),
        lowAngleResidualFraction: roundTo(frac, 4),
        automaticTwoPeakTurbostratic2theta: (r2?.turbostratic).map { roundTo($0.xc, 3) },
        snr: roundTo(snr, 1))
}
