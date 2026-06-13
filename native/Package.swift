// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "XRDAnalyzer",
    platforms: [.macOS(.v14)],
    targets: [
        // Pure-Swift analysis core (parsing, Pseudo-Voigt, LM fit, DG pipeline).
        .target(name: "XRDCore"),
        // Headless CLI used to validate the Swift numbers against the Python ref.
        .executableTarget(name: "xrd-validate", dependencies: ["XRDCore"]),
        // The native SwiftUI app (bundled into the .app by scripts/make-app.sh).
        .executableTarget(name: "XRDApp", dependencies: ["XRDCore"]),
    ]
)
