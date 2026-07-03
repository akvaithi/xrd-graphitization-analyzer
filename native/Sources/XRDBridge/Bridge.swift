import Foundation
import Dispatch
import XRDCore

// JSON-in / JSON-out C ABI over XRDCore, consumed via P/Invoke by the WinUI
// (C#) front-end in windows/XRDAnalyzer. Every exported function takes one
// UTF-8 JSON C string and returns one heap-allocated UTF-8 JSON C string that
// the caller must release with `xrd_free`. All DG/crystallinity/yield/
// calibration math happens in XRDCore — this file only marshals JSON.

private func decode<T: Decodable>(_ ptr: UnsafePointer<CChar>?, as type: T.Type) -> T? {
    guard let ptr = ptr else { return nil }
    let data = Data(String(cString: ptr).utf8)
    return try? JSONDecoder().decode(T.self, from: data)
}

private func allocateCString(_ s: String) -> UnsafeMutablePointer<CChar> {
    let bytes = Array(s.utf8CString)   // includes the trailing NUL
    let buf = UnsafeMutablePointer<CChar>.allocate(capacity: bytes.count)
    buf.initialize(from: bytes, count: bytes.count)
    return buf
}

private func encodeCString<T: Encodable>(_ value: T) -> UnsafeMutablePointer<CChar> {
    let json = (try? JSONEncoder().encode(value)).flatMap { String(decoding: $0, as: UTF8.self) } ?? "{}"
    return allocateCString(json)
}

/// Frees a string returned by any `xrd_*` function below. Must be paired with
/// `allocateCString`'s `UnsafeMutablePointer<CChar>.allocate` (not libc
/// malloc/free), so free it only through this function.
@_cdecl("xrd_free")
public func xrd_free(_ ptr: UnsafeMutablePointer<CChar>?) {
    ptr?.deallocate()
}

// MARK: - xrd_fit

@_cdecl("xrd_fit")
public func xrd_fit(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: FitRequest.self) else {
        return encodeCString(FitResponse(result: nil, calibration: nil, error: "invalid request JSON"))
    }
    let pattern = req.pattern.pattern
    let (opt, cal) = buildFitOptions(pattern, req.settings)
    let calDTO = cal.map(InternalStandardDTO.init)
    do {
        let r = try GraphitizationAnalyzer(pattern).run(opt)
        return encodeCString(FitResponse(result: DGResultDTO(r), calibration: calDTO, error: nil))
    } catch {
        return encodeCString(FitResponse(result: nil, calibration: calDTO, error: errorDescription(error)))
    }
}

// MARK: - xrd_range

@_cdecl("xrd_range")
public func xrd_range(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: RangeRequest.self) else {
        return encodeCString(RangeResponse(range: nil, calibration: nil, error: "invalid request JSON"))
    }
    let pattern = req.pattern.pattern
    let (opt, cal) = buildFitOptions(pattern, req.settings)
    let calDTO = cal.map(InternalStandardDTO.init)
    guard let r = dgRange(pattern, turbostraticLow: req.turbostraticLow, base: opt) else {
        return encodeCString(RangeResponse(range: nil, calibration: calDTO,
                                            error: "fit failed across all deconvolution methods"))
    }
    return encodeCString(RangeResponse(range: DGRangeDTO(r), calibration: calDTO, error: nil))
}

// MARK: - xrd_curve

@_cdecl("xrd_curve")
public func xrd_curve(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: CurveRequest.self), req.xHigh > req.xLow, req.points > 0 else {
        return encodeCString(CurveResponse(series: nil, error: "invalid request JSON"))
    }
    let g = req.graphitic
    let n = req.points
    var graph: [CurvePointDTO] = [], turbo: [CurvePointDTO] = [], total: [CurvePointDTO] = []
    for i in 0...n {
        let x = req.xLow + (req.xHigh - req.xLow) * Double(i) / Double(n)
        let gy = req.y0 + pseudoVoigt(x, g.A, g.xc, g.w, g.mu)
        graph.append(CurvePointDTO(x: x, y: gy))
        if let t = req.turbostratic {
            let ty = req.y0 + pseudoVoigt(x, t.A, t.xc, t.w, t.mu)
            turbo.append(CurvePointDTO(x: x, y: ty))
            total.append(CurvePointDTO(x: x, y: gy + ty - req.y0))
        } else {
            total.append(CurvePointDTO(x: x, y: gy))
        }
    }
    return encodeCString(CurveResponse(series: CurveSeriesDTO(graphitic: graph, turbostratic: turbo, total: total), error: nil))
}

// MARK: - xrd_impurities

@_cdecl("xrd_impurities")
public func xrd_impurities(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: ImpuritiesRequest.self) else {
        return encodeCString(ImpuritiesResponse(scan: nil, error: "invalid request JSON"))
    }
    let scan = ImpurityScan.scan(req.pattern.pattern)
    return encodeCString(ImpuritiesResponse(scan: ImpurityScanDTO(scan), error: nil))
}

// MARK: - xrd_crystallinity

@_cdecl("xrd_crystallinity")
public func xrd_crystallinity(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: CrystallinityRequest.self) else {
        return encodeCString(CrystallinityResponse(result: nil, error: "invalid request JSON"))
    }
    let low = req.windowLow ?? CrystallinityAnalyzer.windowLow
    let high = req.windowHigh ?? CrystallinityAnalyzer.windowHigh
    do {
        let r = try CrystallinityAnalyzer.analyze(req.pattern.pattern, low: low, high: high)
        return encodeCString(CrystallinityResponse(result: CrystallinityDTO(r), error: nil))
    } catch {
        return encodeCString(CrystallinityResponse(result: nil, error: errorDescription(error)))
    }
}

