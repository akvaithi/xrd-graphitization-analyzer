import Foundation
import XRDCore

// JSON DTOs for the C ABI boundary. XRDCore's own types intentionally stay
// free of Codable/JSON concerns; these mirror them field-for-field (property
// names match `DeconvSettings`/`YieldInputs` in the macOS app so the eventual
// C# models line up 1:1). All derived math (calibration, mass scaling,
// AI-suggestion → settings mapping) lives here / in XRDCore, never in C#.

// MARK: - Pattern

struct PatternDTO: Codable {
    var twoTheta: [Double]
    var intensity: [Double]

    var pattern: XRDPattern { XRDPattern(twoTheta: twoTheta, intensity: intensity) }
}

// MARK: - Fit (xrd_fit / xrd_range)

/// Mirrors `DeconvSettings` (native/Sources/XRDApp/AppModel.swift).
struct FitSettingsDTO: Codable {
    var peakCount = 2
    var subtractBg = false
    var turboLocked = false
    var turboCenter = 26.2
    var anchorOn = false
    var anchorTarget = 26.54
    var calStdPhase = ""
}

/// Port of `FitRunner.options` (native/Sources/XRDApp/FitRunner.swift): turns
/// `DeconvSettings` into engine `FitOptions`, applying internal-standard
/// calibration when no explicit anchor is set.
func buildFitOptions(_ pattern: XRDPattern, _ s: FitSettingsDTO) -> (FitOptions, InternalStandard?) {
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
        if c.significant { o.twoThetaOffset = -c.offset }
    }
    return (o, cal)
}

struct PeakDTO: Codable {
    let A, xc, w, mu, dSpacing: Double
    init(_ p: Peak) { A = p.A; xc = p.xc; w = p.w; mu = p.mu; dSpacing = p.dSpacing }
}

struct DGResultDTO: Codable {
    let methodName: String
    let wavelength, y0: Double
    let peakCount: Int
    let backgroundSubtracted: Bool
    let graphitic: PeakDTO
    let turbostratic: PeakDTO?
    let areaFractionGraphitic, areaFractionTurbostratic: Double
    let dPrimeWeighted, crystalliteLc, fitR2, dgPercent: Double
    let dgSigma: Double?
    let twoThetaOffset: Double
    let pointsX, pointsY: [Double]

    init(_ r: DGResult) {
        methodName = r.methodName; wavelength = r.wavelength; y0 = r.y0
        peakCount = r.peakCount; backgroundSubtracted = r.backgroundSubtracted
        graphitic = PeakDTO(r.graphitic); turbostratic = r.turbostratic.map(PeakDTO.init)
        areaFractionGraphitic = r.areaFractionGraphitic; areaFractionTurbostratic = r.areaFractionTurbostratic
        dPrimeWeighted = r.dPrimeWeighted; crystalliteLc = r.crystalliteLc
        fitR2 = r.fitR2; dgPercent = r.dgPercent; dgSigma = r.dgSigma
        twoThetaOffset = r.twoThetaOffset; pointsX = r.pointsX; pointsY = r.pointsY
    }
}

struct InternalStandardMatchDTO: Codable {
    let line, observed, delta: Double
    init(_ m: InternalStandard.Match) { line = m.line; observed = m.observed; delta = m.delta }
}

struct InternalStandardDTO: Codable {
    let phase, phaseLabel: String?
    let offset: Double
    let spread: Double?
    let nLines: Int
    let matches: [InternalStandardMatchDTO]
    let reliable, significant: Bool

    init(_ c: InternalStandard) {
        phase = c.phase; phaseLabel = c.phaseLabel; offset = c.offset; spread = c.spread
        nLines = c.nLines; matches = c.matches.map(InternalStandardMatchDTO.init)
        reliable = c.reliable; significant = c.significant
    }
}

struct FitRequest: Codable { var pattern: PatternDTO; var settings: FitSettingsDTO }

struct FitResponse: Codable {
    var result: DGResultDTO?
    var calibration: InternalStandardDTO?
    var error: String?
}

struct DgRangeMethodDTO: Codable { let name: String; let dg: Double }
struct DGRangeDTO: Codable {
    let primary, low, high: Double
    let byMethod: [DgRangeMethodDTO]
    init(_ r: DGRange) {
        primary = r.primary; low = r.low; high = r.high
        byMethod = r.byMethod.map { DgRangeMethodDTO(name: $0.name, dg: $0.dg) }
    }
}

struct RangeRequest: Codable {
    var pattern: PatternDTO
    var settings: FitSettingsDTO
    var turbostraticLow: Double = 26.10
}

struct RangeResponse: Codable {
    var range: DGRangeDTO?
    var calibration: InternalStandardDTO?
    var error: String?
}

// MARK: - Curve sampling (xrd_curve)

