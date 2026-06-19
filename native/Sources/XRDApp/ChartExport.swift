import SwiftUI
import AppKit

/// Render any SwiftUI view (a chart) to a PNG and save it via NSSavePanel.
@MainActor
func saveChartPNG<V: View>(_ view: V, suggestedName: String,
                           width: CGFloat = 1000, height: CGFloat = 620) {
    let framed = view
        .frame(width: width, height: height)
        .padding(16)
        .background(Color(NSColor.windowBackgroundColor))
    let renderer = ImageRenderer(content: framed)
    renderer.scale = 2.0
    guard let img = renderer.nsImage,
          let tiff = img.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { return }
    let panel = NSSavePanel()
    panel.nameFieldStringValue = suggestedName.replacingOccurrences(of: " ", with: "_") + ".png"
    panel.allowedContentTypes = [.png]
    guard panel.runModal() == NSApplication.ModalResponse.OK, let url = panel.url else { return }
    try? png.write(to: url)
}