// MARK: - xrd_yield

@_cdecl("xrd_yield")
public func xrd_yield(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: YieldRequest.self) else {
        return encodeCString(YieldResponse(result: nil, error: "invalid request JSON"))
    }
    guard req.pellet > 0, let cr = req.carbonRatio, cr > 0, let fr = req.feRatio else {
        return encodeCString(YieldResponse(
            result: nil, error: "insufficient recipe info (pellet mass + carbon/Fe ratios) to derive masses"))
    }
    let car = req.caco3Ratio ?? 0
    let sum = cr + fr + car
    guard sum > 0 else {
        return encodeCString(YieldResponse(result: nil, error: "recipe ratios sum to zero"))
    }
    let gpc = req.pellet * cr / sum, fe = req.pellet * fr / sum, caco3 = req.pellet * car / sum
    let derived = DerivedMassesDTO(gpc: gpc, fe: fe, caco3: caco3, grade: req.carbonType)
    // isComputable mirrors AppModel.YieldInputs.isComputable: pellet + post-furnace both entered.
    guard req.postFurnace > 0 else {
        return encodeCString(YieldResponse(result: nil, derivedMasses: derived, error: nil))
    }
    let base = YieldCalc.defaultComposition(grade: req.carbonType)
    let r = YieldCalc.compute(
        gpcMass: gpc, cWt: req.cWt ?? base.cWt, sWt: req.sWt ?? base.sWt, feMass: fe,
        caco3Mass: caco3, postFurnace: req.postFurnace,
        postAcid: (req.postAcid ?? 0) > 0 ? req.postAcid : nil, pellet: req.pellet,
        crystallineFraction: req.crystallineFraction)
    return encodeCString(YieldResponse(result: YieldResultDTO(r), derivedMasses: derived, error: nil))
}

// MARK: - xrd_parse_run

@_cdecl("xrd_parse_run")
public func xrd_parse_run(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: ParseRunRequest.self) else {
        return encodeCString(ParseRunResponse(info: nil))
    }
    let info = RunParser.parse(fileName: req.fileName)
    return encodeCString(ParseRunResponse(info: RunInfoDTO(info)))
}

// MARK: - xrd_manual

@_cdecl("xrd_manual")
public func xrd_manual(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: ManualRequest.self) else {
        return encodeCString(ManualResponse(result: nil, error: "invalid request JSON"))
    }
    let peaks = req.peaks.map { ManualPeak(xc: $0.xc, area: $0.area) }
    do {
        let r = try dgFromPeaks(peaks, wavelength: req.wavelength ?? DEFAULT_WAVELENGTH)
        return encodeCString(ManualResponse(result: ManualResultDTO(r), error: nil))
    } catch {
        return encodeCString(ManualResponse(result: nil, error: errorDescription(error)))
    }
}

// MARK: - xrd_ai_suggest

/// Boxes the response so it can be mutated from the `Task` and read back on
/// this thread; safe because `sema.wait()` below happens-after the `Task`'s
/// `sema.signal()`, so there is no concurrent access, only a handoff.
private final class ResponseBox: @unchecked Sendable {
    var value = AiSuggestResponse()
}

/// Blocking wrapper over `AISuggester.suggest` (Ollama, async) — mirrors
/// `AISuggestionService.suggest` (native/Sources/XRDApp/AISuggestionService.swift):
/// pre-calibrates against a residual internal-standard phase, then maps the
/// model's decision onto a settings delta the caller merges into its own state.
@_cdecl("xrd_ai_suggest")
public func xrd_ai_suggest(_ reqJson: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar> {
    guard let req = decode(reqJson, as: AiSuggestRequest.self) else {
        return encodeCString(AiSuggestResponse(error: "invalid request JSON"))
    }
    let pattern = req.pattern.pattern
    let cal = InternalStandard.calibrate(pattern, phase: "auto")
    let aiPattern = cal.significant
        ? XRDPattern(twoTheta: pattern.twoTheta.map { $0 - cal.offset }, intensity: pattern.intensity)
        : pattern

    let box = ResponseBox()
    let sema = DispatchSemaphore(value: 0)
    Task {
        defer { sema.signal() }
        do {
            let (s, feats) = try await AISuggester.suggest(aiPattern, model: req.model, ollamaHost: req.host)
            box.value.suggestion = SuggestionDTO(s)
            box.value.features = FeaturesDTO(feats)
            box.value.calibration = InternalStandardDTO(cal)
            box.value.aiConfidence = s.confidence

            if s.amorphousInvalid {
                box.value.aiNote = "\u{26A0}\u{FE0E} Flagged as too amorphous for this method. " + s.rationale
                return
            }
            var settings = FitSettingsDTO()
            settings.peakCount = s.peakCount
            settings.subtractBg = s.subtractBackground
            if let t = s.turbostraticCenter { settings.turboCenter = t; settings.turboLocked = true }
            else { settings.turboLocked = false }
            if cal.significant {
                settings.calStdPhase = "auto"; settings.anchorOn = false
            } else if s.displacementSuspected, s.suggested002Anchor > 0 {
                settings.anchorOn = true; settings.anchorTarget = s.suggested002Anchor
            }
            box.value.settings = settings
            box.value.aiNote = ((s.confidence < 0.8) ? "Review suggested (low confidence). " : "") + s.rationale
        } catch {
            box.value.error = errorDescription(error)
        }
    }
    sema.wait()
    return encodeCString(box.value)
}
