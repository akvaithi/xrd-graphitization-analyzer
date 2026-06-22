import Foundation
import XRDCore
#if canImport(FoundationModels)
import FoundationModels
#endif

/// Which engine answers the AI-assist request.
enum AIBackend: String, CaseIterable, Identifiable {
    case auto    = "Automatic"
    case appleFM = "Apple (on-device)"
    case ollama  = "Ollama (gemma3:4b)"
    var id: String { rawValue }
}

/// Deconvolution suggester backed by Apple's on-device Foundation Model.
///
/// Gated to **macOS 27+**: although the `FoundationModels` API exists on macOS 26,
/// macOS 27 ships the updated on-device foundation model, which is the one we
/// validated against the postdoc gold fits. Mirrors `AISuggester` (Ollama) so the
/// two are interchangeable — same system prompt, same `Suggestion` output, and the
/// deterministic engine still computes DG%.
enum FoundationModelsSuggester {

    /// Usable right now: built with the framework, on macOS 27+, model available.
    static var isAvailable: Bool {
#if canImport(FoundationModels)
        if #available(macOS 27.0, *) {
            if case .available = SystemLanguageModel.default.availability { return true }
        }
#endif
        return false
    }

    /// A short human label for the model that's answering (no public version
    /// string is exposed, so we report the family + OS that backs it).
    static var modelLabel: String {
#if canImport(FoundationModels)
        if #available(macOS 27.0, *) {
            return "Apple on-device model · " + ProcessInfo.processInfo.operatingSystemVersionString
        }
#endif
        return "unavailable"
    }

    /// Why it isn't usable, for the UI.
    static var statusText: String {
#if canImport(FoundationModels)
        if #available(macOS 27.0, *) {
            switch SystemLanguageModel.default.availability {
            case .available:
                return "ready"
            case .unavailable(let reason):
                return "unavailable (\(reason))"
            @unknown default:
                return "unavailable"
            }
        }
        return "needs macOS 27"
#else
        return "not built with FoundationModels"
#endif
    }

#if canImport(FoundationModels)
    /// Guided-generation shape mirroring the shared JSON schema.
    @available(macOS 26.0, *)
    @Generable
    struct FMDecision {
        @Guide(description: "Number of peaks to fit: 1 or 2")
        var peak_count: Int
        @Guide(description: "2-theta center of the turbostratic shoulder")
        var turbostratic_2theta: Double
        var subtract_background: Bool
        var amorphous_invalid: Bool
        var displacement_suspected: Bool
        @Guide(description: "Suggested (002) anchor in 2-theta, or 0 if none")
        var suggested_002_anchor: Double
        @Guide(description: "Confidence from 0 to 1")
        var confidence: Double
        @Guide(description: "Short rationale")
        var rationale: String
    }
#endif

    static func suggest(_ pattern: XRDPattern)
        async throws -> (Suggestion, DeconvolutionFeatures) {
        let features = try computeFeatures(pattern)
#if canImport(FoundationModels)
        if #available(macOS 27.0, *) {
            let featJSON = String(decoding: try JSONEncoder().encode(features), as: UTF8.self)
            let session = LanguageModelSession(instructions: AISuggester.systemPrompt)
            let resp = try await session.respond(
                to: "Features (JSON):\n" + featJSON,
                generating: FMDecision.self,
                options: GenerationOptions(temperature: 0))
            let d = resp.content
            // Reuse Suggestion's tolerant JSON decoder so there's one mapping.
            let dict: [String: Any] = [
                "peak_count": d.peak_count,
                "turbostratic_2theta": d.turbostratic_2theta,
                "subtract_background": d.subtract_background,
                "amorphous_invalid": d.amorphous_invalid,
                "displacement_suspected": d.displacement_suspected,
                "suggested_002_anchor": d.suggested_002_anchor,
                "confidence": d.confidence,
                "rationale": d.rationale,
            ]
            let data = try JSONSerialization.data(withJSONObject: dict)
            return (try JSONDecoder().decode(Suggestion.self, from: data), features)
        }
#endif
        throw AISuggesterError.badResponse("Apple on-device model requires macOS 27")
    }
}
