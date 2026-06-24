import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// Top-level tabs, sharing one loaded-file set and a single Open action.
struct RootView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var server: OllamaServer
    @State private var tab = 0

    @AppStorage("pngTitle") private var pngTitle = true
    @AppStorage("pngComponents") private var pngComponents = true
    @AppStorage("pngAnnotation") private var pngAnnotation = true

    // Only .xy scans — so the sidecar `.xy.xrda.json` files (and other junk) can't
    // be imported. `AppModel.open` also hard-filters by extension as a backstop.
    private var contentTypes: [UTType] {
        [UTType(filenameExtension: "xy") ?? .data]
    }

    var body: some View {
        TabView(selection: $tab) {
            AnalyzeView().tabItem { Label("Analyze", systemImage: "chart.xyaxis.line") }.tag(0)
            CompareView().tabItem { Label("Compare", systemImage: "chart.dots.scatter") }.tag(1)
            StackView().tabItem { Label("Stack spectra", systemImage: "square.stack.3d.up") }.tag(2)
            ManualView().tabItem { Label("Manual calc", systemImage: "function") }.tag(3)
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { Task { await model.suggestAllAI(.current(server: server)) } } label: {
                    Label("AI Suggest All", systemImage: "wand.and.stars.inverse")
                }
                .disabled(model.batchBusy || model.files.isEmpty
                          || !AIConfig.current(server: server).canSuggest)
                Button { runExportAll() } label: { Label("Export All…", systemImage: "square.and.arrow.up.on.square") }
                    .disabled(model.batchBusy || model.files.isEmpty)
                Button { model.requestOpen() } label: { Label("Open .xy…", systemImage: "plus") }
            }
        }
        .overlay(alignment: .bottom) { batchBanner }
        .fileImporter(isPresented: $model.openRequested,
                      allowedContentTypes: contentTypes,
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { model.open(urls) }
        }
        .onChange(of: model.requestExportAll) {
            if model.requestExportAll { model.requestExportAll = false; runExportAll() }
        }
        .task { model.openLaunchArguments() }
    }

    @ViewBuilder private var batchBanner: some View {
        if model.batchBusy || model.batchNote != nil {
            HStack(spacing: 10) {
                if model.batchBusy {
                    ProgressView(value: model.batchProgress).frame(width: 120)
                }
                Text(model.batchNote ?? "Working…").font(.caption)
                if !model.batchBusy {
                    Button { model.batchNote = nil } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(.regularMaterial, in: Capsule())
            .overlay(Capsule().strokeBorder(.quaternary))
            .padding(.bottom, 12)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    private func runExportAll() {
        guard !model.batchBusy, !model.files.isEmpty else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Export Here"
        panel.message = "Choose a folder for the per-file reports, fit charts, and consolidated CSV."
        guard panel.runModal() == .OK, let folder = panel.url else { return }
        let opts = ChartOptions(showComponents: pngComponents, showAnnotation: pngAnnotation)
        Task { await model.exportAll(to: folder, options: opts, includeTitle: pngTitle) }
    }
}
