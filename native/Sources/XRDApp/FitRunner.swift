import Foundation
import XRDCore

/// Single deconvolution pipeline shared by the live Analyze pane and the
/// model-level recompute at open. Turns the user's `DeconvSettings` into engine
/// `FitOptions` (including residual-phase internal-standard calibration) and runs
/// the deterministic `GraphitizationAnalyzer`. Keeping this in one place ensures
/// the sidebar / Compare values match exactly what `DetailView` shows.
enum FitRunner {

    /// Build the engine options for these settings, plus any internal-standard
    /// calibration that was applied (nil when no phase is selected or an explicit
    /// anchor takes precedence).
    static func options(_ pattern: XRDPattern, _ s: DeconvSettings) -> (FitOptions, InternalStandard?) {
        var o = FitOptions()
        o.peakCount = s.peakCount
        o.subtractBackground = s.subtractBg
        o.lockTurbostratic = s.turboLocked
        o.turbostraticCenter = s.turboLocked ? s.turboCenter : nil
        o.anchor002 = (s.anchorOn && s.anchorTarget > 0) ? s.anchorTarget : nil
        var cal: InternalStandard? = nil
        if !s.calStdPhase.isEmpty, o.anchor002 == nil {
            let c = InternalStandard.calibrate(pattern, phase: s.calStdPhase)
            cal = c
            if c.significant { o.twoThetaOffset = -c.offset }   // explicit anchor still wins
        }
        return (o, cal)
    }

    /// Run the fit. Returns the result (nil on failure), the options used (so the
    /// caller can derive the DG range), the calibration, and any error text.
    static func run(_ pattern: XRDPattern, _ s: DeconvSettings)
        -> (result: DGResult?, options: FitOptions, calibration: InternalStandard?, error: String?) {
        let (o, cal) = options(pattern, s)
        do { return (try GraphitizationAnalyzer(pattern).run(o), o, cal, nil) }
        catch { return (nil, o, cal, String(describing: error)) }
    }
}
