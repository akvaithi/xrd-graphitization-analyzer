import Foundation

/// The deconvolution decision the LLM returns (it does NOT compute DG%).
public struct Suggestion: Codable, Sendable {
    public let peakCount: Int
    public let turbostraticCenter: Double?
    public let subtractBackground: Bool
    public let amorphousInvalid: Bool
    public let confidence: Double
    public let rationale: String

    enum CodingKeys: String, CodingKey {
        case peakCount = "peak_count"
        case turbostraticCenter = "turbostratic_2theta"
        case subtractBackground = "subtract_background"
        case amorphousInvalid = "amorphous_invalid"
        case confidence, rationale
    }
}

public enum AISuggesterError: Error, CustomStringConvertible {
    case noAPIKey
    case http(Int, String)
    case badResponse(String)

    public var description: String {
        switch self {
        case .noAPIKey: return "ANTHROPIC_API_KEY is not set in the environment."
        case .http(let c, let b): return "Anthropic API error \(c): \(b.prefix(300))"
        case .badResponse(let m): return "Unexpected API response: \(m)"
        }
    }
}

/// Asks Claude to choose the deconvolution setup (peak count, turbostratic
/// shoulder, background) from the numeric features. Validated at ~0.94% DG MAE
/// vs the expert gold standard. The deterministic engine still computes DG%.
public enum AISuggester {
    public static let model = "claude-opus-4-8"

    public static let systemPrompt = """
You are an expert XRD analyst applying the NETL standard procedure to deconvolve the carbon (002) reflection of Fe-catalyzed petroleum-coke graphite, to SET UP a Degree of Graphitization calculation. You decide ONLY the deconvolution setup; you do NOT compute DG%.

CRITICAL DOMAIN FACT: these Fe-catalyzed samples almost always retain a SMALL but physically real TURBOSTRATIC fraction - a broad low-angle shoulder (2-theta ~26.0-26.45, below the sharp graphitic peak ~26.4-26.7). It is usually SUBTLE (a few percent of peak height), so a high single-peak R2 (>0.99) does NOT rule it out. Experts fit TWO peaks in the large majority of these samples.

Rules:
1. DEFAULT to peak_count=2. Place turbostratic_2theta at the low-angle shoulder, using low_angle_residual_2theta and automatic_two_peak_turbostratic_2theta as anchors (typically 26.0-26.4, below the graphitic peak).
2. peak_count=1 ONLY if truly symmetric: low_angle_residual_fraction < ~0.015 AND dR2 < ~0.0005. The exception, not the norm.
3. amorphous_invalid=true only if no resolvable (002) peak (very broad/weak, low SNR).
4. subtract_background only if an obvious sloped background; else false.
Give a brief rationale and a confidence in [0,1].
"""

    static var schema: [String: Any] { [
        "type": "object",
        "properties": [
            "peak_count": ["type": "integer", "enum": [1, 2]],
            "turbostratic_2theta": ["anyOf": [["type": "number"], ["type": "null"]]],
            "subtract_background": ["type": "boolean"],
            "amorphous_invalid": ["type": "boolean"],
            "confidence": ["type": "number"],
            "rationale": ["type": "string"],
        ],
        "required": ["peak_count", "turbostratic_2theta", "subtract_background",
                     "amorphous_invalid", "confidence", "rationale"],
        "additionalProperties": false,
    ] }

    public static func suggest(_ pattern: XRDPattern, apiKey: String)
        async throws -> (Suggestion, DeconvolutionFeatures) {
        if apiKey.isEmpty { throw AISuggesterError.noAPIKey }
        let features = try computeFeatures(pattern)
        let featJSON = String(decoding: try JSONEncoder().encode(features), as: UTF8.self)

        let body: [String: Any] = [
            "model": model,
            "max_tokens": 2000,
            "thinking": ["type": "adaptive"],
            "system": systemPrompt,
            "messages": [["role": "user",
                          "content": [["type": "text", "text": "Features (JSON):\n" + featJSON]]]],
            "output_config": ["format": ["type": "json_schema", "schema": schema]],
        ]

        var req = URLRequest(url: URL(string: "https://api.anthropic.com/v1/messages")!)
        req.httpMethod = "POST"
        req.timeoutInterval = 120
        req.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        req.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw AISuggesterError.badResponse("no HTTP response")
        }
        guard http.statusCode == 200 else {
            throw AISuggesterError.http(http.statusCode, String(decoding: data, as: UTF8.self))
        }
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let content = obj["content"] as? [[String: Any]],
              let text = content.first(where: { ($0["type"] as? String) == "text" })?["text"] as? String,
              let sdata = text.data(using: .utf8) else {
            throw AISuggesterError.badResponse("missing text content")
        }
        let suggestion = try JSONDecoder().decode(Suggestion.self, from: sdata)
        return (suggestion, features)
    }
}
