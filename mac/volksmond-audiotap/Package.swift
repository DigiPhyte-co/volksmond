// swift-tools-version:5.9
//
// volksmond-audiotap: a small, signed, standalone system-audio capture helper for
// the macOS build of Volksmond. It captures the system audio mix via a Core Audio
// process tap (AudioHardwareCreateProcessTap family, macOS 14.2+, practical floor
// 14.4) and streams it to stdout under the frozen binary contract in CONTRACT.md.
//
// Design intent: audio-only TCC permission (NSAudioCaptureUsageDescription), no
// Screen Recording permission and no ScreenCaptureKit. The helper ships inside
// Volksmond.app at Contents/Resources/bin/ and is signed as part of the bundle so
// TCC attribution rolls up to the parent app.
//
// No third-party dependencies: system frameworks only.

import PackageDescription

let package = Package(
    name: "volksmond-audiotap",
    platforms: [
        // Practical floor for Core Audio process taps with audio-only TCC. The
        // string form pins the minor version (14.4), which .v14 (14.0) cannot.
        .macOS("14.4"),
    ],
    targets: [
        .executableTarget(
            name: "volksmond-audiotap",
            path: "Sources/volksmond-audiotap",
            linkerSettings: [
                // System frameworks are implicitly linked on import, but declare
                // them so a clean-room build environment cannot get it wrong.
                .linkedFramework("CoreAudio"),
                .linkedFramework("AudioToolbox"),
                .linkedFramework("AVFoundation"),
                .linkedFramework("CoreMedia"),
            ]
        ),
    ]
)
