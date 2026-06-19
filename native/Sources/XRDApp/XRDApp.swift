import SwiftUI
import AppKit

/// Forces a normal foreground app, and starts/stops the bundled Ollama server.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        MainActor.assumeIsolated { OllamaServer.shared.start() }
    }
    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { OllamaServer.shared.stop() }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
}

@main
struct XRDApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .environmentObject(OllamaServer.shared)
                .frame(minWidth: 1040, minHeight: 600)
        }
        .defaultSize(width: 1180, height: 720)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open .xy…") { model.requestOpen() }
                    .keyboardShortcut("o", modifiers: .command)
            }
            CommandGroup(replacing: .appInfo) {
                Button("About XRD Graphitization Analyzer") { showAboutPanel() }
            }
            CommandGroup(replacing: .help) { HelpMenu() }
        }

        // Dedicated Help window (opened from the Help menu).
        Window("XRD Graphitization Analyzer — Help", id: "help") {
            HelpView()
        }
        .defaultSize(width: 700, height: 800)
    }
}

/// Help menu: opens the in-app Help window + quick links.
private struct HelpMenu: View {
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Button("XRD Graphitization Analyzer Help") { openWindow(id: "help") }
            .keyboardShortcut("?", modifiers: .command)
        Divider()
        Button("Portfolio — akvaithi.page") {
            NSWorkspace.shared.open(URL(string: "https://akvaithi.page")!)
        }
        Button("Source on GitHub") {
            NSWorkspace.shared.open(URL(string: "https://github.com/akvaithi/xrd-graphitization-analyzer")!)
        }
    }
}

/// Custom About panel with author bio + clickable portfolio link.
@MainActor
private func showAboutPanel() {
    let credits = NSMutableAttributedString()
    let body = NSFont.systemFont(ofSize: 11)
    credits.append(NSAttributedString(
        string: "Degree-of-Graphitization analysis for carbon materials from XRD — the NETL PsdVoigt1 / Maire–Mering method.\n\nCreated by Arun Vaithianathan\nTexas A&M University · NETL / ARPA-E graphite-from-petroleum-coke project\n\n",
        attributes: [.font: body, .foregroundColor: NSColor.labelColor]))
    credits.append(NSAttributedString(
        string: "akvaithi.page",
        attributes: [.link: URL(string: "https://akvaithi.page")!, .font: body]))
    credits.append(NSAttributedString(string: "     ", attributes: [.font: body]))
    credits.append(NSAttributedString(
        string: "Source on GitHub",
        attributes: [.link: URL(string: "https://github.com/akvaithi/xrd-graphitization-analyzer")!, .font: body]))

    NSApp.orderFrontStandardAboutPanel(options: [
        .applicationName: "XRD Graphitization Analyzer",
        .applicationVersion: "1.3",
        .credits: credits,
        NSApplication.AboutPanelOptionKey(rawValue: "Copyright"):
            "Created by Arun Vaithianathan · akvaithi.page",
    ])
    NSApp.activate(ignoringOtherApps: true)
}