/// Samples the fitted pseudo-Voigt curves for chart rendering — port of
/// `FitChartView.curves()` (native/Sources/XRDApp/FitChartView.swift). Keeps
/// the pseudo-Voigt evaluation in Swift so the C# chart layer only plots
/// points, never re-derives them.
struct CurveRequest: Codable {
    var y0: Double
    var graphitic: PeakDTO
    var turbostratic: PeakDTO?
    var xLow: Double
    var xHigh: Double
    var points: Int = 320
}

struct CurvePointDTO: Codable { let x, y: Double }

struct CurveSeriesDTO: Codable {
    let graphitic: [CurvePointDTO]
    let turbostratic: [CurvePointDTO]
    let total: [CurvePointDTO]
}

struct CurveResponse: Codable { var series: CurveSeriesDTO?; var error: String? }

// MARK: - Impurities (xrd_impurities)

struct ImpurityHitDTO: Codable {
    let twoTheta: Double
    let phase, meaning: String
    let relPct: Double
    let level: String
    init(_ h: ImpurityScan.Hit) {
        twoTheta = h.twoTheta; phase = h.phase; meaning = h.meaning
        relPct = h.relPct; level = h.level
    }
}

struct ImpurityScanDTO: Codable {
    let verdict: String
    let hits: [ImpurityHitDTO]
    let clean: Bool
    let worstPct: Double
    init(_ s: ImpurityScan) {
        verdict = s.verdict; hits = s.hits.map(ImpurityHitDTO.init)
        clean = s.clean; worstPct = s.worstPct
    }
}

struct ImpuritiesRequest: Codable { var pattern: PatternDTO }
struct ImpuritiesResponse: Codable { var scan: ImpurityScanDTO?; var error: String? }

// MARK: - Crystallinity (xrd_crystallinity)

struct CrystallinityDTO: Codable {
    let crystallineFraction, disorderedFraction: Double
    let sharpArea, broadArea: Double
    let graphiticCenter, graphiticFWHM, fitR2: Double
    let windowLow, windowHigh: Double
    init(_ r: CrystallinityResult) {
        crystallineFraction = r.crystallineFraction; disorderedFraction = r.disorderedFraction
        sharpArea = r.sharpArea; broadArea = r.broadArea
        graphiticCenter = r.graphiticCenter; graphiticFWHM = r.graphiticFWHM
        fitR2 = r.fitR2; windowLow = r.windowLow; windowHigh = r.windowHigh
    }
}

struct CrystallinityRequest: Codable {
    var pattern: PatternDTO
    var windowLow: Double?
    var windowHigh: Double?
}
struct CrystallinityResponse: Codable { var result: CrystallinityDTO?; var error: String? }

// MARK: - Yield (xrd_yield)

/// Recipe ratios + pellet mass drive the same derivation as
/// `AppModel.derivedMasses` (native/Sources/XRDApp/AppModel.swift): masses are
/// back-derived from the filename's GPC/Fe/CaCO3 ratios scaled to the weighed
/// pellet. Kept here (not in C#) so the mass-balance math has one home.
struct YieldRequest: Codable {
    var pellet: Double
    var postFurnace: Double
    var postAcid: Double?
    var carbonRatio: Double?
    var feRatio: Double?
    var caco3Ratio: Double?
    var carbonType: String?
    var cWt: Double?
    var sWt: Double?
    var crystallineFraction: Double?
}

struct YieldResultDTO: Codable {
    let actualC, actualS: Double
    let boudouardCLoss, remainingC: Double
    let caResidues, graphiteTheoretical, targetWash: Double
    let measuredCAfterFurnace, massYield, carbonLostBeyondBoudouard: Double
    let unaccountedFurnaceLoss: Double?
    let trappedMetal, washEfficiency: Double?
    let overRemoved: Bool?
    let crystallineFraction, crystallineGraphiteYield: Double?

    init(_ r: YieldResult) {
        actualC = r.actualC; actualS = r.actualS
        boudouardCLoss = r.boudouardCLoss; remainingC = r.remainingC
        caResidues = r.caResidues; graphiteTheoretical = r.graphiteTheoretical; targetWash = r.targetWash
        measuredCAfterFurnace = r.measuredCAfterFurnace; massYield = r.massYield
        carbonLostBeyondBoudouard = r.carbonLostBeyondBoudouard
        unaccountedFurnaceLoss = r.unaccountedFurnaceLoss
        trappedMetal = r.trappedMetal; washEfficiency = r.washEfficiency; overRemoved = r.overRemoved
        crystallineFraction = r.crystallineFraction; crystallineGraphiteYield = r.crystallineGraphiteYield
    }
}

