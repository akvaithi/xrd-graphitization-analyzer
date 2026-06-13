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
        case .noAPIKey: return "No Claude API key (set ANTHROPIC_API_KEY or paste one)."
        case .http(let c, let b): return "Provider error \(c): \(b.prefix(300))"
        case .badResponse(let m): return "Unexpected provider response: \(m)"
        }
    }
}

/// Asks an LLM — Anthropic **Claude** (cloud) or a local **Ollama** model — to
/// choose the deconvolution setup from the numeric features. The deterministic
/// engine still computes DG%. Validated ~0.94% (Claude) / ~0.99% (Ollama) MAE.
public enum AISuggester {
    public enum Provider: String, Sendable, CaseIterable { case claude, ollama }
    public static let defaultClaudeModel = "claude-opus-4-8"
    public static let defaultOllamaModel = "gemma3:4b"

    public static let systemPrompt = """
You are an expert XRD analyst applying the NETL standard procedure to deconvolve the carbon (002) reflection of Fe-catalyzed petroleum-coke graphite, to SET UP a Degree of Graphitization calculation. You decide ONLY the deconvolution setup; you do NOT compute DG%.

CRITICAL DOMAIN FACT: these Fe-catalyzed samples almost always retain a SMALL but physically real TURBOSTRATIC fraction - a broad low-angle shoulder (2-theta ~26.0-26.45, below the sharp graphitic peak ~26.4-26.7). It is usually SUBTLE (a few percent of peak height), so a high single-peak R2 (>0.99) does NOT rule it out. Experts fit TWO peaks in the large majority of these samples.

Rules:
1. DEFAULT to peak_count=2. Place turbostratic_2theta at the low-angle shoulder, using low_angle_residual_2theta and automatic_two_peak_turbostratic_2theta as anchors (typically 26.0-26.4, below the graphitic peak).
2. peak_count=1 ONLY if truly symmetric: low_angle_residual_fraction < ~0.015 AND dR2 < ~0.0005. The exception, not the norm. (When peak_count=1, still output a plausible turbostratic_2theta; it is ignored.)
3. amorphous_invalid=true only if no resolvable (002) peak (very broad/weak, low SNR).
4. subtract_background only if an obvious sloped background; else false.
Respond with ONLY a JSON object with keys: peak_count (1 or 2), turbostratic_2theta (number), subtract_background (bool), amorphous_invalid (bool), confidence (0-1), rationale (short string).
"""

    static var schema: [String: Any] { [
        "type": "object",
        "properties": [
            "peak_count": ["type": "integer", "enum": [1, 2]],
            "turbostratic_2theta": ["type": "number"],
            "subtract_background": ["type": "boolean"],
            "amorphous_invalid": ["type": "boolean"],
            "confidence": ["type": "number"],
            "rationale": ["type": "string"],
        ],
        "required": ["peak_count", "turbostratic_2theta", "subtract_background",
                     "amorphous_invalid", "confidence", "rationale"],
        "additionalProperties": false,
    ] }

    public static func suggest(_ pattern: XRDPattern, provider: Provider = .claude,
                               model: String? = nil, apiKey: String = "",
                               ollamaHost: String = "http://localhost:11434")
        async throws -> (Suggestion, DeconvolutionFeatures) {
        let features = try computeFeatures(pattern)
        let featJSON = String(decoding: try JSONEncoder().encode(features), as: UTF8.self)
        let userText = "Features (JSON):\n" + featJSON
        let decisionJSON: Data
        switch provider {
        case .claude:
            decisionJSON = try await callClaude(userText, model: model ?? defaultClaudeModel, apiKey: apiKey)
        case .ollama:
            decisionJSON = try await callOllama(userText, model: model ?? defaultOllamaModel, host: ollamaHost)
        }
        return (try JSONDecoder().decode(Suggestion.self, from: decisionJSON), features)
    }

    // MARK: providers

    private static func callClaude(_ userText: String, model: String, apiKey: String) async throws -> Data {
        if apiKey.isEmpty { throw AISuggesterError.noAPIKey }
        let body: [String: Any] = [
            "model": model, "max_tokens": 2000,
            "thinking": ["type": "adaptive"],
            "system": systemPrompt,
            "messages": [["role": "user", "content": [["type": "text", "text": userText]]]],
            "output_config": ["format": ["type": "json_schema", "schema": schema]],
        ]
        var req = URLRequest(url: URL(string: "https://api.anthropic.com/v1/messages")!)
        req.httpMethod = "POST"; req.timeoutInterval = 120
        req.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        req.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkHTTP(resp, data)
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let content = obj["content"] as? [[String: Any]],
              let text = content.first(where: { ($0["type"] as? String) == "text" })?["text"] as? String,
              let out = text.data(using: .utf8) else {
            throw AISuggesterError.badResponse("missing text content")
        }
        return out
    }

    private static func callOllama(_ userText: String, model: String, host: String) async throws -> Data {
        let body: [String: Any] = [
            "model": model, "stream": false, "format": schema,
            "options": ["temperature": 0],
            "messages": [
                ["role": "system", "content": systemPrompt],
                ["role": "user", "content": userText],
            ],
        ]
        let base = host.hasSuffix("/") ? String(host.dropLast()) : host
        var req = URLRequest(url: URL(string: base + "/api/chat")!)
        req.httpMethod = "POST"; req.timeoutInterval = 300
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkHTTP(resp, data)
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let msg = obj["message"] as? [String: Any],
              let text = msg["content"] as? String,
              let out = text.data(using: .utf8) else {
            throw AISuggesterError.badResponse("missing message content")
        }
        return out
    }

    private static func checkHTTP(_ resp: URLResponse, _ data: Data) throws {
        guard let http = resp as? HTTPURLResponse else { throw AISuggesterError.badResponse("no HTTP response") }
        guard http.statusCode == 200 else {
            throw AISuggesterError.http(http.statusCode, String(decoding: data, as: UTF8.self))
        }
    }
}
