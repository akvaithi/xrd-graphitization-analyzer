// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "XRDAnalyzer",
    platforms: [.macOS(.v14)],
    products: [
        // C ABI shared library consumed by the Windows (WinUI/C#) front-end.
        .library(name: "XRDBridge", type: .dynamic, targets: ["XRDBridge"]),
    ],
    targets: [
        // Pure-Swift analysis core (parsing, Pseudo-Voigt, LM fit, DG pipeline).
        .target(name: "XRDCore"),
        // Headless CLI used to validate the Swift numbers against the Python ref.
        .executableTarget(name: "xrd-validate", dependencies: ["XRDCore"]),
        // The native SwiftUI app (bundled into the .app by scripts/make-app.sh). macOS-only.
        .executableTarget(name: "XRDApp", dependencies: ["XRDCore"]),
        // JSON-in/JSON-out C ABI over XRDCore, consumed via P/Invoke by windows/XRDAnalyzer.
        .target(name: "XRDBridge", dependencies: ["XRDCore"]),
    ]
)
