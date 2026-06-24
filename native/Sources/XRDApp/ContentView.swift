import SwiftUI

/// Analyze tab — sidebar of loaded runs + the interactive per-file deconvolution.
struct AnalyzeView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationSplitView {
            List(model.files, selection: $model.selection) { file in
                row(file).tag(file.id)
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

    @ViewBuilder private func row(_ file: LoadedFile) -> some View {
        let redo = model.redoRecommended(file)
        let flagged = model.isFlaggedForRedo(file)
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 4) {
                Text(file.displayName)
                    .font(.system(size: 12, weight: .medium)).lineLimit(2)
                Spacer(minLength: 2)
                if flagged {
                    Image(systemName: "flag.fill").foregroundStyle(.orange).imageScale(.small)
                        .help("Flagged for re-scan")
                }
                if redo != nil {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.yellow).imageScale(.small)
                        .help(redo!)
                }
            }
            Text(model.dgText(for: file))
                .font(.system(size: 11))
                .foregroundStyle(model.failed(for: file) ? .red : .secondary)
        }
        .padding(.vertical, 2)
        .contextMenu {
            Button("Open File Location") { model.revealInFinder(file) }
            Button(flagged ? "Unflag for Re-scan" : "Flag for Re-scan") { model.toggleRedo(file) }
            Divider()
            Button("Remove from List", role: .destructive) { model.remove(file.id) }
        }
    }
}
