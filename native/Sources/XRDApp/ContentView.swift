import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject var model: AppModel

    private var contentTypes: [UTType] {
        [UTType(filenameExtension: "xy") ?? .plainText, .plainText, .text,
         .commaSeparatedText, .data]
    }

    var body: some View {
        NavigationSplitView {
            List(model.files, selection: $model.selection) { file in
                VStack(alignment: .leading, spacing: 3) {
                    Text(file.displayName)
                        .font(.system(size: 12, weight: .medium))
                        .lineLimit(2)
                    Text(file.dgText)
                        .font(.system(size: 11))
                        .foregroundStyle(dgColor(file))
                }
                .padding(.vertical, 2)
                .tag(file.id)
            }
            .navigationTitle("Runs")
            .frame(minWidth: 240)
            .overlay {
                if model.files.isEmpty {
                    ContentUnavailableView(
                        "No runs loaded",
                        systemImage: "tray",
                        description: Text("Open .xy file(s) to analyze."))
                }
            }
        } detail: {
            if let file = model.selected() {
                DetailView(file: file)
            } else {
                ContentUnavailableView(
                    "Select a run",
                    systemImage: "chart.xyaxis.line",
                    description: Text("Choose a file on the left, or open more."))
            }
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    model.requestOpen()
                } label: {
                    Label("Open .xy…", systemImage: "plus")
                }
            }
        }
        .fileImporter(isPresented: $model.openRequested,
                      allowedContentTypes: contentTypes,
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { model.open(urls) }
        }
        .task { model.openLaunchArguments() }
    }

    private func dgColor(_ f: LoadedFile) -> Color {
        f.failed ? .red : .secondary
    }
}
