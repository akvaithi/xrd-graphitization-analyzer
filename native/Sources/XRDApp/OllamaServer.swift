import Foundation
import Darwin
import XRDCore

/// Manages a private, bundled Ollama server: launches the runtime shipped inside
/// the .app on a free local port, so the AI assist works with zero user setup.
///
/// Models can come from two places:
///  • a read-only `ollama-models` folder bundled in the .app (the big build), or
///  • a writable Application-Support folder that the user fills via an in-app
///    download (the small build / fallback) — see `pull(model:)`.
///
/// Falls back to a system Ollama (manual host) when the runtime isn't bundled
/// (e.g. `swift run` in dev).
@MainActor
final class OllamaServer: ObservableObject {
    static let shared = OllamaServer()

    @Published var host: String?              // "http://127.0.0.1:PORT" once ready
    @Published var status: String = "idle"
    @Published var bundled = false            // runtime present inside the .app?
    @Published var modelPresent = false       // is the target model available?
    @Published var downloadProgress: Double?  // 0…1 while pulling, nil otherwise
    @Published var downloadStatus: String?    // human progress line while pulling

    let targetModel = AISuggester.defaultModel

    private var process: Process?

    /// Writable model store used when the .app doesn't bundle the model.
    private var writableModelsDir: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory,
                                            in: .userDomainMask).first!
        return base.appendingPathComponent("XRD Graphitization Analyzer/models", isDirectory: true)
    }

    func start() {
        guard process == nil, let res = Bundle.main.resourceURL else { return }
        let binary = res.appendingPathComponent("ollama-runtime/ollama")
        let bundledModels = res.appendingPathComponent("ollama-models", isDirectory: true)
        guard FileManager.default.fileExists(atPath: binary.path) else {
            status = "no bundled runtime — using system Ollama if available"
            bundled = false
            return
        }
        bundled = true

        // Prefer the bundled (read-only) models; otherwise a writable dir we can
        // download into.
        let modelsURL: URL
        if FileManager.default.fileExists(atPath: bundledModels.path) {
            modelsURL = bundledModels
        } else {
            modelsURL = writableModelsDir
            try? FileManager.default.createDirectory(at: modelsURL, withIntermediateDirectories: true)
        }

        let port = Self.freePort()
        let p = Process()
        p.executableURL = binary
        p.arguments = ["serve"]
        var env = ProcessInfo.processInfo.environment
        env["OLLAMA_HOST"] = "127.0.0.1:\(port)"
        env["OLLAMA_MODELS"] = modelsURL.path
        p.environment = env
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { status = "failed to launch: \(error)"; return }
        process = p
        status = "starting local model…"
        let h = "http://127.0.0.1:\(port)"
        Task { await self.waitReady(h) }
    }

    func stop() {
        process?.terminate()
        process = nil
        host = nil
    }

    private func waitReady(_ h: String) async {
        for _ in 0..<80 {
            if await reachable(h) {
                host = h
                await refreshModelPresence(host: h)
                status = modelPresent ? "ready" : "model not downloaded"
                return
            }
            try? await Task.sleep(nanoseconds: 700_000_000)
        }
        status = "local model did not start"
    }

    private func reachable(_ h: String) async -> Bool {
        guard let url = URL(string: h + "/api/tags") else { return false }
        var req = URLRequest(url: url); req.timeoutInterval = 2
        guard let (_, resp) = try? await URLSession.shared.data(for: req) else { return false }
        return (resp as? HTTPURLResponse)?.statusCode == 200
    }

    /// Check whether the target model is installed on the given host.
    func refreshModelPresence(host h: String) async {
        guard let url = URL(string: h + "/api/tags"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let models = obj["models"] as? [[String: Any]] else {
            modelPresent = false; return
        }
        let want = targetModel
        let wantBase = targetModel.split(separator: ":").first.map(String.init) ?? targetModel
        modelPresent = models.contains { m in
            guard let n = m["name"] as? String else { return false }
            return n == want || n == wantBase || n.hasPrefix(wantBase + ":")
        }
    }

    /// Stream `POST /api/pull` to download the model, updating `downloadProgress`.
    func pull(host h: String? = nil, model: String? = nil) async {
        let target = model ?? targetModel
        guard let base = h ?? host, let url = URL(string: base + "/api/pull") else {
            downloadStatus = "no host to download from"; return
        }
        downloadProgress = 0
        downloadStatus = "starting…"
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["model": target, "stream": true])
        do {
            let (bytes, resp) = try await URLSession.shared.bytes(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
                downloadStatus = "download failed (HTTP \((resp as? HTTPURLResponse)?.statusCode ?? -1))"
                downloadProgress = nil; return
            }
            for try await line in bytes.lines {
                guard let d = line.data(using: .utf8),
                      let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { continue }
                if let err = o["error"] as? String {
                    downloadStatus = "error: \(err)"; downloadProgress = nil; return
                }
                let st = (o["status"] as? String) ?? ""
                if let total = o["total"] as? Double, let done = o["completed"] as? Double, total > 0 {
                    downloadProgress = done / total
                    downloadStatus = String(format: "%@ — %.0f%%", st, done / total * 100)
                } else {
                    downloadStatus = st
                }
                if st == "success" { break }
            }
            await refreshModelPresence(host: base)
            downloadProgress = nil
            downloadStatus = modelPresent ? nil : "download finished but model not found"
            if modelPresent { status = "ready" }
        } catch {
            downloadStatus = "download error: \(error.localizedDescription)"
            downloadProgress = nil
        }
    }

    /// Ask the kernel for an unused TCP port (bind to :0, read it back).
    static func freePort() -> Int {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        if fd < 0 { return Int.random(in: 11500...11999) }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        addr.sin_port = 0
        let sz = socklen_t(MemoryLayout<sockaddr_in>.size)
        let ok = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, sz) }
        }
        if ok != 0 { return Int.random(in: 11500...11999) }
        var bound = sockaddr_in(); var blen = sz
        _ = withUnsafeMutablePointer(to: &bound) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &blen) }
        }
        return Int(UInt16(bigEndian: bound.sin_port))
    }
}
