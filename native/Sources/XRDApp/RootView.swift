import SwiftUI
import UniformTypeIdentifiers

/// Top-level tabs, sharing one loaded-file set and a single Open action.
struct RootView: View {
    @EnvironmentObject var model: AppModel
    @State private var tab = 0

    private var contentTypes: [UTType] {
        [UTType(filenameExtension: "xy") ?? .plainText, .plainText, .text,
         .commaSeparatedText, .data]
    }

    var body: some View {
        TabView(selection: $tab) {
            AnalyzeView().tabItem { Label("Analyze", systemImage: "chart.xyaxis.line") }.tag(0)
            CompareView().tabItem { Label("Compare", systemImage: "chart.dots.scatter") }.tag(1)
            StackView().tabItem { Label("Stack spectra", systemImage: "square.stack.3d.up") }.tag(2)
            ManualView().tabItem { Label("Manual calc", systemImage: "function") }.tag(3)
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { model.requestOpen() } label: { Label("Open .xy…", systemImage: "plus") }
            }
        }
        .fileImporter(isPresented: $model.openRequested,
                      allowedContentTypes: contentTypes,
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { model.open(urls) }
        }
        .task { model.openLaunchArguments() }
    }
}
