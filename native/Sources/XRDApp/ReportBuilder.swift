import Foundation
import XRDCore

/// Builds the per-file key/value report CSV. Shared by the single-file export
/// (DetailView) and batch export (AppModel) so they stay identical.
enum ReportBuilder {
    static func csv(displayName: String, fileName: String, result r: DGResult,
                    span: DGRange?, quality: ImpurityScan?) -> String {
        var rows: [(String, String)] = [
            ("sample", displayName), ("file", fileName),
            ("method", r.methodName), ("wavelength_angstrom", String(format: "%.5f", r.wavelength)),
            ("DG_percent", String(format: "%.2f", r.dgPercent)),
            ("DG_sigma", r.dgSigma.map { String(format: "%.2f", $0) } ?? ""),
        ]
        if let s = span { rows += [("DG_range_low", String(format: "%.2f", s.low)),
                                   ("DG_range_high", String(format: "%.2f", s.high))] }
        rows += [
            ("peak_count", "\(r.peakCount)"),
            ("graphitic_2theta", String(format: "%.4f", r.graphitic.xc)),
            ("graphitic_FWHM", String(format: "%.4f", r.graphitic.w)),
            ("graphitic_mu", String(format: "%.4f", r.graphitic.mu)),
            ("graphitic_d_nm", String(format: "%.5f", r.graphitic.dSpacing)),
        ]
        if let t = r.turbostratic {
            rows += [("turbostratic_2theta", String(format: "%.4f", t.xc)),
                     ("turbostratic_FWHM", String(format: "%.4f", t.w)),
                     ("turbostratic_d_nm", String(format: "%.5f", t.dSpacing))]
        }
        rows += [
            ("X_graphitic", String(format: "%.4f", r.areaFractionGraphitic)),
            ("X_turbostratic", String(format: "%.4f", r.areaFractionTurbostratic)),
            ("d_prime_nm", String(format: "%.5f", r.dPrimeWeighted)),
            ("crystallite_Lc_A", String(format: "%.1f", r.crystalliteLc)),
            ("baseline_y0", String(format: "%.3f", r.y0)),
            ("two_theta_offset", String(format: "%+.3f", r.twoThetaOffset)),
            ("fit_R2", String(format: "%.5f", r.fitR2)),
        ]
        if let q = quality { rows.append(("data_quality", q.verdict)) }
        return "key,value\n" + rows.map { "\($0.0),\"\($0.1)\"" }.joined(separator: "\n") + "\n"
    }

    /// Columns for the consolidated runs CSV (mirrors the Compare tab).
    static func consolidatedHeader() -> String {
        "file,carbon_type,carbon_ratio,fe_ratio,caco3_ratio,temperature_C,time_h,form,wash,DG,Lc,d_prime,graphitic_xc\n"
    }
    static func consolidatedRow(fileName: String, info: RunInfo?, result r: DGResult?) -> String {
        func n(_ v: Double?) -> String { v.map { String(format: "%g", $0) } ?? "" }
        func q(_ s: String) -> String { s.contains(",") ? "\"\(s)\"" : s }
        let i = info
        return [q(fileName), i?.carbonType ?? "", n(i?.carbonRatio), n(i?.feRatio),
                n(i?.caco3Ratio), i?.temperatureC.map(String.init) ?? "", n(i?.timeH),
                i?.form ?? "", i?.wash ?? "",
                r.map { String(format: "%.2f", $0.dgPercent) } ?? "",
                r.map { String(format: "%.1f", $0.crystalliteLc) } ?? "",
                r.map { String(format: "%.5f", $0.dPrimeWeighted) } ?? "",
                r.map { String(format: "%.4f", $0.graphitic.xc) } ?? ""].joined(separator: ",") + "\n"
    }
}
