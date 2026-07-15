import Foundation

// Audio-capture TCC permission handling, modelled on insidegui/AudioCap (the
// canonical reference for correct process-tap permission attribution). The public
// SDK has no high-level "request audio capture access" call analogous to
// AVCaptureDevice.requestAccess, so, like AudioCap, we resolve the private TCC
// preflight/request symbols at runtime via dlsym. If they cannot be resolved on a
// future OS we fall back to proceeding and letting tap creation be the gate.
//
// TODO(mac-hw): R1. Confirm on hardware that for a helper signed as part of
// Volksmond.app and launched from Contents/Resources/bin/, the TCC prompt names
// the PARENT app (Volksmond), not the helper, and that the grant persists across
// relaunch. This is the single riskiest assumption in the whole macOS port.
// TODO(mac-hw): R2. On Sequoia, soak-test that no periodic (roughly monthly)
// re-approval nag reappears for audio-capture after the first grant.
// TODO(mac-hw): Confirm the private TCC symbols (TCCAccessPreflight /
// TCCAccessRequest) still resolve and behave as documented on every target OS in
// the support window (14.4 through the current Sequoia point release).

enum AudioCapturePermission {
    // TCC service identifier for microphone-and-system audio capture.
    private static let service = "kTCCServiceAudioCapture" as CFString

    // int TCCAccessPreflight(CFStringRef service, CFDictionaryRef options)
    //   returns 0 granted, 1 denied, 2 undetermined.
    private typealias PreflightFn = @convention(c) (CFString, CFDictionary?) -> Int32
    // void TCCAccessRequest(CFStringRef service, CFDictionaryRef options,
    //                       void(^reply)(Bool granted))
    private typealias RequestFn = @convention(c) (CFString, CFDictionary?, @escaping @convention(block) (Bool) -> Void) -> Void

    enum Status {
        case authorized
        case denied
        case unknown // symbols unavailable; caller should proceed and let the tap gate
    }

    // Resolve, then preflight, and request if undetermined. Blocks until the async
    // request completes so the caller has a definite answer before creating a tap.
    static func ensure() -> Status {
        guard let handle = dlopen("/System/Library/PrivateFrameworks/TCC.framework/TCC", RTLD_NOW) else {
            logStderr("permission: could not open TCC private framework; proceeding without preflight")
            return .unknown
        }
        // The handle intentionally stays open for the process lifetime.

        guard let preflightSym = dlsym(handle, "TCCAccessPreflight") else {
            logStderr("permission: TCCAccessPreflight unavailable; proceeding without preflight")
            return .unknown
        }
        let preflight = unsafeBitCast(preflightSym, to: PreflightFn.self)

        let pre = preflight(service, nil)
        if pre == 0 {
            logStderr("permission: audio-capture already authorized")
            return .authorized
        }
        if pre == 1 {
            logStderr("permission: audio-capture denied")
            return .denied
        }

        // Undetermined (2): request interactively. This is what triggers the OS
        // prompt the first time; the grant is then remembered by TCC.
        guard let requestSym = dlsym(handle, "TCCAccessRequest") else {
            logStderr("permission: TCCAccessRequest unavailable; proceeding without request")
            return .unknown
        }
        let request = unsafeBitCast(requestSym, to: RequestFn.self)

        logStderr("permission: requesting audio-capture access")
        // Announce, on stdout, that we are about to block on the interactive TCC
        // dialog. This is the ONLY path that blocks on a human, so it is the only
        // place waiting_permission is emitted. It follows the format line and precedes
        // started, so it is a valid pre-started control event (CONTRACT.md 2.1). The
        // consumer uses it to extend its handshake deadline; a consumer that does not
        // recognise it ignores it (unknown control events are tolerated).
        StdoutWriter.shared.writeControlLine(Control.waitingPermission)
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        request(service, nil) { ok in
            granted = ok
            semaphore.signal()
        }
        semaphore.wait()

        let status: Status = granted ? .authorized : .denied
        logStderr("permission: request result \(granted ? "granted" : "denied")")
        return status
    }
}
