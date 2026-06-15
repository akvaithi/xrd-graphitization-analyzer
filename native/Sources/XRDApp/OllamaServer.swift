import Foundation
import Darwin

/// Manages a private, bundled Ollama server: launches the runtime shipped inside
/// the .app pointed at the bundled gemma3:4b model on a free local port, so the
/// AI assist works with zero user setup. Falls back to a system Ollama (manual
/// host) when the app isn't bundled with the runtime (e.g. `swift run` in dev).
@MainActor
final class OllamaServer: ObservableObject {
    static let shared = OllamaServer()

    @Published var host: String?              // "http://127.0.0.1:PORT" once ready
    @Published var status: String = "idle"
    @Published var bundled = false            // runtime present inside the .app?

    private var process: Process?

    func start() {
        guard process == nil, let res = Bundle.main.resourceURL else { return }
        let runtime = res.appendingPathComponent("ollama-runtime", isDirectory: true)
        let binary = runtime.appendingPathComponent("ollama")
        let models = res.appendingPathComponent("ollama-models", isDirectory: true)
        guard FileManager.default.fileExists(atPath: binary.path) else {
            status = "no bundled model — using system Ollama if available"
            bundled = false
            return
        }
        bundled = true
        let port = Self.freePort()
        let p = Process()
        p.executableURL = binary
        p.arguments = ["serve"]
        var env = ProcessInfo.processInfo.environment
        env["OLLAMA_HOST"] = "127.0.0.1:\(port)"
        env["OLLAMA_MODELS"] = models.path
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
            if await reachable(h) { host = h; status = "ready"; return }
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