/// Recipe masses back-derived from the filename ratios scaled to the pellet —
/// port of `AppModel.derivedMasses` (native/Sources/XRDApp/AppModel.swift).
/// Surfaced alongside the yield result so the UI can show "from filename"
/// without re-deriving the ratio math itself.
struct DerivedMassesDTO: Codable {
    let gpc, fe, caco3: Double
    let grade: String?
}

struct YieldResponse: Codable {
    var result: YieldResultDTO?
    var derivedMasses: DerivedMassesDTO?
    var error: String?
}

// MARK: - Run parsing (xrd_parse_run)

struct RunInfoDTO: Codable {
    let carbonType: String?
    let carbonRatio, feRatio: Double?
    let hasFe: Bool
    let caco3Ratio: Double?
    let temperatureC: Int?
    let timeH: Double?
    let form, wash: String?
    let displayName: String
    init(_ i: RunInfo) {
        carbonType = i.carbonType; carbonRatio = i.carbonRatio; feRatio = i.feRatio
        hasFe = i.hasFe; caco3Ratio = i.caco3Ratio; temperatureC = i.temperatureC
        timeH = i.timeH; form = i.form; wash = i.wash; displayName = i.displayName
    }
}

struct ParseRunRequest: Codable { var fileName: String }
struct ParseRunResponse: Codable { var info: RunInfoDTO? }

// MARK: - Manual entry (xrd_manual)

struct ManualPeakDTO: Codable { var xc, area: Double }

struct ManualResultDTO: Codable {
    let nPeaks: Int
    let wavelength: Double
    let graphiticXc, graphiticArea, graphiticD: Double
    let turbostraticXc, turbostraticArea, turbostraticD: Double?
    let areaFractionGraphitic, areaFractionTurbostratic: Double?
    let dPrime, dgPercent: Double
    init(_ r: ManualResult) {
        nPeaks = r.nPeaks; wavelength = r.wavelength
        graphiticXc = r.graphiticXc; graphiticArea = r.graphiticArea; graphiticD = r.graphiticD
        turbostraticXc = r.turbostraticXc; turbostraticArea = r.turbostraticArea; turbostraticD = r.turbostraticD
        areaFractionGraphitic = r.areaFractionGraphitic; areaFractionTurbostratic = r.areaFractionTurbostratic
        dPrime = r.dPrime; dgPercent = r.dgPercent
    }
}

struct ManualRequest: Codable { var peaks: [ManualPeakDTO]; var wavelength: Double? }
struct ManualResponse: Codable { var result: ManualResultDTO?; var error: String? }

// MARK: - AI suggest (xrd_ai_suggest)

struct SuggestionDTO: Codable {
    let peakCount: Int
    let turbostraticCenter: Double?
    let subtractBackground, amorphousInvalid, displacementSuspected: Bool
    let suggested002Anchor, confidence: Double
    let rationale: String
    init(_ s: Suggestion) {
        peakCount = s.peakCount; turbostraticCenter = s.turbostraticCenter
        subtractBackground = s.subtractBackground; amorphousInvalid = s.amorphousInvalid
        displacementSuspected = s.displacementSuspected; suggested002Anchor = s.suggested002Anchor
        confidence = s.confidence; rationale = s.rationale
    }
}

struct FeaturesDTO: Codable {
    let singlePeakR2, twoPeakR2, dR2: Double
    let singlePeakCenter, singlePeakFWHM: Double
    let lowAngleResidual2theta, lowAngleResidualFraction: Double
    let automaticTwoPeakTurbostratic2theta: Double?
    let snr: Double
    init(_ f: DeconvolutionFeatures) {
        singlePeakR2 = f.singlePeakR2; twoPeakR2 = f.twoPeakR2; dR2 = f.dR2
        singlePeakCenter = f.singlePeakCenter; singlePeakFWHM = f.singlePeakFWHM
        lowAngleResidual2theta = f.lowAngleResidual2theta
        lowAngleResidualFraction = f.lowAngleResidualFraction
        automaticTwoPeakTurbostratic2theta = f.automaticTwoPeakTurbostratic2theta
        snr = f.snr
    }
}

struct AiSuggestRequest: Codable {
    var pattern: PatternDTO
    var model: String?
    var host: String?
}

/// Response carries both the raw model output and the *derived settings delta*
/// (peakCount/subtractBg/turboLocked/... — same shape as `FitSettingsDTO`) so
/// the decision logic in `AISuggestionService.suggest`
/// (native/Sources/XRDApp/AISuggestionService.swift) is not re-encoded in C#.
struct AiSuggestResponse: Codable {
    var suggestion: SuggestionDTO?
    var features: FeaturesDTO?
    var calibration: InternalStandardDTO?
    var settings: FitSettingsDTO?
    var aiConfidence: Double?
    var aiNote: String?
    var error: String?
}

func errorDescription(_ e: Error) -> String {
    String(describing: e)
}
