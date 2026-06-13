import SwiftUI
import AppKit

/// Forces a normal foreground app with an active window even when the process is
/// launched from a non-GUI context (and guarantees it comes to the front).
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
}

@main
struct XRDApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
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
