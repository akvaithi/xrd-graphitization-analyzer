import SwiftUI
import XRDCore

/// Live preview + controls for a fit-chart PNG export. Shows exactly what will be
/// saved (header, components, parameters box, text size) and lets the user tweak
/// it before choosing a destination. Seeds from the persisted Settings defaults
/// and writes them back so the choices stick.
struct ExportPreviewView: View {
    let result: DGResult
    let displayName: String

    @Environment(\.dismiss) private var dismiss
    @AppStorage("pngTitle") private var pngTitle = true
    @AppStorage("pngComponents") private var pngComponents = true
    @AppStorage("pngAnnotation") private var pngAnnotation = true
    @AppStorage("pngTextScale") private var pngTextScale = 1.0

    @State private var includeTitle = true
    @State private var includeSubtitle = true
    @State private var options = ChartOptions()

    private var subtitleText: String {
        String(format: "DG %.2f%% · (002) fit · %@", result.dgPercent,
                Date().formatted(date: .abbreviated, time: .omitted))
    }

    var body: some View {
        HSplitView {
            preview.frame(minWidth: 440)
            controls.frame(width: 280)
        }
        .frame(width: 820, height: 560)
        .onAppear {
            options.showComponents = pngComponents
            options.showAnnotation = pngAnnotation
            options.textScale = pngTextScale
            includeTitle = pngTitle
        }
    }

    private var preview: some View {
        VStack(spacing: 0) {
            ScrollView([.horizontal, .vertical]) {
                ExportChart(result: result,
                            title: includeTitle ? displayName : nil,
                            subtitle: includeSubtitle ? subtitleText : nil,
                            options: options, width: 1000, height: 600)
                    .frame(width: 1000, height: 700, alignment: .topLeading)
                    .scaleEffect(0.46, anchor: .topLeading)
                    .frame(width: 1000 * 0.46, height: 700 * 0.46)
            }
            .background(Color(NSColor.windowBackgroundColor))
            Divider()
            HStack {
                Text("Preview").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save PNG…") { save() }.keyboardShortcut(.defaultAction)
            }
            .padding(10)
        }
    }

    private var controls: some View {
        Form {
            Section("Header") {
                Toggle("Title (file name)", isOn: $includeTitle)
                Toggle("DG% / date subtitle", isOn: $includeSubtitle)
            }
            Section("On chart") {
                Toggle("Fitted peak components", isOn: $options.showComponents)
                Toggle("Parameters box", isOn: $options.showAnnotation)
            }
            if options.showAnnotation {
                Section("Parameters box") {
                    ForEach(AnnotationField.allCases) { f in
                        Toggle(f.rawValue, isOn: field(f))
                            .disabled(f == .turbo && result.turbostratic == nil)
                    }
                }
            }
            Section("Text size") {
                Slider(value: $options.textScale, in: 0.7...1.8, step: 0.1)
                Text("\(Int(options.textScale * 100))%").font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }

    private func field(_ f: AnnotationField) -> Binding<Bool> {
        Binding(get: { options.fields.contains(f) },
                set: { on in if on { options.fields.insert(f) } else { options.fields.remove(f) } })
    }

    private func save() {
        let chart = ExportChart(result: result,
                                title: includeTitle ? displayName : nil,
                                subtitle: includeSubtitle ? subtitleText : nil,
                                options: options, width: 1000, height: 620)
        savePNGPanel(pngData(chart), suggestedName: displayName + " — 002 fit")
        // Remember the choices for next time / Settings.
        pngTitle = includeTitle; pngComponents = options.showComponents
        pngAnnotation = options.showAnnotation; pngTextScale = options.textScale
        dismiss()
    }
}
