import SwiftUI
import AppKit
import XRDCore

extension String {
    /// A readable, filesystem-safe filename stem: keeps spaces and ` · `,
    /// transliterates science glyphs (CaCO₃→CaCO3, subscripts→digits, α→alpha,
    /// ° dropped), and replaces only the characters macOS forbids (`/`, `:`).
    var fileSafe: String {
        var s = self
        let map = ["°": "", "′": "'", "″": "''", "×": "x", "±": "",
                   "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
                   "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
                   "α": "alpha", "β": "beta", "/": "-", ":": "-"]
        for (k, v) in map { s = s.replacingOccurrences(of: k, with: v) }
        s = s.folding(options: .diacriticInsensitive, locale: .current)
        s = String(s.unicodeScalars.filter { $0.value >= 0x20 && $0.value != 0x7F })
        while s.contains("  ") { s = s.replacingOccurrences(of: "  ", with: " ") }
        let trimmed = s.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? "export" : trimmed
    }
}

/// WYSIWYG export content for a (002) fit: optional title/subtitle header + the
/// fit chart, with scalable text. The on-screen preview and the saved PNG render
/// this same view, so what you see is what you get.
struct ExportChart: View {
    let result: DGResult
    var title: String?
    var subtitle: String?
    var options: ChartOptions
    var width: CGFloat = 1000
    var height: CGFloat = 620

    var body: some View {
        VStack(alignment: .leading, spacing: 4 * options.textScale) {
            if let title, !title.isEmpty {
                Text(title).font(.system(size: 15 * options.textScale, weight: .semibold)).lineLimit(2)
            }
            if let subtitle, !subtitle.isEmpty {
                Text(subtitle).font(.system(size: 11 * options.textScale)).foregroundStyle(.secondary)
            }
            FitChartView(result: result, options: options).frame(width: width, height: height)
        }
        .padding(16)
        .background(Color(NSColor.windowBackgroundColor))
    }
}

@MainActor
func pngData<V: View>(_ view: V, scale: CGFloat = 2.0) -> Data? {
    let renderer = ImageRenderer(content: view)
    renderer.scale = scale
    guard let img = renderer.nsImage, let tiff = img.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff) else { return nil }
    return rep.representation(using: .png, properties: [:])
}

@MainActor
func savePNGPanel(_ data: Data?, suggestedName: String) {
    guard let data else { return }
    let panel = NSSavePanel()
    panel.nameFieldStringValue = suggestedName.fileSafe + ".png"
    panel.allowedContentTypes = [.png]
    guard panel.runModal() == NSApplication.ModalResponse.OK, let url = panel.url else { return }
    try? data.write(to: url)
}

// MARK: - Generic chart export (used by the Compare scatter)

@MainActor
func saveChartPNG<V: View>(_ view: V, suggestedName: String,
                           title: String? = nil, subtitle: String? = nil,
                           width: CGFloat = 1000, height: CGFloat = 620) {
    let framed = VStack(alignment: .leading, spacing: 4) {
        if let title { Text(title).font(.system(size: 15, weight: .semibold)).lineLimit(2) }
        if let subtitle { Text(subtitle).font(.system(size: 11)).foregroundStyle(.secondary) }
        view.frame(width: width, height: height)
    }
    .padding(16)
    .background(Color(NSColor.windowBackgroundColor))
    savePNGPanel(pngData(framed), suggestedName: suggestedName)
}

/// Headless fit-chart render straight to a file (batch export).
@MainActor
@discardableResult
func renderExportChart(_ chart: ExportChart, to url: URL) -> Bool {
    guard let data = pngData(chart) else { return false }
    do { try data.write(to: url); return true } catch { return false }
}
