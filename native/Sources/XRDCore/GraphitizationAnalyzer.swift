import Foundation

// Physical constants — match the NETL method exactly.
public let DEFAULT_WAVELENGTH = 1.54187      // Å, Cu Kα (weighted)
let D_GRAPHITE = 3.354
let D_TURBOSTRATIC = 3.440
let SCHERRER_K = 0.89
let GRAPH_XC = (lo: 26.3, hi: 26.8)
// Turbostratic auto bound: NETL's worked examples / the postdoc place it at
// 26.0–26.4° (NOT the deck's "25.1–25.3" text). Keep it below the graphitic
// peak so an unconstrained fit can't slide the shoulder up and inflate DG.
let TURBO_XC = (lo: 25.1, hi: 26.45)

public struct Peak: Sendable {
    public let A, xc, w, mu, dSpacing: Double
}

/// User-controllable deconvolution choices — the NETL procedure is explicitly
/// human-in-the-loop (1-vs-2 peaks, turbostratic placement, optional background).
public struct FitOptions: Sendable, Equatable {
    public var peakCount: Int = 2                 // 1 or 2
    public var subtractBackground: Bool = false   // sloped baseline (NETL step 4)
    public var windowLow: Double = 24.0
    public var windowHigh: Double = 30.0
    public var graphiticCenter: Double? = nil     // optional seed for the (002) centre
    public var turbostraticCenter: Double? = nil  // human-set shoulder position
    public var lockTurbostratic: Bool = false     // fix turbostratic xc to the value above
    public init() {}
}

public struct DGResult: Sendable {
    public let methodName: String
    public let wavelength: Double
    public let y0: Double
    public let peakCount: Int
    public let backgroundSubtracted: Bool
    public let graphitic: Peak
    public let turbostratic: Peak?               // nil for a single-peak fit
    public let areaFractionGraphitic: Double
    public let areaFractionTurbostratic: Double
    public let dPrimeWeighted: Double
    public let crystalliteLc: Double
    public let fitR2: Double
    public let dgPercent: Double
    public let pointsX: [Double]                 // fitted window (raw, or bg-subtracted)
    public let pointsY: [Double]
}

/// DG% via the NETL pipeline: PsdVoigt1 (with a free shared `y0`), area-weighted
/// d′, Maire–Mering. One or two peaks; the turbostratic centre can be supplied.
public struct GraphitizationAnalyzer {
    public let pattern: XRDPattern
    public let wavelength: Double

    public init(_ pattern: XRDPattern, wavelength: Double = DEFAULT_WAVELENGTH) {
        self.pattern = pattern
        self.wavelength = wavelength
    }

    func bragg(_ twoThetaDeg: Double) -> Double {
        wavelength / (2.0 * sin((twoThetaDeg / 2.0) * .pi / 180.0))
    }
    func maireMering(_ d: Double) -> Double {
        (D_TURBOSTRATIC - d) / (D_TURBOSTRATIC - D_GRAPHITE) * 100.0
    }
    func scherrerLc(_ xc: Double, _ w: Double) -> Double {
        SCHERRER_K * wavelength / ((w * .pi / 180.0) * cos((xc / 2.0) * .pi / 180.0))
    }

