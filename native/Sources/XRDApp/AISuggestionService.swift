import Foundation
import SwiftUI
import XRDCore

/// Resolves the AI engine preference (`@AppStorage("aiBackend")`) + Ollama host
/// into the backend that will actually answer. Shared by the Analyze pane and the
/// batch "Suggest all" action.
struct AIConfig {
    var backend: AIBackend
    var host: String?

    /// The backend that will run, given preference + availability.
    var active: AIBackend {
        switch backend {
        case .auto:    return FoundationModelsSuggester.isAvailable ? .appleFM : .ollama
        case .appleFM: return .appleFM
        case .ollama:  return .ollama
        }
    }
    /// Whether a suggestion can run right now.
    var canSuggest: Bool {
        switch active {
        case .appleFM: return FoundationModelsSuggester.isAvailable
        case .ollama:  return host != nil
        case .auto:    return false
        }
    }

    @MainActor
    static func current(server: OllamaServer) -> AIConfig {
        let d = UserDefaults.standard
        let raw = d.string(forKey: "aiBackend") ?? AIBackend.auto.rawValue
        let saved = d.string(forKey: "ollamaHost")
        let env = ProcessInfo.processInfo.environment["OLLAMA_HOST"]
        let fallback = [saved, env].compactMap { $0 }.first { !$0.isEmpty } ?? "http://localhost:11434"
        return AIConfig(backend: AIBackend(rawValue: raw) ?? .auto, host: server.host ?? fallback)
    }
}

/// Runs the chosen backend on one pattern and maps the model's decision onto a
/// `DeconvSettings` update — identical logic for the single-file Suggest button
/// and the batch "AI Suggest all". DG% is still computed by the deterministic
/// engine; this only chooses the deconvolution setup.
enum AISuggestionService {
    struct Outcome {
        var settings: DeconvSettings
        var suggestion: Suggestion
        var features: DeconvolutionFeatures
    }

    static func suggest(pattern: XRDPattern, active: AIBackend, host: String?,
                        base: DeconvSettings) async throws -> Outcome {
        // Measure displacement deterministically from a residual phase first; feed
        // the AI calibrated data (more accurate than it guessing displacement).
        let cal = InternalStandard.calibrate(pattern, phase: "auto")
        let aiPattern = cal.significant
            ? XRDPattern(twoTheta: pattern.twoTheta.map { $0 - cal.offset }, intensity: pattern.intensity)
            : pattern
        let (s, feats): (Suggestion, DeconvolutionFeatures) = active == .appleFM
            ? try await FoundationModelsSuggester.suggest(aiPattern)
            : try await AISuggester.suggest(aiPattern, ollamaHost: host ?? AISuggester.defaultHost)

        var v = base
        if s.amorphousInvalid {
            v.aiConfidence = s.confidence
            v.aiNote = "⚠︎ Flagged as too amorphous for this method. " + s.rationale
            return Outcome(settings: v, suggestion: s, features: feats)
        }
        v.peakCount = s.peakCount
        v.subtractBg = s.subtractBackground
        if let t = s.turbostraticCenter { v.turboCenter = t; v.turboLocked = true }
        else { v.turboLocked = false }
        if cal.significant {                       // measured offset beats AI guess
            v.calStdPhase = "auto"; v.anchorOn = false
        } else if s.displacementSuspected, s.suggested002Anchor > 0 {
            v.anchorOn = true; v.anchorTarget = s.suggested002Anchor
        }
        v.aiConfidence = s.confidence
        v.aiNote = ((s.confidence < 0.8) ? "Review suggested (low confidence). " : "") + s.rationale
        return Outcome(settings: v, suggestion: s, features: feats)
    }
}
