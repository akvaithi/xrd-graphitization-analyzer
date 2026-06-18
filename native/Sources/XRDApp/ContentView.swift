import SwiftUI

/// Analyze tab — sidebar of loaded runs + the interactive per-file deconvolution.
struct AnalyzeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationSplitView {
            List(model.files, selection: $model.selection) { file in
                VStack(alignment: .leading, spacing: 3) {
                    Text(file.displayName)
                        .font(.system(size: 12, weight: .medium))
                        .lineLimit(2)
                    Text(file.dgText)
                        .font(.system(size: 11))
                        .foregroundStyle(file.failed ? .red : .secondary)
                }
                .padding(.vertical, 2)
                .tag(file.id)
            }
            .navigationTitle("Runs")
            .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 300)
            .overlay {
                if model.files.isEmpty {
                    ContentUnavailableView("No runs loaded", systemImage: "tray",
                        description: Text("Open .xy file(s) to analyze."))
                }
            }
        } detail: {
            if let file = model.selected() {
                DetailView(file: file)
            } else {
                ContentUnavailableView("Select a run", systemImage: "chart.xyaxis.line",
                    description: Text("Choose a file on the left, or open more."))
            }
        }
    }
}