    public func run(_ opt: FitOptions = FitOptions()) throws -> DGResult {
        var (x, y) = pattern.window(opt.windowLow, opt.windowHigh)
        if x.count < 10 { throw XRDError.tooFewPoints(x.count) }
        if opt.subtractBackground { y = linearBaselineSubtract(x, y) }

        let ph = y.max() ?? 0
        let ymin = y.min() ?? 0
        let gSeed = opt.graphiticCenter ?? 26.55

        let graphitic: Peak, turbostratic: Peak?
        let y0: Double, params: [Double]

        if opt.peakCount <= 1 {
            // y0 + single Pseudo-Voigt
            let model: (Double, [Double]) -> Double = { xx, p in p[0] + pseudoVoigt(xx, p[1], p[2], p[3], p[4]) }
            let p0 = [ymin, ph * 0.9, gSeed, 0.2, 0.5]
            let lo = [-Double.infinity, 0, GRAPH_XC.lo, 0.02, 0.0]
            let hi = [Double.infinity, Double.infinity, GRAPH_XC.hi, 3.0, 1.0]
            let p = levenbergMarquardt(x: x, y: y, model: model, p0: p0, lower: lo, upper: hi).params
            y0 = p[0]
            graphitic = Peak(A: p[1], xc: p[2], w: p[3], mu: p[4], dSpacing: bragg(p[2]))
            turbostratic = nil
            params = p
        } else if opt.lockTurbostratic, let T = opt.turbostraticCenter {
            // y0 + graphitic (free μ) + turbostratic at fixed centre T (μ = 1)
            let model: (Double, [Double]) -> Double = { xx, p in
                p[0] + pseudoVoigt(xx, p[1], p[2], p[3], p[4]) + pseudoVoigt(xx, p[5], T, p[6], 1.0)
            }
            let p0 = [ymin, ph * 0.6, gSeed, 0.15, 0.5, ph * 0.3, 0.5]
            let lo = [-Double.infinity, 0, GRAPH_XC.lo, 0.02, 0.0, 0, 0.05]
            let hi = [Double.infinity, Double.infinity, GRAPH_XC.hi, 3.0, 1.0, Double.infinity, 3.0]
            let p = levenbergMarquardt(x: x, y: y, model: model, p0: p0, lower: lo, upper: hi).params
            y0 = p[0]
            graphitic = Peak(A: p[1], xc: p[2], w: p[3], mu: p[4], dSpacing: bragg(p[2]))
            turbostratic = Peak(A: p[5], xc: T, w: p[6], mu: 1.0, dSpacing: bragg(T))
            params = p
        } else {
            // y0 + graphitic (free μ) + turbostratic (free centre, μ = 1)
            let tSeed = opt.turbostraticCenter ?? 26.2
            let model: (Double, [Double]) -> Double = { xx, p in
                p[0] + pseudoVoigt(xx, p[1], p[2], p[3], p[4]) + pseudoVoigt(xx, p[5], p[6], p[7], 1.0)
            }
            let p0 = [ymin, ph * 0.6, gSeed, 0.15, 0.5, ph * 0.3, tSeed, 0.6]
            let lo = [-Double.infinity, 0, GRAPH_XC.lo, 0.02, 0.0, 0, TURBO_XC.lo, 0.05]
            let hi = [Double.infinity, Double.infinity, GRAPH_XC.hi, 3.0, 1.0, Double.infinity, TURBO_XC.hi, 3.0]
            let p = levenbergMarquardt(x: x, y: y, model: model, p0: p0, lower: lo, upper: hi).params
            y0 = p[0]
            // graphitic = higher-2θ (sharp) peak, turbostratic = lower-2θ
            let pkA = Peak(A: p[1], xc: p[2], w: p[3], mu: p[4], dSpacing: bragg(p[2]))
            let pkB = Peak(A: p[5], xc: p[6], w: p[7], mu: 1.0, dSpacing: bragg(p[6]))
            if pkA.xc >= pkB.xc { graphitic = pkA; turbostratic = pkB }
            else { graphitic = pkB; turbostratic = pkA }
            params = p
        }

        // R² over the fitted window
        let ymean = mean(y)
        var ssRes = 0.0, ssTot = 0.0
        let modelEval = makeEvaluator(y0: y0, graphitic: graphitic, turbostratic: turbostratic)
        for i in x.indices {
            let f = modelEval(x[i])
            ssRes += (y[i] - f) * (y[i] - f)
            ssTot += (y[i] - ymean) * (y[i] - ymean)
        }
        let r2 = ssTot > 0 ? 1.0 - ssRes / ssTot : 0.0

        // DG% — area-weighted d′ (or single-peak d)
        let dg = graphitic.dSpacing
        let Xg: Double, Xt: Double, dPrime: Double
        if let t = turbostratic {
            let total = graphitic.A + t.A
            Xg = total > 0 ? graphitic.A / total : 1
            Xt = 1 - Xg
            dPrime = Xg * dg + Xt * t.dSpacing
        } else {
            Xg = 1; Xt = 0; dPrime = dg
        }
        let DG = maireMering(dPrime)
        let Lc = scherrerLc(graphitic.xc, graphitic.w)

        let name = opt.peakCount <= 1
            ? "NETL single-peak (PsdVoigt1 + y0)"
            : "NETL two-peak (graphitic + Lorentzian turbostratic + y0)"
        _ = params
        return DGResult(
            methodName: name, wavelength: wavelength, y0: y0,
            peakCount: turbostratic == nil ? 1 : 2,
            backgroundSubtracted: opt.subtractBackground,
            graphitic: graphitic, turbostratic: turbostratic,
            areaFractionGraphitic: Xg, areaFractionTurbostratic: Xt,
            dPrimeWeighted: dPrime, crystalliteLc: Lc, fitR2: r2, dgPercent: DG,
            pointsX: x, pointsY: y)
    }

    private func makeEvaluator(y0: Double, graphitic: Peak, turbostratic: Peak?)
        -> (Double) -> Double {
        return { xx in
            var v = y0 + pseudoVoigt(xx, graphitic.A, graphitic.xc, graphitic.w, graphitic.mu)
            if let t = turbostratic { v += pseudoVoigt(xx, t.A, t.xc, t.w, t.mu) }
            return v
        }
    }

    /// Sloped linear baseline between the window edges, subtracted and clipped ≥ 0.
    private func linearBaselineSubtract(_ x: [Double], _ y: [Double]) -> [Double] {
        let nEdge = max(3, x.count / 20)
        let xl = mean(Array(x.prefix(nEdge))), yl = mean(Array(y.prefix(nEdge)))
        let xr = mean(Array(x.suffix(nEdge))), yr = mean(Array(y.suffix(nEdge)))
        let slope = xr != xl ? (yr - yl) / (xr - xl) : 0.0
        return x.indices.map { max(y[$0] - (yl + slope * (x[$0] - xl)), 0.0) }
    }
}
