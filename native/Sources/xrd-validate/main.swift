import Foundation
import XRDCore

// Headless validator: `xrd-validate path/to/file.xy` -> one-line JSON of the
// key fitted quantities, so we can diff the Swift core against the Python ref.
let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write(Data("usage: xrd-validate <file.xy> | --name <filename>\n".utf8))
    exit(2)
}

if args[1] == "--name", args.count >= 3 {
    print(RunParser.parse(fileName: args[2]).displayName)
    exit(0)
}

// --ai <file.xy> : full AI-suggested deconvolution → deterministic fit → DG.
if args[1] == "--ai", args.count >= 3 {
    let env = ProcessInfo.processInfo.environment
    let host = env["OLLAMA_HOST"] ?? "http://localhost:11434"
    let model = env["AI_MODEL"]
    do {
        let pattern = try XRDPattern.parse(contentsOf: URL(fileURLWithPath: args[2]))
        let (s, _) = try await AISuggester.suggest(pattern, model: model, ollamaHost: host)
        var o = FitOptions()
        o.peakCount = s.peakCount
        o.subtractBackground = s.subtractBackground
        if let t = s.turbostraticCenter { o.lockTurbostratic = true; o.turbostraticCenter = t }
        let dg: Double? = s.amorphousInvalid ? nil : (try? GraphitizationAnalyzer(pattern).run(o).dgPercent)
        let out: [String: Any] = [
            "DG_percent": dg ?? -1,
            "peak_count": s.peakCount,
            "turbostratic_2theta": s.turbostraticCenter ?? 0,
            "amorphous_invalid": s.amorphousInvalid,
            "confidence": s.confidence,
        ]
        print(String(decoding: try JSONSerialization.data(withJSONObject: out, options: [.sortedKeys]), as: UTF8.self))
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("ai-error: \(error)\n".utf8))
        exit(1)
    }
}

// Optional flags: --turbo <xc> (lock turbostratic), --peaks <1|2>
var opt = FitOptions()
var path: String? = nil
var calibPhase: String? = nil
var i = 1
while i < args.count {
    switch args[i] {
    case "--turbo": opt.lockTurbostratic = true; opt.turbostraticCenter = Double(args[i + 1]); i += 2
    case "--peaks": opt.peakCount = Int(args[i + 1]) ?? 2; i += 2
    case "--anchor": opt.anchor002 = Double(args[i + 1]); i += 2
    case "--calib": calibPhase = args[i + 1]; i += 2
    default: path = args[i]; i += 1
    }
}

do {
    let pattern = try XRDPattern.parse(contentsOf: URL(fileURLWithPath: path ?? args[1]))
    if let ph = calibPhase {
        let c = InternalStandard.calibrate(pattern, phase: ph)
        print("{\"phase\":\"\(c.phase ?? "none")\",\"offset\":\(c.offset),\"spread\":\(c.spread ?? -1),\"n_lines\":\(c.nLines),\"reliable\":\(c.reliable),\"significant\":\(c.significant)}")
        if c.significant { opt.twoThetaOffset = -c.offset }
    }
    let r = try GraphitizationAnalyzer(pattern).run(opt)
    var dict: [String: Double] = [
        "DG_percent": r.dgPercent,
        "y0": r.y0,
        "graphitic_xc": r.graphitic.xc,
        "graphitic_w": r.graphitic.w,
        "graphitic_mu": r.graphitic.mu,
        "graphitic_A": r.graphitic.A,
        "d_prime": r.dPrimeWeighted,
        "Lc": r.crystalliteLc,
        "r2": r.fitR2,
    ]
    if let t = r.turbostratic {
        dict["turbostratic_xc"] = t.xc
        dict["turbostratic_w"] = t.w
        dict["turbostratic_A"] = t.A
    }
    let data = try JSONSerialization.data(withJSONObject: dict, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n".utf8))
    exit(1)
}
