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
                .frame(minWidth: 940, minHeight: 580)
        }
        .defaultSize(width: 1040, height: 660)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open .xy…") { model.requestOpen() }
                    .keyboardShortcut("o", modifiers: .command)
            }
        }
    }
}
