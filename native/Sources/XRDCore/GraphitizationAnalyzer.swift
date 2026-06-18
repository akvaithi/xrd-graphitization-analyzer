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
    // Right edge trimmed to 28.5° so residual calcite CaCO3 (104) (~29.4-29.7°)
    // can't intrude; that region is pure (002) baseline, so DG is unchanged.
    public var windowHigh: Double = 28.5
    public var graphiticCenter: Double? = nil     // optional seed for the (002) centre
    public var turbostraticCenter: Double? = nil  // human-set shoulder position
    public var lockTurbostratic: Bool = false     // fix turbostratic xc to the value above
    // Specimen-displacement calibration: shift the whole pattern in 2θ before
    // fitting. Set anchor002 to put the measured (002) at that angle (e.g. 26.54),
    // or twoThetaOffset for a raw constant shift.
    public var twoThetaOffset: Double = 0.0
    public var anchor002: Double? = nil
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
    public let dgSigma: Double?                  // statistical (fit-covariance) 1σ; nil if ill-posed
    public let twoThetaOffset: Double            // applied specimen-displacement shift
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

    /// Constant 2θ offset that puts the measured (002) at `target` (displacement fix).
    public func anchorOffset(_ target: Double, _ opt: FitOptions = FitOptions()) -> Double {
        var o = FitOptions()
        o.peakCount = 1; o.windowLow = opt.windowLow; o.windowHigh = opt.windowHigh
        guard let r = try? run(o) else { return 0 }
        return target - r.graphitic.xc
    }

    public func run(_ opt: FitOptions = FitOptions()) throws -> DGResult {
        // Specimen-displacement calibration: shift the whole pattern in 2θ first.
        let offset = opt.anchor002.map { anchorOffset($0, opt) } ?? opt.twoThetaOffset
        let src = offset == 0 ? pattern
            : XRDPattern(twoTheta: pattern.twoTheta.map { $0 + offset }, intensity: pattern.intensity)
        var (x, y) = src.window(opt.windowLow, opt.windowHigh)
        if x.count < 10 { throw XRDError.tooFewPoints(x.count) }
        if opt.subtractBackground { y = linearBaselineSubtract(x, y) }

        let ph = y.max() ?? 0
        let ymin = y.min() ?? 0
        let gSeed = opt.graphiticCenter ?? 26.55

        let graphitic: Peak, turbostratic: Peak?
        let y0: Double, params: [Double]
        let paramModel: (Double, [Double]) -> Double      // raw model(x, params) for σ
        let dgFromParams: ([Double]) -> Double             // DG(params) for σ propagation

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
            params = p; paramModel = model
            dgFromParams = { [self] q in maireMering(bragg(q[2])) }
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
            params = p; paramModel = model
            dgFromParams = { [self] q in dgTwo(q[1], q[2], q[5], T) }
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
            params = p; paramModel = model
            dgFromParams = { [self] q in dgTwo(q[1], q[2], q[5], q[6]) }
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
        let sigma = dgSigma(paramModel: paramModel, params: params, x: x, y: y,
                            ssRes: ssRes, dgFromParams: dgFromParams)

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
            dgSigma: sigma, twoThetaOffset: offset, pointsX: x, pointsY: y)
    }

    /// graphitic = higher-2θ; area-weighted d′ → Maire-Mering DG (for σ propagation).
    private func dgTwo(_ Ag: Double, _ xcg: Double, _ At: Double, _ xct: Double) -> Double {
        var (ag, g, at, t) = (Ag, xcg, At, xct)
        if t > g { swap(&g, &t); swap(&ag, &at) }
        let total = ag + at
        let Xg = total > 0 ? ag / total : 1.0
        return maireMering(Xg * bragg(g) + (1 - Xg) * bragg(t))
    }

    /// Statistical DG 1σ: covariance = s²·(JᵀJ)⁻¹ (finite-difference J), then
    /// linear propagation σ_DG = √(gᵀ·cov·g). Returns nil if ill-posed.
    private func dgSigma(paramModel: (Double, [Double]) -> Double, params: [Double],
                         x: [Double], y: [Double], ssRes: Double,
                         dgFromParams: ([Double]) -> Double) -> Double? {
        let m = x.count, n = params.count
        guard m > n else { return nil }
        // finite-difference Jacobian J[i][j] = ∂model(x_i)/∂p_j
        var step = [Double](repeating: 0, count: n)
        for j in 0..<n { step[j] = max(1e-6, abs(params[j]) * 1e-4) }
        var J = [[Double]](repeating: [Double](repeating: 0, count: n), count: m)
        for j in 0..<n {
            var pp = params, pm = params
            pp[j] += step[j]; pm[j] -= step[j]
            for i in 0..<m { J[i][j] = (paramModel(x[i], pp) - paramModel(x[i], pm)) / (2 * step[j]) }
        }
        // JᵀJ
        var JtJ = [[Double]](repeating: [Double](repeating: 0, count: n), count: n)
        for a in 0..<n { for b in 0..<n {
            var s = 0.0; for i in 0..<m { s += J[i][a] * J[i][b] }; JtJ[a][b] = s
        }}
        // invert column-by-column via solveLinear; scale by residual variance
        let s2 = ssRes / Double(m - n)
        var cov = [[Double]](repeating: [Double](repeating: 0, count: n), count: n)
        for col in 0..<n {
            var e = [Double](repeating: 0, count: n); e[col] = 1
            guard let c = solveLinear(JtJ, e) else { return nil }
            for r in 0..<n { cov[r][col] = c[r] * s2 }
        }
        // gradient g_j = ∂DG/∂p_j, then σ² = gᵀ cov g
        var g = [Double](repeating: 0, count: n)
        for j in 0..<n {
            var pp = params, pm = params
            pp[j] += step[j]; pm[j] -= step[j]
            g[j] = (dgFromParams(pp) - dgFromParams(pm)) / (2 * step[j])
        }
        var v = 0.0
        for a in 0..<n { for b in 0..<n { v += g[a] * cov[a][b] * g[b] } }
        guard v.isFinite, v >= 0 else { return nil }
        let sd = v.squareRoot()
        return sd.isFinite ? (sd * 100).rounded() / 100 : nil
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

/// DG across the defensible deconvolution choices → primary + [low, high]. The
/// dominant DG uncertainty is the deconvolution choice (1 vs 2 peaks, turbostratic
/// placement), not the fit covariance. Mirrors `xrd_analyzer.dg_range`.
public struct DGRange: Sendable {
    public let primary: Double
    public let low: Double
    public let high: Double
    public let byMethod: [(name: String, dg: Double)]
}

public func dgRange(_ pattern: XRDPattern, turbostraticLow: Double = 26.10,
                    base: FitOptions = FitOptions()) -> DGRange? {
    var methods: [(String, Double)] = []
    func tryFit(_ name: String, _ mutate: (inout FitOptions) -> Void) {
        var o = base; mutate(&o)
        if let r = try? GraphitizationAnalyzer(pattern, wavelength: DEFAULT_WAVELENGTH).run(o) {
            methods.append((name, (r.dgPercent * 100).rounded() / 100))
        }
    }
    tryFit("2-peak") { $0.peakCount = 2; $0.lockTurbostratic = false; $0.turbostraticCenter = nil }
    tryFit("1-peak") { $0.peakCount = 1 }
    tryFit("2-peak low turbostratic") { $0.peakCount = 2; $0.lockTurbostratic = true; $0.turbostraticCenter = turbostraticLow }
    if methods.isEmpty { return nil }
    let vals = methods.map { $0.1 }
    let primary = methods.first { $0.0 == "2-peak" }?.1 ?? vals[0]
    return DGRange(primary: primary, low: vals.min()!, high: vals.max()!,
                   byMethod: methods.map { (name: $0.0, dg: $0.1) })
}
