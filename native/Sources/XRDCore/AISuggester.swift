import Foundation

/// The deconvolution decision the LLM returns (it does NOT compute DG%).
public struct Suggestion: Codable, Sendable {
    public let peakCount: Int
    public let turbostraticCenter: Double?
    public let subtractBackground: Bool
    public let amorphousInvalid: Bool
    public let displacementSuspected: Bool
    public let suggested002Anchor: Double
    public let confidence: Double
    public let rationale: String

    enum CodingKeys: String, CodingKey {
        case peakCount = "peak_count"
        case turbostraticCenter = "turbostratic_2theta"
        case subtractBackground = "subtract_background"
        case amorphousInvalid = "amorphous_invalid"
        case displacementSuspected = "displacement_suspected"
        case suggested002Anchor = "suggested_002_anchor"
        case confidence, rationale
    }

    // Tolerate older models that omit the displacement fields.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        peakCount = try c.decode(Int.self, forKey: .peakCount)
        turbostraticCenter = try c.decodeIfPresent(Double.self, forKey: .turbostraticCenter)
        subtractBackground = try c.decode(Bool.self, forKey: .subtractBackground)
        amorphousInvalid = try c.decode(Bool.self, forKey: .amorphousInvalid)
        displacementSuspected = (try? c.decode(Bool.self, forKey: .displacementSuspected)) ?? false
        suggested002Anchor = (try? c.decode(Double.self, forKey: .suggested002Anchor)) ?? 0
        confidence = try c.decode(Double.self, forKey: .confidence)
        rationale = try c.decode(String.self, forKey: .rationale)
    }
}

public enum AISuggesterError: Error, CustomStringConvertible {
    case http(Int, String)
    case badResponse(String)

    public var description: String {
        switch self {
        case .http(let c, let b): return "Ollama error \(c): \(b.prefix(300))"
        case .badResponse(let m): return "Unexpected Ollama response: \(m)"
        }
    }
}

/// Asks a **local Ollama** model to choose the deconvolution setup from the
/// numeric features. Fully offline — no cloud, no API key. The deterministic
/// engine still computes DG%. Validated ~0.99% DG MAE with gemma3:4b.
public enum AISuggester {
    public static let defaultModel = "gemma3:4b"
    public static let defaultHost = "http://localhost:11434"

    public static let systemPrompt = """
You are an expert XRD analyst applying the NETL standard procedure to deconvolve the carbon (002) reflection of Fe-catalyzed petroleum-coke graphite, to SET UP a Degree of Graphitization calculation. You decide ONLY the deconvolution setup; you do NOT compute DG%.

CRITICAL DOMAIN FACT: these Fe-catalyzed samples almost always retain a SMALL but physically real TURBOSTRATIC fraction - a broad low-angle shoulder (2-theta ~26.0-26.45, below the sharp graphitic peak ~26.4-26.7). It is usually SUBTLE (a few percent of peak height), so a high single-peak R2 (>0.99) does NOT rule it out. Experts fit TWO peaks in the large majority of these samples.

Rules:
1. DEFAULT to peak_count=2. Place turbostratic_2theta at the low-angle shoulder, using low_angle_residual_2theta and automatic_two_peak_turbostratic_2theta as anchors (typically 26.0-26.4, below the graphitic peak).
2. peak_count=1 ONLY if truly symmetric: low_angle_residual_fraction < ~0.015 AND dR2 < ~0.0005. The exception, not the norm. (When peak_count=1, still output a plausible turbostratic_2theta; it is ignored.)
3. amorphous_invalid=true only if no resolvable (002) peak (very broad/weak, low SNR).
4. subtract_background only if an obvious sloped background; else false.
5. SPECIMEN DISPLACEMENT (sample-height error): a well-crystallized graphite (002) sits at ~26.50-26.60 (Cu K-alpha). If the peak is SHARP and SMOOTH (single_peak_FWHM <= ~0.18 AND single_peak_R2 >= ~0.99) - clearly well graphitized - but single_peak_center is notably BELOW ~26.50, that is most likely a sample-displacement shift, NOT low graphitization. Then set displacement_suspected=true and suggested_002_anchor=26.54. A BROAD peak at low angle is GENUINE low graphitization - set displacement_suspected=false, suggested_002_anchor=0. Be CONSERVATIVE: only flag when sharp/smooth AND below 26.50; when unsure, false. The human confirms it.
Respond with ONLY a JSON object with keys: peak_count (1 or 2), turbostratic_2theta (number), subtract_background (bool), amorphous_invalid (bool), displacement_suspected (bool), suggested_002_anchor (number; 0 if none), confidence (0-1), rationale (short string).
"""

    static var schema: [String: Any] { [
        "type": "object",
        "properties": [
            "peak_count": ["type": "integer", "enum": [1, 2]],
            "turbostratic_2theta": ["type": "number"],
            "subtract_background": ["type": "boolean"],
            "amorphous_invalid": ["type": "boolean"],
            "displacement_suspected": ["type": "boolean"],
            "suggested_002_anchor": ["type": "number"],
            "confidence": ["type": "number"],
            "rationale": ["type": "string"],
        ],
        "required": ["peak_count", "turbostratic_2theta", "subtract_background",
                     "amorphous_invalid", "displacement_suspected", "suggested_002_anchor",
                     "confidence", "rationale"],
        "additionalProperties": false,
    ] }

    public static func suggest(_ pattern: XRDPattern, model: String? = nil,
                               ollamaHost: String? = nil)
        async throws -> (Suggestion, DeconvolutionFeatures) {
        let features = try computeFeatures(pattern)
        let featJSON = String(decoding: try JSONEncoder().encode(features), as: UTF8.self)
        let body: [String: Any] = [
            "model": model ?? defaultModel, "stream": false, "format": schema,
            "options": ["temperature": 0],
            "messages": [
                ["role": "system", "content": systemPrompt],
                ["role": "user", "content": "Features (JSON):\n" + featJSON],
            ],
        ]
        let host = ollamaHost ?? defaultHost
        let base = host.hasSuffix("/") ? String(host.dropLast()) : host
        var req = URLRequest(url: URL(string: base + "/api/chat")!)
        req.httpMethod = "POST"; req.timeoutInterval = 300
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw AISuggesterError.badResponse("no HTTP response") }
        guard http.statusCode == 200 else {
            throw AISuggesterError.http(http.statusCode, String(decoding: data, as: UTF8.self))
        }
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let msg = obj["message"] as? [String: Any],
              let text = msg["content"] as? String,
              let out = text.data(using: .utf8) else {
            throw AISuggesterError.badResponse("missing message content")
        }
        return (try JSONDecoder().decode(Suggestion.self, from: out), features)
    }
}
