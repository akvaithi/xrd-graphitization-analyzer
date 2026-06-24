import SwiftUI

/// App Settings (⌘,): AI engine selection (moved off the Analyze page), scan-QC
/// thresholds, and chart-export options. All values are `@AppStorage` so they're
/// read directly by `AIConfig`, `AppModel.redoRecommended`, and the export paths.
struct SettingsView: View {
    @EnvironmentObject private var server: OllamaServer

    @AppStorage("aiBackend") private var backendRaw = AIBackend.auto.rawValue
    @AppStorage("ollamaHost") private var ollamaHost = ""
    @AppStorage("redoMin") private var redoMin = 26.50
    @AppStorage("redoMax") private var redoMax = 26.60
    @AppStorage("pngTitle") private var pngTitle = true
    @AppStorage("pngComponents") private var pngComponents = true
    @AppStorage("pngAnnotation") private var pngAnnotation = true

    private var backend: AIBackend { AIBackend(rawValue: backendRaw) ?? .auto }
    private var activeFM: Bool {
        backend == .appleFM || (backend == .auto && FoundationModelsSuggester.isAvailable)
    }

    var body: some View {
        TabView {
            aiTab.tabItem { Label("AI", systemImage: "sparkles") }
            qcTab.tabItem { Label("Scan QC", systemImage: "checkmark.seal") }
            exportTab.tabItem { Label("Export", systemImage: "square.and.arrow.up") }
        }
        .frame(width: 460, height: 340)
    }

    // MARK: AI engine

    private var aiTab: some View {
        Form {
            Picker("Engine", selection: $backendRaw) {
                ForEach(AIBackend.allCases) { b in
                    Text(b == .appleFM && !FoundationModelsSuggester.isAvailable
                         ? b.rawValue + " — unavailable" : b.rawValue).tag(b.rawValue)
                }
            }
            if activeFM {
                LabeledContent("Status") {
                    Text(FoundationModelsSuggester.isAvailable
                         ? FoundationModelsSuggester.modelLabel
                         : "Apple on-device model \(FoundationModelsSuggester.statusText)")
                        .foregroundStyle(FoundationModelsSuggester.isAvailable ? .green : .orange)
                }
            } else {
                LabeledContent("Local model") {
                    Text(server.host == nil ? server.status
                         : (server.modelPresent ? "ready (gemma3:4b)" : "gemma3:4b not downloaded"))
                        .foregroundStyle(server.modelPresent ? .green : .secondary)
                }
                if !server.bundled {
                    TextField("Ollama host", text: $ollamaHost, prompt: Text("http://localhost:11434"))
                }
                if !server.modelPresent {
                    if let prog = server.downloadProgress {
                        ProgressView(value: prog) { Text(server.downloadStatus ?? "downloading…").font(.caption) }
                    } else {
                        Button("Download gemma3:4b (~3.3 GB)") {
                            Task { await server.pull(host: server.host ?? (ollamaHost.isEmpty ? nil : ollamaHost)) }
                        }
                    }
                }
            }
            Text("The AI suggests only the deconvolution setup — peak count, turbostratic position, background — which you confirm; DG% is always computed locally by the deterministic engine. Everything runs on-device (Apple's on-device model on macOS 27+, or a local Ollama gemma3:4b), so nothing leaves your machine.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .formStyle(.grouped).padding()
    }

    // MARK: Scan QC

    private var qcTab: some View {
        Form {
            Section("Re-scan recommendation") {
                Text("Recommend re-scanning when the fitted graphitic (002) 2θ falls outside this band.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Text("Min 2θ")
                    TextField("26.50", value: $redoMin, format: .number).frame(width: 80)
                    Text("°").foregroundStyle(.secondary)
                    Spacer()
                    Text("Max 2θ")
                    TextField("26.60", value: $redoMax, format: .number).frame(width: 80)
                    Text("°").foregroundStyle(.secondary)
                }
                Button("Reset to 26.50–26.60°") { redoMin = 26.50; redoMax = 26.60 }
                    .buttonStyle(.link).font(.caption)
            }
        }
        .formStyle(.grouped).padding()
    }

    // MARK: Chart export

    private var exportTab: some View {
        Form {
            Section("Exported PNG charts") {
                Toggle("Title + DG%/date header", isOn: $pngTitle)
                Toggle("Fitted peak components (graphitic + turbostratic)", isOn: $pngComponents)
                Toggle("DG% + parameters annotation box", isOn: $pngAnnotation)
            }
            Text("Applies to single-file and batch (Export all) PNG exports.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .formStyle(.grouped).padding()
    }
}
