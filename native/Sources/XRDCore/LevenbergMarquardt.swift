import Foundation

/// Bounded nonlinear least-squares fit (Levenberg–Marquardt with Marquardt
/// diagonal scaling + box constraints by projection). Replaces
/// `scipy.optimize.curve_fit(..., bounds=...)` for the 7-parameter peak model.
///
/// Marquardt scaling — solving `(JᵀJ + λ·diag(JᵀJ))·δ = −Jᵀr` — handles the wide
/// disparity in parameter magnitudes (area ~10²,  centre ~26,  width ~0.1,  μ ~0.5)
/// without explicit rescaling, which keeps results close to scipy's TRF here.
public struct LMResult {
    public let params: [Double]
    public let converged: Bool
    public let iterations: Int
    public let cost: Double
}

public func levenbergMarquardt(
    x: [Double], y: [Double],
    model: (Double, [Double]) -> Double,
    p0: [Double], lower: [Double], upper: [Double],
    maxIter: Int = 300, ftol: Double = 1e-10
) -> LMResult {
    let m = p0.count
    let n = x.count
    var p = clampVec(p0, lower, upper)

    func modelAll(_ pp: [Double]) -> [Double] {
        var f = [Double](repeating: 0, count: n)
        for i in 0..<n { f[i] = model(x[i], pp) }
        return f
    }
    func residuals(from f: [Double]) -> [Double] {
        var r = [Double](repeating: 0, count: n)
        for i in 0..<n { r[i] = f[i] - y[i] }
        return r
    }
    func sumsq(_ r: [Double]) -> Double {
        var s = 0.0; for v in r { s += v * v }; return s
    }

    var fCur = modelAll(p)
    var r = residuals(from: fCur)
    var cost = sumsq(r)
    var lambda = 1e-3
    var converged = false
    var iter = 0

    while iter < maxIter {
        iter += 1

        // Forward-difference Jacobian J (n×m), stepping away from an upper bound.
        var J = [[Double]](repeating: [Double](repeating: 0, count: m), count: n)
        for j in 0..<m {
            let scale = max(abs(p[j]), 1e-3)
            let h0 = 1e-7 * scale
            var sign = 1.0
            if p[j] + h0 > upper[j] { sign = -1.0 }
            let h = sign * h0
            var pj = p
            pj[j] = p[j] + h
            for i in 0..<n {
                let fp = model(x[i], pj)
                J[i][j] = (fp - fCur[i]) / h
            }
        }

        // Normal equations: A = JᵀJ (m×m), g = Jᵀr (m).
        var A = [[Double]](repeating: [Double](repeating: 0, count: m), count: m)
        var g = [Double](repeating: 0, count: m)
        for a in 0..<m {
            for b in a..<m {
                var s = 0.0
                for i in 0..<n { s += J[i][a] * J[i][b] }
                A[a][b] = s; A[b][a] = s
            }
            var sg = 0.0
            for i in 0..<n { sg += J[i][a] * r[i] }
            g[a] = sg
        }

        // Inner loop: grow λ until the damped step reduces the cost.
        var accepted = false
        for _ in 0..<40 {
            var Mtx = A
            for k in 0..<m { Mtx[k][k] = A[k][k] * (1.0 + lambda) + 1e-12 }
            guard let delta = solveLinear(Mtx, negate(g)) else { lambda *= 4; continue }

            let pNew = clampVec(addVec(p, delta), lower, upper)
            let fNew = modelAll(pNew)
            let rNew = residuals(from: fNew)
            let cNew = sumsq(rNew)
            if cNew < cost {
                let dCost = cost - cNew
                p = pNew; fCur = fNew; r = rNew
                if dCost < ftol * (1.0 + cost) { converged = true }
                cost = cNew
                lambda = max(lambda * 0.3, 1e-12)
                accepted = true
                break
            } else {
                lambda *= 4
                if lambda > 1e14 { break }
            }
        }
        if !accepted || converged { converged = true; break }
    }

    return LMResult(params: p, converged: converged, iterations: iter, cost: cost)
}

// MARK: - small numeric helpers

func clampVec(_ p: [Double], _ lo: [Double], _ hi: [Double]) -> [Double] {
    var r = p
    for i in p.indices {
        if r[i] < lo[i] { r[i] = lo[i] }
        if r[i] > hi[i] { r[i] = hi[i] }
    }
    return r
}

func addVec(_ a: [Double], _ b: [Double]) -> [Double] {
    var r = a; for i in a.indices { r[i] += b[i] }; return r
}

func negate(_ a: [Double]) -> [Double] { a.map { -$0 } }

/// Dense linear solve via Gaussian elimination with partial pivoting (small m).
func solveLinear(_ Ain: [[Double]], _ bin: [Double]) -> [Double]? {
    let n = bin.count
    var A = Ain
    var b = bin
    for col in 0..<n {
        var piv = col
        var maxv = abs(A[col][col])
        for rIdx in (col + 1)..<n where abs(A[rIdx][col]) > maxv {
            maxv = abs(A[rIdx][col]); piv = rIdx
        }
        if maxv < 1e-300 { return nil }
        if piv != col { A.swapAt(piv, col); b.swapAt(piv, col) }
        let d = A[col][col]
        for rIdx in (col + 1)..<n {
            let f = A[rIdx][col] / d
            if f == 0 { continue }
            for c in col..<n { A[rIdx][c] -= f * A[col][c] }
            b[rIdx] -= f * b[col]
        }
    }
    var xv = [Double](repeating: 0, count: n)
    for i in stride(from: n - 1, through: 0, by: -1) {
        var s = b[i]
        if i + 1 < n { for c in (i + 1)..<n { s -= A[i][c] * xv[c] } }
        xv[i] = s / A[i][i]
    }
    return xv
}
